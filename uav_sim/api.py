import csv
import io
import json
import math
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, model_validator

from .config import DEFAULT_WORKBOOK, ConfigurationError, load_experiment_data
from .database import EventStore
from .simulator import Simulator


class SimulationRequest(BaseModel):
    trialId: int
    action: Literal["release", "delay", "reroute"]


class PracticeSimulationRequest(BaseModel):
    action: Literal["release", "delay", "reroute"]


class FormalSimulationRequest(BaseModel):
    action: Literal["release", "delay", "reroute"]


class EventRequest(BaseModel):
    eventType: str = Field(min_length=1, max_length=80)
    sessionId: Optional[str] = None
    participantId: Optional[str] = None
    trialId: Optional[int] = None
    eventValue: Optional[Dict[str, Any]] = None
    clientTimeMs: Optional[float] = None


class ParticipantRequest(BaseModel):
    participantId: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")


class TrialResponseRequest(BaseModel):
    trialId: int
    defaultPrediction: str = Field(min_length=1, max_length=80)
    predictionRtMs: float = Field(ge=0)
    intermediateAnswer: str = Field(min_length=1, max_length=120)
    intermediateRtMs: float = Field(ge=0)
    finalAction: Literal["release", "delay", "reroute"]
    decisionRtMs: float = Field(ge=0)
    perceivedUncertainty: int = Field(ge=1, le=7)
    perceivedIntervenability: int = Field(ge=1, le=7)
    flightTimeConfidence: int = Field(ge=1, le=7)
    actionChoiceConfidence: int = Field(ge=1, le=7)
    ratingRtMs: float = Field(ge=0)
    layerDwellMs: List[float] = Field(min_length=3, max_length=3)
    layerSwitchCount: int = Field(ge=0)
    layerSequence: List[int] = Field(min_length=1)
    firstFutureLayerOpenedMs: Optional[float] = Field(default=None, ge=0)
    predictionChangeCount: int = Field(ge=0)
    intermediateChangeCount: int = Field(ge=0)
    decisionChangeCount: int = Field(ge=0)
    trialStartedAt: str = Field(min_length=1, max_length=80)
    timedOut: bool = False


class BlockRatingRequest(BaseModel):
    mentalEffort: int = Field(ge=1, le=7)
    fatigue: int = Field(ge=1, le=7)
    taskDifficulty: int = Field(ge=1, le=7)
    mentalSimulationStrategy: int = Field(ge=1, le=7)
    multiActionComparisonStrategy: int = Field(ge=1, le=7)
    simpleCueStrategy: int = Field(ge=1, le=7)


class ComprehensionRequest(BaseModel):
    answers: Dict[str, str]


class BackgroundQuestionnaire(BaseModel):
    major: Literal[
        "工程与技术", "计算机与信息科学", "管理与经济", "心理与认知科学",
        "自然科学", "人文与社会科学", "其他",
    ]
    uavExperience: Literal["none", "some", "extensive"]
    aviationCourse: Literal["none", "some", "extensive"]
    systemManagementExperience: Literal["none", "some", "extensive"]
    gameExperience: Literal["none", "some", "extensive"]
    mapAbility: int = Field(ge=1, le=7)
    sustainedAttention: int = Field(ge=1, le=7)
    overallEffort: int = Field(ge=1, le=7)
    scenarioComprehension: int = Field(ge=1, le=7)
    windRouteReliance: int = Field(ge=1, le=7)
    windRelationImportance: int = Field(ge=1, le=7)
    mentalSimulationUse: int = Field(ge=1, le=7)
    perceivedRealism: int = Field(ge=1, le=7)
    uncertaintyAnalysisEffort: int = Field(ge=1, le=7)
    intervenabilityComparisonEffort: int = Field(ge=1, le=7)
    simulationTriggerSelections: List[Literal[
        "未来风场的变化范围较大时", "不同调度方案可能造成较大的飞行时间差异时",
        "路线、风区和时间层之间的关系较复杂时", "第一眼看不出哪个方案更合适时",
        "我对初步判断的把握较低时", "只要时间允许，我通常都会仔细推演或比较方案",
        "没有固定规律", "其他，请说明",
    ]] = Field(min_length=1, max_length=3)
    simulationTriggerOther: str = Field(default="", max_length=500)
    decisionInformationSelections: List[Literal[
        "风速大小", "风向与飞行路线的相对关系", "风场变化范围",
        "T0、T1、T2时间层变化", "无风benchmark", "无人机进入和离开W1、W2的时间",
        "Path-A与Path-B的路线差异", "比较多个调度方案的可能结果",
        "主要依据一个关键线索快速判断", "主要依靠整体直觉", "其他，请说明",
    ]] = Field(min_length=1, max_length=4)
    decisionInformationOther: str = Field(default="", max_length=500)
    additionalInformation: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def validate_other_descriptions(self):
        if "其他，请说明" in self.simulationTriggerSelections and not self.simulationTriggerOther.strip():
            raise ValueError("第一道多选题选择其他后必须说明")
        if "其他，请说明" in self.decisionInformationSelections and not self.decisionInformationOther.strip():
            raise ValueError("第二道多选题选择其他后必须说明")
        return self


class CompletionRequest(BaseModel):
    questionnaire: BackgroundQuestionnaire


class PracticeResponseRequest(BaseModel):
    defaultPrediction: str = Field(min_length=1, max_length=80)
    finalAction: Literal["release", "delay", "reroute"]


def _segment_label(start, end) -> str:
    return f"{start.waypoint_id}–{end.waypoint_id}"


def _balance_option_position(options: List[str], answer: str, trial_id: int) -> List[str]:
    """Balance correct-option position while keeping one fixed order per trial."""
    ordered = list(options)
    desired_index = (trial_id - 1) % len(ordered)
    current_index = ordered.index(answer)
    shift = (desired_index - current_index) % len(ordered)
    return ordered[-shift:] + ordered[:-shift] if shift else ordered


NOT_IN_WIND_ZONE = "不在任何风区"
ARRIVED_AT_GOAL = "已到达终点"
DEFAULT_PREDICTION_OPTIONS = (
    "更短",
    "更长",
    "基本不变",
)
PRACTICE_PREDICTION_OPTIONS = DEFAULT_PREDICTION_OPTIONS[:2]


def _action_baseline(data, trial, action: str) -> float:
    path_id = trial.reroute_path if action == "reroute" else trial.release_path
    route = data.routes[trial.route_set_id][path_id]
    route_length = sum(
        math.hypot(end.x-start.x, end.y-start.y)
        for start, end in zip(route.waypoints, route.waypoints[1:])
    )
    return route_length / float(data.parameters["vAir_mps"])


def _prediction_category(forecast_time: float, baseline: float) -> Optional[str]:
    difference = (forecast_time - baseline) / baseline
    if difference < -0.05:
        return DEFAULT_PREDICTION_OPTIONS[0]
    if difference > 0.05:
        return DEFAULT_PREDICTION_OPTIONS[1]
    return DEFAULT_PREDICTION_OPTIONS[2]


def _prediction_assignments(data):
    """Assign only clearly shorter/longer comparisons across frozen trials."""
    categories = {}
    for candidate in data.trials.values():
        truth = data.ground_truth[candidate.trial_id]
        categories[candidate.trial_id] = {
            action: category
            for action in ("release", "delay", "reroute")
            if (category := _prediction_category(
                truth.time_for(action), _action_baseline(data, candidate, action),
            )) is not None
        }
    trial_ids = sorted(categories, key=lambda trial_id: (len(set(categories[trial_id].values())), trial_id))
    capacities = {
        option: sum(option in action_categories.values() for action_categories in categories.values())
        for option in DEFAULT_PREDICTION_OPTIONS
    }
    targets = {option: len(trial_ids) // len(DEFAULT_PREDICTION_OPTIONS)
               for option in DEFAULT_PREDICTION_OPTIONS}
    for option in DEFAULT_PREDICTION_OPTIONS:
        targets[option] = min(targets[option], capacities[option])
    while sum(targets.values()) < len(trial_ids):
        option = max(
            DEFAULT_PREDICTION_OPTIONS,
            key=lambda item: (capacities[item] - targets[item], -targets[item]),
        )
        if targets[option] >= capacities[option]:
            raise RuntimeError("默认状态预测答案无法完成全局平衡")
        targets[option] += 1

    remaining = dict(targets)
    assigned = {}

    def assign(index: int) -> bool:
        if index == len(trial_ids):
            return True
        trial_id = trial_ids[index]
        available = set(categories[trial_id].values())
        if not available:
            return False
        for option in sorted(available, key=lambda item: (-remaining[item], DEFAULT_PREDICTION_OPTIONS.index(item))):
            if remaining[option] <= 0:
                continue
            remaining[option] -= 1
            assigned[trial_id] = option
            if assign(index + 1):
                return True
            remaining[option] += 1
            assigned.pop(trial_id, None)
        return False

    if not assign(0):
        raise RuntimeError("默认状态预测答案无法完成全局平衡")
    return assigned, categories


def _prediction_reference(data, trial):
    action = "release"
    forecast_time = data.ground_truth[trial.trial_id].time_for(action)
    answer = _prediction_category(
        forecast_time, _action_baseline(data, trial, action),
    )
    return action, answer


def _prediction_scheme_text(trial, action: str) -> str:
    return {
        "release": f"沿 {trial.release_path}，立即起飞",
        "delay": f"沿 {trial.release_path}，等待 {trial.delay_seconds:g} 秒后起飞",
        "reroute": f"沿 {trial.reroute_path}，立即起飞",
    }[action]


def default_prediction_spec(data, trial) -> Dict[str, Any]:
    action, answer = _prediction_reference(data, trial)
    baseline = _action_baseline(data, trial, action)
    return {
        "text": (
            "假设无人机立即沿 Path-A 起飞。"
            "根据预测风场，其飞行时间与 Path-A 的无风飞行时间相比会如何变化？"
        ),
        "options": _balance_option_position(
            list(DEFAULT_PREDICTION_OPTIONS), answer, trial.trial_id,
        ),
        "answer": answer,
        "referenceAction": action,
        "baselineTimeSeconds": baseline,
    }


def practice_prediction_spec(data, trial) -> Dict[str, Any]:
    """Use a binary direction judgment in practice, without '基本不变'."""
    spec = default_prediction_spec(data, trial)
    forecast_time = data.ground_truth[trial.trial_id].time_for("release")
    answer = (
        PRACTICE_PREDICTION_OPTIONS[0]
        if forecast_time < spec["baselineTimeSeconds"]
        else PRACTICE_PREDICTION_OPTIONS[1]
    )
    return {
        **spec,
        "options": _balance_option_position(
            list(PRACTICE_PREDICTION_OPTIONS), answer, trial.trial_id,
        ),
        "answer": answer,
    }


def _trajectory_state_at(result, observation_time: float):
    """Interpolate one forecast-mean trajectory at an explicitly named time."""
    frames = result.frames
    if observation_time >= frames[-1].time:
        final = frames[-1]
        return final.x, final.y, final.segment_index, True
    for previous, current in zip(frames, frames[1:]):
        if previous.time <= observation_time <= current.time:
            span = max(current.time - previous.time, 1e-9)
            ratio = (observation_time - previous.time) / span
            segment_index = (
                current.segment_index if ratio >= 1.0 - 1e-9
                else previous.segment_index
            )
            return (
                previous.x + (current.x - previous.x) * ratio,
                previous.y + (current.y - previous.y) * ratio,
                segment_index,
                current.status == "finished" and ratio >= 1.0 - 1e-9,
            )
    first = frames[0]
    return first.x, first.y, first.segment_index, False


def intermediate_spec(data, trial, simulator: Optional[Simulator] = None) -> Dict[str, Any]:
    """Build the fixed Path-A state question from the forecast-mean trajectory."""
    scene_simulator = simulator or Simulator(data)
    route = data.routes[trial.route_set_id][trial.release_path]
    labels = [
        _segment_label(start, end)
        for start, end in zip(route.waypoints, route.waypoints[1:])
    ]
    result = scene_simulator.run(
        trial.trial_id, "release", include_frames=True, wind_mode="forecast",
    )
    observation_time = 120.0
    _, _, segment_index, finished = _trajectory_state_at(result, observation_time)
    answer = ARRIVED_AT_GOAL if finished else labels[min(segment_index, len(labels) - 1)]
    options = [*labels, ARRIVED_AT_GOAL]
    return {
        "text": "假设无人机立即沿 Path-A 起飞，第120秒时，你认为无人机位于哪个航段？",
        "options": _balance_option_position(options, answer, trial.trial_id),
        "answer": answer,
    }


def create_app(workbook: Path = DEFAULT_WORKBOOK, db_path: Optional[Path] = None) -> FastAPI:
    data = load_experiment_data(workbook)
    simulator = Simulator(data)
    store = EventStore(db_path) if db_path else EventStore()
    app = FastAPI(title="UAV监督决策仿真", version="0.4.0")
    app.add_middleware(
        CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_methods=["*"], allow_headers=["*"],
    )

    @app.get("/api/health")
    def health() -> Dict[str, Any]:
        return {"status": "ok", "trialCount": len(data.trials), "version": app.version}

    trials_by_block: Dict[int, List[int]] = {}
    for configured_trial in data.trials.values():
        trials_by_block.setdefault(configured_trial.block, []).append(configured_trial.trial_id)

    @app.post("/api/participants", status_code=201)
    def create_participant(request: ParticipantRequest) -> Dict[str, Any]:
        try:
            return store.create_participant(request.participantId, trials_by_block)
        except ValueError as error:
            raise HTTPException(409, str(error)) from error

    @app.get("/api/participants/{participant_id}/state")
    def participant_state(participant_id: str) -> Dict[str, Any]:
        try:
            state = store.participant_state(participant_id)
        except ValueError as error:
            raise HTTPException(404, str(error)) from error
        return {
            "participantId": state["participant_id"], "orderId": state["order_id"],
            "status": state["status"], "completedTrials": state["completed_trials"],
            "experimentVersion": state["experiment_version"],
            "materialVersion": state["material_version"],
        }

    def safe_trial_payload(trial_id: int) -> Dict[str, Any]:
        trial = data.trials.get(trial_id)
        if not trial:
            raise HTTPException(404, "Trial不存在")
        routes = data.routes[trial.route_set_id]
        process_question = intermediate_spec(data, trial, simulator)
        prediction_question = default_prediction_spec(data, trial)
        forecast_winds = simulator.forecast_winds(trial_id)
        route_baselines = {
            path_id: _action_baseline(
                data, trial, "reroute" if path_id == trial.reroute_path else "release",
            )
            for path_id in (trial.release_path, trial.reroute_path)
        }
        no_wind_benchmarks = {
            action: simulator.no_wind_benchmark(trial_id, action)
            for action in ("release", "delay", "reroute")
        }
        return {
            "trialId": trial.trial_id, "block": trial.block,
            "droneCount": 1,
            "delaySeconds": trial.delay_seconds, "intermediateQuestion": process_question["text"],
            "intermediateOptions": process_question["options"],
            "defaultPredictionQuestion": prediction_question["text"],
            "defaultPredictionOptions": prediction_question["options"],
            "predictionReferenceAction": prediction_question["referenceAction"],
            "baselineTimeSeconds": prediction_question["baselineTimeSeconds"],
            "routeBaselineTimeSeconds": route_baselines,
            "noWindBenchmarks": no_wind_benchmarks,
            "routes": [{"pathId": route.path_id, "taskRole": route.task_role,
                        # Return independent dictionaries. vars(p) would expose the
                        # frozen dataclass's mutable __dict__, allowing practice
                        # display shifts to corrupt the authoritative route geometry.
                        "waypoints": [
                            {"waypoint_id": p.waypoint_id, "x": p.x, "y": p.y}
                            for p in route.waypoints
                        ]}
                       for route in routes.values()
                       if route.path_id in {trial.release_path, trial.reroute_path}],
            "windLayers": [{"layer": layer, "zones": [
                {"zoneId": z.zone_id, "cx": z.cx, "cy": z.cy, "radius": z.radius,
                 "meanSpeed": math.hypot(wx, wy),
                 "directionDeg": math.degrees(math.atan2(wy, wx)) % 360.0,
                 "speedSD": 0.0 if layer == 0 else z.speed_sd,
                 "directionSD": 0.0 if layer == 0 else z.direction_sd,
                 "isForecast": layer > 0,
                 "shapeSeed": (
                     z.trial_id * 17 + z.time_layer * 11 + sum(map(ord, z.zone_id))
                 ) % 31}
                for z, wx, wy in forecast_winds[layer]
            ]} for layer in (0, 1, 2)],
            "windLayerStarts": [0, data.parameters["windLayer1Start_s"], data.parameters["windLayer2Start_s"]],
            "initialDrones": simulator.initial_drones(trial_id),
        }

    def realized_wind_layers(trial_id: int) -> List[Dict[str, Any]]:
        """Serialize the seed-fixed wind realization without forecast uncertainty."""
        actual_winds = simulator.actual_winds(trial_id)
        return [{"layer": layer, "zones": [
            {"zoneId": zone.zone_id, "cx": zone.cx, "cy": zone.cy,
             "radius": zone.radius, "meanSpeed": math.hypot(wx, wy),
             "directionDeg": math.degrees(math.atan2(wy, wx)) % 360.0,
             "speedSD": 0.0, "directionSD": 0.0, "isForecast": False,
             "shapeSeed": (
                 zone.trial_id * 17 + zone.time_layer * 11
                 + sum(map(ord, zone.zone_id))
             ) % 31}
            for zone, wx, wy in actual_winds[layer]
        ]} for layer in (0, 1, 2)]

    def participant_frames(result, *, shift_x: float = 0, shift_y: float = 0):
        """Hide the delay waiting animation while retaining absolute scene time."""
        trial = data.trials[result.trial_id]
        start_time = trial.delay_seconds if result.action == "delay" else 0.0
        visible = [frame for frame in result.frames if frame.time >= start_time - 1e-9]
        return [{
            "time": frame.time,
            "x": frame.x + shift_x,
            "y": frame.y + shift_y,
            "groundSpeed": frame.ground_speed,
            "batteryWh": frame.battery_wh,
            "energyWh": frame.energy_wh,
            "eta": frame.eta,
            "status": (
                "flying" if result.action == "delay" and index == 0 else frame.status
            ),
            "actualWindLayer": frame.actual_wind_layer,
            "segmentIndex": frame.segment_index,
        } for index, frame in enumerate(visible)]

    def action_simulation_payload(
        trial_id: int, action: str, results: Dict[str, Any],
        *, shift_x: float = 0, shift_y: float = 0, session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        selected = results[action]
        wind_layers = realized_wind_layers(trial_id)
        if shift_x or shift_y:
            for layer in wind_layers:
                for zone in layer["zones"]:
                    zone["cx"] += shift_x
                    zone["cy"] += shift_y
        return {
            "sessionId": session_id or f"practice-{trial_id}-{action}",
            "trialId": trial_id,
            "action": action,
            "pathId": selected.path_id,
            "seed": selected.seed,
            "summary": {
                "totalTime": selected.total_time,
                "airborneTime": selected.airborne_time,
                "energyWh": selected.energy_wh,
                "minBatteryWh": selected.min_battery_wh,
                "maxCrosswind": selected.max_crosswind,
                "strongestZone": selected.strongest_zone,
            },
            "actionTimes": {
                candidate: result.airborne_time for candidate, result in results.items()
            },
            "actualWindLayers": wind_layers,
            "frames": participant_frames(
                selected, shift_x=shift_x, shift_y=shift_y,
            ),
        }

    def practice_action_results(practice_no: int):
        """Use forecast means as the authority for training feedback."""
        return {
            action: simulator.run(practice_no, action, wind_mode="forecast")
            for action in ("release", "delay", "reroute")
        }

    def practice_answer(practice_no: int) -> str:
        results = practice_action_results(practice_no)
        return min(results, key=lambda action: results[action].airborne_time)

    def practice_payload(practice_no: int) -> Dict[str, Any]:
        if practice_no not in range(1, 5):
            raise HTTPException(404, "练习trial不存在")
        base_trial_id = practice_no
        payload = safe_trial_payload(base_trial_id)
        payload["trialId"] = -practice_no
        payload["block"] = 0
        payload["positionInBlock"] = practice_no
        payload["globalOrder"] = practice_no
        payload["practice"] = True
        prediction_question = practice_prediction_spec(data, data.trials[base_trial_id])
        payload["defaultPredictionQuestion"] = prediction_question["text"]
        payload["defaultPredictionOptions"] = prediction_question["options"]
        # Deterministic visual variation keeps practice stimuli separate from formal IDs.
        shift_x = (-150, 120, -80, 160)[practice_no - 1]
        shift_y = (100, -120, -80, 60)[practice_no - 1]
        for route in payload["routes"]:
            for point in route["waypoints"]:
                point["x"] += shift_x
                point["y"] += shift_y
        for layer in payload["windLayers"]:
            for zone in layer["zones"]:
                zone["cx"] += shift_x
                zone["cy"] += shift_y
        for drone in payload["initialDrones"]:
            drone["x"] += shift_x
            drone["y"] += shift_y
        return payload

    @app.get("/api/practice/{practice_no}")
    def get_practice(practice_no: int) -> Dict[str, Any]:
        return practice_payload(practice_no)

    @app.post("/api/practice/{practice_no}/simulation")
    def simulate_practice(
        practice_no: int, request: PracticeSimulationRequest,
    ) -> Dict[str, Any]:
        if practice_no not in range(1, 5):
            raise HTTPException(404, "练习trial不存在")
        result = simulator.run(
            practice_no, request.action, include_frames=True, wind_mode="forecast",
        )
        shift_x = (-150, 120, -80, 160)[practice_no - 1]
        shift_y = (100, -120, -80, 60)[practice_no - 1]
        return {
            "sessionId": f"practice-{practice_no}-{request.action}",
            "trialId": -practice_no,
            "action": result.action,
            "pathId": result.path_id,
            "seed": result.seed,
            "summary": {
                "totalTime": result.total_time,
                "airborneTime": result.airborne_time,
                "energyWh": result.energy_wh,
                "minBatteryWh": result.min_battery_wh,
                "maxCrosswind": result.max_crosswind,
                "strongestZone": result.strongest_zone,
            },
            "frames": participant_frames(
                result, shift_x=shift_x, shift_y=shift_y,
            ),
        }

    @app.post("/api/participants/{participant_id}/practice/{practice_no}")
    def submit_practice(
        participant_id: str, practice_no: int, request: PracticeResponseRequest,
    ) -> Dict[str, Any]:
        if practice_no not in range(1, 5):
            raise HTTPException(404, "练习trial不存在")
        if request.defaultPrediction not in PRACTICE_PREDICTION_OPTIONS:
            raise HTTPException(400, "无效的飞行时间判断")
        results = practice_action_results(practice_no)
        answer = min(results, key=lambda action: results[action].airborne_time)
        correct = request.finalAction == answer
        prediction_spec = practice_prediction_spec(data, data.trials[practice_no])
        prediction_correct = request.defaultPrediction == prediction_spec["answer"]
        try:
            store.add_practice_response(
                participant_id, practice_no, request.defaultPrediction, request.finalAction, correct,
            )
        except ValueError as error:
            raise HTTPException(404, str(error)) from error
        except Exception as error:
            raise HTTPException(409, "该练习已提交") from error
        labels = {
            "release": "现在沿 Path-A 起飞",
            "delay": f"等待 {data.trials[practice_no].delay_seconds:g} 秒后沿 Path-A 起飞",
            "reroute": "现在沿 Path-B 起飞",
        }
        times = {action: result.airborne_time for action, result in results.items()}
        return {
            "correct": correct, "predictionCorrect": prediction_correct, "bestAction": answer,
            "feedback": (
                f"第1题：回答{'正确' if prediction_correct else '不正确'}。\n"
                f"正确答案：“{prediction_spec['answer']}”。\n\n"
                f"第2题：回答{'正确' if correct else '不正确'}。\n"
                f"正确答案：“{labels[answer]}”（按预测均值评分）。\n"
                f"三种方案的最终飞行时间：立即沿 Path-A 起飞 {times['release']:.1f} 秒；"
                f"等待 {data.trials[practice_no].delay_seconds:g} 秒后沿 Path-A 起飞 "
                f"{times['delay']:.1f} 秒；立即沿 Path-B 起飞 {times['reroute']:.1f} 秒。"
                + (
                    "\n\n原因提示：需要同时比较 Path-A 与 Path-B 在 W1、W2 中受到的风向和风速影响，"
                    "并结合立即起飞经历的 T0/T1 与延迟起飞经历的 T1/T2 判断完整飞行过程。"
                    if practice_no == 1 else ""
                )
            ),
        }

    @app.get("/api/participants/{participant_id}/next-trial")
    def next_trial(participant_id: str) -> Dict[str, Any]:
        try:
            store.participant_state(participant_id)
            if not store.formal_ready(participant_id):
                raise HTTPException(409, "请先通过理解测试并完成4个练习trial")
            assignment = store.next_assignment(participant_id)
        except ValueError as error:
            raise HTTPException(404, "participant不存在") from error
        if not assignment:
            return {"complete": True}
        payload = safe_trial_payload(assignment["trial_id"])
        payload.update({"complete": False, "positionInBlock": assignment["position_in_block"],
                        "globalOrder": assignment["global_order"]})
        return payload

    @app.post("/api/participants/{participant_id}/trials/{trial_id}/simulation")
    def simulate_formal_trial(
        participant_id: str, trial_id: int, request: FormalSimulationRequest,
    ) -> Dict[str, Any]:
        if not store.formal_ready(participant_id):
            raise HTTPException(409, "请先通过理解测试并完成4个练习trial")
        saved = store.trial_response(participant_id, trial_id)
        if not saved:
            raise HTTPException(409, "必须先一次性提交本轮全部答案，才能查看仿真结果")
        if saved["final_action"] != request.action:
            raise HTTPException(409, "只能播放本轮已提交的调度方案")

        results = {
            action: simulator.run(
                trial_id, action, include_frames=action == request.action,
                wind_mode="actual",
            )
            for action in ("release", "delay", "reroute")
        }
        selected = results[request.action]
        session_id = str(uuid4())
        store.add_session(session_id, trial_id, request.action, selected.seed)
        return action_simulation_payload(
            trial_id, request.action, results, session_id=session_id,
        )

    @app.post("/api/participants/{participant_id}/trials/{trial_id}/submit")
    def submit_trial(
        participant_id: str, trial_id: int, request: TrialResponseRequest,
    ) -> Dict[str, Any]:
        if request.trialId != trial_id:
            raise HTTPException(400, "trialId不一致")
        truth = data.ground_truth.get(trial_id)
        if not truth:
            raise HTTPException(404, "Trial不存在")
        if request.defaultPrediction not in DEFAULT_PREDICTION_OPTIONS:
            raise HTTPException(400, "无效的飞行时间判断")
        if any(not math.isfinite(value) or value < 0 for value in request.layerDwellMs) or any(
            layer not in (0, 1, 2) for layer in request.layerSequence
        ):
            raise HTTPException(400, "无效的时间层交互记录")
        if not store.formal_ready(participant_id):
            raise HTTPException(409, "请先通过理解测试并完成4个练习trial")
        results = {
            action: simulator.run(
                trial_id, action, include_frames=action == request.finalAction,
                wind_mode="actual",
            )
            for action in ("release", "delay", "reroute")
        }
        timed_out = request.timedOut or request.decisionRtMs > 90_000
        prediction_spec = default_prediction_spec(data, data.trials[trial_id])
        prediction_time = truth.time_for(prediction_spec["referenceAction"])
        prediction_difference_s = prediction_time - prediction_spec["baselineTimeSeconds"]
        response = {
            "trial_id": trial_id, "predicted_best_action": "not_applicable",
            "default_prediction": request.defaultPrediction,
            "default_prediction_correct": int(
                request.defaultPrediction == prediction_spec["answer"]
            ),
            "default_prediction_difference_s": prediction_difference_s,
            "default_prediction_difference_pct": (
                prediction_difference_s / prediction_spec["baselineTimeSeconds"] * 100.0
            ),
            "prediction_rt_ms": request.predictionRtMs,
            "intermediate_answer": request.intermediateAnswer,
            "intermediate_correct": int(
                request.intermediateAnswer == intermediate_spec(data, data.trials[trial_id])["answer"]
            ),
            "intermediate_rt_ms": request.intermediateRtMs, "final_action": request.finalAction,
            "decision_rt_ms": request.decisionRtMs,
            # This legacy non-null column recorded prediction difficulty.  New
            # questionnaires use perceived_uncertainty instead; zero exports as blank.
            "perceived_predictability": 0,
            "perceived_uncertainty": request.perceivedUncertainty,
            "perceived_intervenability": request.perceivedIntervenability,
            "flight_time_confidence": request.flightTimeConfidence,
            "action_choice_confidence": request.actionChoiceConfidence,
            "rating_rt_ms": request.ratingRtMs,
            "layer_t0_dwell_ms": request.layerDwellMs[0],
            "layer_t1_dwell_ms": request.layerDwellMs[1],
            "layer_t2_dwell_ms": request.layerDwellMs[2],
            "layer_switch_count": request.layerSwitchCount,
            "layer_sequence_json": json.dumps(request.layerSequence),
            "first_future_layer_opened_ms": request.firstFutureLayerOpenedMs,
            "prediction_change_count": request.predictionChangeCount,
            "intermediate_change_count": request.intermediateChangeCount,
            "decision_change_count": request.decisionChangeCount,
            "prediction_decision_consistency": (
                0 if request.defaultPrediction == DEFAULT_PREDICTION_OPTIONS[2]
                else int(
                    (request.defaultPrediction == DEFAULT_PREDICTION_OPTIONS[0]
                     and request.finalAction == "release")
                    or (request.defaultPrediction == DEFAULT_PREDICTION_OPTIONS[1]
                        and request.finalAction != "release")
                )
            ),
            "optimal_action_accuracy": int(
                request.finalAction == truth.best_time_action
            ),
            "trial_started_at": request.trialStartedAt,
            "timed_out": int(timed_out),
            "actual_release_time_s": results["release"].airborne_time,
            "actual_delay_time_s": results["delay"].airborne_time,
            "actual_reroute_time_s": results["reroute"].airborne_time,
        }
        try:
            store.add_trial_response(participant_id, response)
        except ValueError as error:
            raise HTTPException(409, str(error)) from error
        selected = results[request.finalAction]
        session_id = str(uuid4())
        store.add_session(session_id, trial_id, request.finalAction, selected.seed)
        return action_simulation_payload(
            trial_id, request.finalAction, results, session_id=session_id,
        )

    @app.post("/api/participants/{participant_id}/blocks/{block}/ratings", status_code=204)
    def submit_block_rating(participant_id: str, block: int, request: BlockRatingRequest) -> None:
        if block not in (1, 2, 3):
            raise HTTPException(400, "block必须为1–3")
        try:
            store.add_block_rating(participant_id, block, {
                "mental_effort": request.mentalEffort, "fatigue": request.fatigue,
                "task_difficulty": request.taskDifficulty,
                "mental_simulation_strategy": request.mentalSimulationStrategy,
                "multi_action_comparison_strategy": request.multiActionComparisonStrategy,
                "simple_cue_strategy": request.simpleCueStrategy,
            })
        except Exception as error:
            raise HTTPException(409, "block评分已提交或participant无效") from error

    comprehension_key = {
        "wind": "B",
        "layers": "C",
        "delay": "A",
        "goal": "B",
        "flight_layers": "C",
        "wind_effect": "B",
    }

    @app.post("/api/participants/{participant_id}/comprehension")
    def submit_comprehension(participant_id: str, request: ComprehensionRequest) -> Dict[str, Any]:
        correct = sum(request.answers.get(key) == value for key, value in comprehension_key.items())
        passed = correct == len(comprehension_key)
        try:
            store.add_comprehension_attempt(participant_id, request.answers, correct, passed)
        except Exception as error:
            raise HTTPException(404, "participant不存在") from error
        return {"correctCount": correct, "total": len(comprehension_key), "passed": passed}

    @app.post("/api/participants/{participant_id}/complete", status_code=204)
    def complete_participant(participant_id: str, request: CompletionRequest) -> None:
        try:
            answers = request.questionnaire.model_dump()
            store.complete(participant_id, {
                "questionnaire_version": "2026-09-measures-v7",
                "major": answers["major"],
                "uav_experience": answers["uavExperience"],
                "aviation_course": answers["aviationCourse"],
                "system_management_experience": answers["systemManagementExperience"],
                "game_experience": answers["gameExperience"], "map_ability": answers["mapAbility"],
                "sustained_attention": answers["sustainedAttention"],
                "overall_effort": answers["overallEffort"],
                "scenario_comprehension": answers["scenarioComprehension"],
                "wind_route_reliance": answers["windRouteReliance"],
                "wind_relation_importance": answers["windRelationImportance"],
                "mental_simulation_use": answers["mentalSimulationUse"],
                "perceived_realism": answers["perceivedRealism"],
                "uncertainty_analysis_effort": answers["uncertaintyAnalysisEffort"],
                "intervenability_comparison_effort": answers["intervenabilityComparisonEffort"],
                "simulation_trigger_selections": answers["simulationTriggerSelections"],
                "simulation_trigger_other": answers["simulationTriggerOther"],
                "decision_information_selections": answers["decisionInformationSelections"],
                "decision_information_other": answers["decisionInformationOther"],
                "additional_information": answers["additionalInformation"],
            })
        except ValueError as error:
            raise HTTPException(409, str(error)) from error

    def csv_response(columns: List[str], rows: List[tuple[Any, ...]], filename: str) -> StreamingResponse:
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(columns)
        writer.writerows(rows)
        content = "\ufeff" + output.getvalue()
        return StreamingResponse(
            iter([content]), media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @app.get("/api/admin/export/trials.csv")
    def export_trials() -> StreamingResponse:
        columns, rows = store.export_trial_rows()
        enriched_columns = columns[:8] + [
            "objective_uncertainty", "objective_predictability", "objective_intervenability",
            "best_time_action", "forecast_release_time_s", "forecast_delay_time_s",
            "forecast_reroute_time_s", "forecast_selected_time_s", "forecast_best_time_s",
            "forecast_second_best_time_s", "forecast_decision_gap_seconds", "forecast_decision_gap_pct",
            "decision_regret_seconds", "normalized_decision_regret", "intervenability_pct",
            "decision_gap_pct",
        ] + columns[8:]
        enriched_rows = []
        for row in rows:
            trial_id = int(row[4])
            material_version = row[2]
            trial = data.trials.get(trial_id)
            truth = data.ground_truth.get(trial_id)
            if material_version != "2026-08-pretest-flow-v2" or not trial or not truth:
                enriched_rows.append(row[:8] + (None,) * 16 + row[8:])
                continue
            forecast_times = {
                "release": truth.release_time_s,
                "delay": truth.delay_time_s,
                "reroute": truth.reroute_time_s,
            }
            ranked_actions = sorted(forecast_times, key=forecast_times.get)
            best_action, second_action = ranked_actions[:2]
            selected_action = row[columns.index("final_action")]
            selected_time = forecast_times[selected_action]
            best_time = forecast_times[best_action]
            second_time = forecast_times[second_action]
            regret = selected_time - best_time
            uncertainty = {"H": "low", "M": "medium", "L": "high"}[trial.predictability]
            enriched_rows.append(row[:8] + (
                uncertainty, trial.predictability, trial.intervenability,
                truth.best_time_action, forecast_times["release"], forecast_times["delay"],
                forecast_times["reroute"], selected_time, best_time, second_time,
                second_time - best_time, (second_time - best_time) / best_time,
                regret, regret / best_time, truth.intervenability_pct, truth.decision_gap_pct,
            ) + row[8:])
        return csv_response(enriched_columns, enriched_rows, "trial_level.csv")

    @app.get("/api/admin/export/questionnaires.csv")
    def export_questionnaires() -> StreamingResponse:
        columns, rows = store.export_questionnaire_rows()
        return csv_response(columns, rows, "questionnaire_level.csv")

    @app.get("/api/admin/export/events.csv")
    def export_events() -> StreamingResponse:
        columns, rows = store.export_event_rows()
        return csv_response(columns, rows, "event_level.csv")

    @app.get("/api/trials")
    def trials() -> List[Dict[str, Any]]:
        return [{
            "trialId": t.trial_id, "block": t.block, "predictability": t.predictability,
            "intervenability": t.intervenability, "droneCount": t.drone_count,
        } for t in data.trials.values()]

    @app.get("/api/trials/{trial_id}")
    def trial_detail(trial_id: int) -> Dict[str, Any]:
        # Development-only endpoint. It is still participant-safe and never returns ground truth.
        return safe_trial_payload(trial_id)

    @app.post("/api/simulations")
    def simulate(request: SimulationRequest) -> Dict[str, Any]:
        try:
            result = simulator.run(
                request.trialId, request.action, wind_mode="forecast",
            )
        except ValueError as error:
            raise HTTPException(400, str(error)) from error
        session_id = str(uuid4())
        store.add_session(session_id, result.trial_id, result.action, result.seed)
        return {
            "sessionId": session_id, "trialId": result.trial_id, "action": result.action,
            "pathId": result.path_id, "seed": result.seed,
            "summary": {"totalTime": result.total_time, "airborneTime": result.airborne_time,
                        "energyWh": result.energy_wh, "minBatteryWh": result.min_battery_wh,
                        "maxCrosswind": result.max_crosswind, "strongestZone": result.strongest_zone},
            "frames": [{
                "time": f.time, "x": f.x, "y": f.y, "groundSpeed": f.ground_speed,
                "batteryWh": f.battery_wh, "energyWh": f.energy_wh, "eta": f.eta,
                "status": f.status, "actualWindLayer": f.actual_wind_layer,
                "segmentIndex": f.segment_index,
            } for f in result.frames],
        }

    @app.post("/api/events", status_code=204)
    def record_event(request: EventRequest) -> None:
        try:
            store.add_event(request.eventType, request.sessionId, request.participantId, request.trialId,
                            request.eventValue, request.clientTimeMs)
        except Exception as error:
            raise HTTPException(400, "事件记录失败") from error

    frontend_dist = Path(__file__).resolve().parents[1] / "frontend" / "dist"
    if frontend_dist.exists():
        assets = frontend_dist / "assets"
        if assets.exists():
            app.mount("/assets", StaticFiles(directory=assets), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str):
            candidate = frontend_dist / path
            if candidate.is_file():
                headers = (
                    {"Cache-Control": "no-cache, no-store, must-revalidate"}
                    if candidate.name == "index.html" else None
                )
                return FileResponse(candidate, headers=headers)
            # Do not let a browser retain an old SPA entry point after rebuilding.
            # Fingerprinted assets remain cacheable under their unique filenames.
            return FileResponse(
                frontend_dist / "index.html",
                headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
            )
    return app
