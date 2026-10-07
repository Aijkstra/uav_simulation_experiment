from pathlib import Path
import math
import random
from collections import Counter
from dataclasses import replace
from typing import Dict, Iterable, List, Tuple

from openpyxl import load_workbook

from .models import ActionGroundTruth, ExperimentData, Route, TrialConfig, Waypoint, WindZone


DEFAULT_WORKBOOK = (
    Path(__file__).resolve().parents[1]
    / "data"
    / "UAV_Trial_Design.xlsx"
)
REQUIRED_SHEETS = {"Trials", "Routes", "WindZones", "Parameters", "ActionGroundTruth"}
REQUIRED_PARAMETERS = {
    "vAir_mps", "minGroundSpeed_mps", "maxGroundSpeed_mps", "windUpdate_s",
    "Pbase_W", "headwindCoeff_W_per_mps2", "crosswindCoeff_W_per_mps2",
    "payloadCoeff_W_per_kg", "batteryCapacity_Wh", "delaySeconds",
}

# The participant-facing network contains only the two candidate routes.
ROUTE_POINT_TARGETS = {"Path-A": 5, "Path-B": 6}
MIN_VISIBLE_WAYPOINTS = 8
MAX_VISIBLE_WAYPOINTS = 12


class ConfigurationError(ValueError):
    pass


def _point_on_route(route: Route, fraction: float) -> Tuple[float, float, float]:
    """Return x, y and local heading at a fraction of route length."""
    segments = []
    total = 0.0
    for start, end in zip(route.waypoints, route.waypoints[1:]):
        length = math.hypot(end.x - start.x, end.y - start.y)
        segments.append((start, end, length))
        total += length
    target = fraction * total
    cursor = 0.0
    for start, end, length in segments:
        if cursor + length >= target:
            ratio = (target - cursor) / max(length, 1e-9)
            return (
                start.x + (end.x - start.x) * ratio,
                start.y + (end.y - start.y) * ratio,
                math.degrees(math.atan2(end.y - start.y, end.x - start.x)) % 360,
            )
        cursor += length
    end = route.waypoints[-1]
    return end.x, end.y, 0.0


PREDICTABILITY_LEVELS = ("H", "M", "L")
INTERVENABILITY_LEVELS = ("H", "L")
FIXED_DRONE_COUNT = 1
FACTORIAL_CELLS = [
    (predictability, intervenability)
    for predictability in PREDICTABILITY_LEVELS
    for intervenability in INTERVENABILITY_LEVELS
]
ACTION_SEQUENCE = ("release", "delay", "reroute")
QUESTION_SEQUENCE = ("第120秒时所在航段",)
PREDICTABILITY_SPEED_SD_MPS = {"H": 0.5, "M": 1.0, "L": 1.5}
PREDICTABILITY_DIRECTION_SD_DEG = {"H": 12.0, "M": 35.0, "L": 60.0}
HIGH_PREDICTABILITY_DIRECTION_SD_DEG = PREDICTABILITY_DIRECTION_SD_DEG["H"]
MEDIUM_PREDICTABILITY_DIRECTION_SD_DEG = PREDICTABILITY_DIRECTION_SD_DEG["M"]
LOW_PREDICTABILITY_DIRECTION_SD_DEG = PREDICTABILITY_DIRECTION_SD_DEG["L"]
ANGLE_CLASS_CENTERS_DEG = (35.0, 90.0, 145.0)
ANGLE_CLASS_NAMES = ("tailwind", "crosswind", "headwind")
LOW_INTERVENABILITY_RANGE_PCT = (5.0, 15.0)
HIGH_INTERVENABILITY_RANGE_PCT = (35.0, 50.0)
DECISION_GAP_RANGE_PCT = (5.0, 15.0)
WIND_ZONE_ROUTE_FRACTIONS = (0.25, 0.75)
# Keep the auxiliary state-prediction answers close to balanced while preserving
# the primary 3x2 design and the stricter action/intervenability constraints.
SHORTER_PREDICTION_TRIAL_IDS = frozenset({1, 3, 5, 7, 11, 15, 17, 18})

# Fixed, rank-preserving realizations used by the formal post-decision playback.
# Each seed was selected only when all realized T1/T2 wind vectors remained
# within the displayed ±1 SD ranges, the planned action stayed uniquely best,
# the best/second gap stayed within 5%–15%, and realized intervenability stayed
# inside the condition's calibrated band. The workbook seed remains the search
# origin; this versioned map is the runtime authority for formal playback.
RANK_PRESERVING_REALIZATION_SEEDS = {
    1: 2026073128,
    2: 2026073340,
    3: 2026083118,
    4: 2026083118,
    5: 2026073128,
    6: 2026083245,
    7: 2026083118,
    8: 2026083214,
    9: 2026073132,
    10: 2026073340,
    11: 2026083615,
    12: 2026073333,
    13: 2026073128,
    14: 2026073128,
    15: 2026083181,
    16: 2026083162,
    17: 2026073128,
    18: 2026083464,
}


def _relative_angle(direction_deg: float, heading_deg: float) -> float:
    return abs((direction_deg - heading_deg + 180.0) % 360.0 - 180.0)


def _angle_class_index(trial: TrialConfig) -> int:
    """Latin-rotate the reference wind/heading class against the best action."""
    cell_index = (trial.trial_id - 1) % len(FACTORIAL_CELLS)
    return (trial.block - 1 + 2 * cell_index) % len(ANGLE_CLASS_CENTERS_DEG)


def _action_metrics(times: Dict[str, float]) -> Tuple[str, float, float]:
    """Return best action, outcome controllability, and best/second decision gap."""
    ranked = sorted(times, key=times.get)
    best, second, worst = ranked
    intervenability = (times[worst] - times[best]) / times[worst] * 100.0
    decision_gap = (times[second] - times[best]) / times[best] * 100.0
    return best, intervenability, decision_gap


def _route_baseline_time(route: Route, airspeed_mps: float) -> float:
    length = sum(
        math.hypot(end.x - start.x, end.y - start.y)
        for start, end in zip(route.waypoints, route.waypoints[1:])
    )
    return length / airspeed_mps


def _apply_factorial_design(trials: Dict[int, TrialConfig]) -> Dict[int, TrialConfig]:
    """Orthogonalize the 3x2 predictability-by-intervenability design.

    Each block contains all six 3x2 cells once. Across the three blocks,
    every cell receives each best action and each process-question family once.
    Background drones remain fixed scene context and are not an experimental factor.
    """
    designed: Dict[int, TrialConfig] = {}
    for block in (1, 2, 3):
        block_trials = sorted(
            (trial for trial in trials.values() if trial.block == block),
            key=lambda trial: trial.trial_id,
        )
        if len(block_trials) != len(FACTORIAL_CELLS):
            raise ConfigurationError(f"Block {block}必须包含6个trial")
        for cell_index, (trial, cell) in enumerate(zip(block_trials, FACTORIAL_CELLS)):
            predictability, intervenability = cell
            designed[trial.trial_id] = replace(
                trial,
                predictability=predictability,
                intervenability=intervenability,
                complexity="Fixed",
                drone_count=FIXED_DRONE_COUNT,
                planned_best_action=ACTION_SEQUENCE[(block - 1 + cell_index) % 3],
                intermediate_question=QUESTION_SEQUENCE[
                    (2 * (block - 1) + cell_index) % len(QUESTION_SEQUENCE)
                ],
                seed=RANK_PRESERVING_REALIZATION_SEEDS[trial.trial_id],
            )
    return designed


def _candidate_zones(
    trial: TrialConfig, route: Route, directions: List[int], speeds: List[float],
    radius: float,
):
    """Create one candidate scene with a pre-balanced reference angle class."""
    speed_sd = PREDICTABILITY_SPEED_SD_MPS[trial.predictability]
    direction_sd = PREDICTABILITY_DIRECTION_SD_DEG[trial.predictability]
    layers: Dict[int, List[WindZone]] = {}
    for layer in (0, 1, 2):
        layers[layer] = []
        for index, fraction in enumerate(WIND_ZONE_ROUTE_FRACTIONS, 1):
            x, y, heading = _point_on_route(route, fraction)
            candidate_index = layer * 2 + index - 1
            offset = ANGLE_CLASS_CENTERS_DEG[directions[candidate_index]]
            layers[layer].append(WindZone(
                trial.trial_id, layer, f"W{index}", x, y, radius,
                speeds[candidate_index], (heading + offset) % 360.0,
                speed_sd, direction_sd,
            ))
    return layers


def _design_wind_zones(
    trials: Dict[int, TrialConfig], routes: Dict[str, Dict[str, Route]],
    parameters: Dict[str, object],
) -> Dict[int, Dict[int, List[WindZone]]]:
    """Offline calibration for angle balance, controllability, and decision gap."""
    from .simulator import Simulator

    designed: Dict[int, Dict[int, List[WindZone]]] = {}
    placeholder = ActionGroundTruth(
        0, 0, 0, 0, 0, 0, 0, "release", "release", 0, 0,
    )
    for trial in trials.values():
        route = routes[trial.route_set_id][trial.release_path]
        reference_class = _angle_class_index(trial)
        rng = random.Random(20260819 + trial.trial_id * 1009)
        selected_layers = None
        for _ in range(12000):
            directions = [reference_class] + [rng.randrange(3) for _ in range(5)]
            if len(set(directions)) < 3:
                continue
            speeds = [rng.choice((2.0, 5.0, 8.0, 11.0, 14.0, 17.0, 20.0)) for _ in range(6)]
            radius = rng.choice((225.0, 250.0, 275.0, 300.0, 325.0, 350.0))
            layers = _candidate_zones(trial, route, directions, speeds, radius)
            provisional = ExperimentData(
                {trial.trial_id: trial}, routes, {trial.trial_id: layers},
                parameters, {trial.trial_id: placeholder},
            )
            simulator = Simulator(provisional)
            times = {
                action: simulator.run(
                    trial.trial_id, action, include_frames=False, wind_mode="forecast",
                ).airborne_time
                for action in ACTION_SEQUENCE
            }
            # The displayed reference action is selected later from the actions
            # that satisfy this clearly-shorter/clearly-longer constraint.
            desired_prediction_sign = (
                -1 if trial.trial_id in SHORTER_PREDICTION_TRIAL_IDS else 1
            )
            clear_prediction_available = False
            for action, forecast_time in times.items():
                path_id = trial.reroute_path if action == "reroute" else trial.release_path
                baseline = _route_baseline_time(
                    routes[trial.route_set_id][path_id],
                    float(parameters["vAir_mps"]),
                )
                relative_difference = (forecast_time - baseline) / baseline
                if desired_prediction_sign * relative_difference > 0.05:
                    clear_prediction_available = True
                    break
            if not clear_prediction_available:
                continue
            best_action, intervenability, decision_gap = _action_metrics(times)
            if best_action != trial.planned_best_action:
                continue
            low, high = (
                HIGH_INTERVENABILITY_RANGE_PCT
                if trial.intervenability == "H"
                else LOW_INTERVENABILITY_RANGE_PCT
            )
            if not low <= intervenability <= high:
                continue
            if not DECISION_GAP_RANGE_PCT[0] <= decision_gap <= DECISION_GAP_RANGE_PCT[1]:
                continue
            selected_layers = layers
            break
        if selected_layers is None:
            raise ConfigurationError(
                f"Trial {trial.trial_id}无法校准{trial.intervenability}可干预性"
            )
        designed[trial.trial_id] = selected_layers
        print(f"calibrated trial {trial.trial_id}/{len(trials)}", flush=True)
    return designed


def _records(ws) -> Iterable[Dict[str, object]]:
    rows = ws.iter_rows(values_only=True)
    headers = [str(value) if value is not None else "" for value in next(rows)]
    for values in rows:
        if any(value is not None for value in values):
            yield dict(zip(headers, values))


def _resample_route(route_set_id: str, path_id: str, points: List[Waypoint], target: int) -> Route:
    """Deterministically densify a route while preserving its original geometry."""
    if len(points) >= target:
        return Route(route_set_id, path_id, points)
    lengths = []
    total = 0.0
    for start, end in zip(points, points[1:]):
        length = ((end.x - start.x) ** 2 + (end.y - start.y) ** 2) ** 0.5
        lengths.append(length)
        total += length
    sampled = [points[0]]
    prefix = path_id.split("-")[-1]
    for index in range(1, target - 1):
        wanted = total * index / (target - 1)
        cursor = 0.0
        for segment, (start, end) in enumerate(zip(points, points[1:])):
            length = lengths[segment]
            if wanted <= cursor + length or segment == len(lengths) - 1:
                ratio = (wanted - cursor) / max(length, 1e-9)
                sampled.append(Waypoint(
                    f"{prefix}{index}",
                    start.x + (end.x - start.x) * ratio,
                    start.y + (end.y - start.y) * ratio,
                ))
                break
            cursor += length
    sampled.append(points[-1])
    return Route(route_set_id, path_id, sampled)


def load_experiment_data(
    path: Path = DEFAULT_WORKBOOK, *, redesign_wind_zones: bool = False,
) -> ExperimentData:
    if not path.exists():
        raise ConfigurationError("找不到实验配置工作簿: %s" % path)
    wb = load_workbook(path, read_only=True, data_only=True)
    missing = REQUIRED_SHEETS.difference(wb.sheetnames)
    if missing:
        raise ConfigurationError("工作簿缺少工作表: %s" % ", ".join(sorted(missing)))

    trials: Dict[int, TrialConfig] = {}
    for row in _records(wb["Trials"]):
        trial_id = int(row["trialId"])
        if trial_id in trials:
            raise ConfigurationError("Trials中trialId重复: %s" % trial_id)
        trials[trial_id] = TrialConfig(
            trial_id=trial_id, block=int(row["block"]),
            predictability=str(row["predictability"]),
            intervenability=str(row["intervenability"]),
            complexity=str(row["complexity"]), drone_count=int(row["droneCount"]),
            route_set_id=str(row["routeSetId"]), start_node=str(row["startNode"]),
            goal_node=str(row["goalNode"]), release_path=str(row["releasePath"]),
            delay_seconds=float(row["delaySeconds"]), reroute_path=str(row["reroutePath"]),
            planned_best_action=str(row["plannedBestAction"]),
            intermediate_question=str(row["intermediateQuestion"]), seed=int(row["seed"]),
        )
    trials = _apply_factorial_design(trials)

    route_rows: Dict[Tuple[str, str], List[Tuple[int, Waypoint]]] = {}
    for row in _records(wb["Routes"]):
        key = (str(row["routeSetId"]), str(row["pathId"]))
        route_rows.setdefault(key, []).append((
            int(row["seq"]),
            Waypoint(str(row["waypointId"]), float(row["x_m"]), float(row["y_m"])),
        ))
    routes: Dict[str, Dict[str, Route]] = {}
    for (route_set_id, path_id), points in route_rows.items():
        if path_id not in ROUTE_POINT_TARGETS:
            continue
        points.sort(key=lambda item: item[0])
        seqs = [seq for seq, _ in points]
        if seqs != list(range(1, len(seqs) + 1)) or len(points) < 2:
            raise ConfigurationError("路线航点序号无效: %s/%s" % (route_set_id, path_id))
        raw_points = [point for _, point in points]
        target = ROUTE_POINT_TARGETS.get(path_id, len(raw_points))
        routes.setdefault(route_set_id, {})[path_id] = _resample_route(
            route_set_id, path_id, raw_points, target
        )

    wind_zones: Dict[int, Dict[int, List[WindZone]]] = {}
    for row in _records(wb["WindZones"]):
        zone = WindZone(
            int(row["trialId"]), int(row["timeLayer"]), str(row["zoneId"]),
            float(row["centerX_m"]), float(row["centerY_m"]), float(row["radius_m"]),
            float(row["meanSpeed_mps"]), float(row["direction_deg"]),
            float(row["speedSD_mps"]), float(row["directionSD_deg"]),
        )
        wind_zones.setdefault(zone.trial_id, {}).setdefault(zone.time_layer, []).append(zone)

    parameters = {str(row["parameter"]): row["value"] for row in _records(wb["Parameters"])}
    parameters.setdefault("windLayer1Start_s", 120.0)
    parameters.setdefault("windLayer2Start_s", 240.0)
    missing_params = REQUIRED_PARAMETERS.difference(parameters)
    if missing_params:
        raise ConfigurationError("Parameters缺少参数: %s" % ", ".join(sorted(missing_params)))

    # Material calibration is intentionally offline. Normal experiment startup
    # reads the frozen workbook; the sync tool opts into the expensive redesign.
    if redesign_wind_zones:
        parameters["minGroundSpeed_mps"] = 2.0
        wind_zones = _design_wind_zones(trials, routes, parameters)

    ground_truth: Dict[int, ActionGroundTruth] = {}
    for row in _records(wb["ActionGroundTruth"]):
        trial_id = int(row["trialId"])
        action_times = {
            "release": float(row["releaseTime_s"]),
            "delay": float(row["delayTime_s"]),
            "reroute": float(row["rerouteTime_s"]),
        }
        action_energy = {
            "release": float(row["releaseEnergy_Wh"]),
            "delay": float(row["delayEnergy_Wh"]),
            "reroute": float(row["rerouteEnergy_Wh"]),
        }
        best_time, intervenability_pct, decision_gap_pct = _action_metrics(action_times)
        best_energy = min(action_energy, key=action_energy.get)
        ground_truth[trial_id] = ActionGroundTruth(
            trial_id=trial_id,
            release_time_s=action_times["release"], delay_time_s=action_times["delay"],
            reroute_time_s=action_times["reroute"], release_energy_wh=action_energy["release"],
            delay_energy_wh=action_energy["delay"], reroute_energy_wh=action_energy["reroute"],
            best_time_action=best_time, best_energy_action=best_energy,
            intervenability_pct=intervenability_pct,
            decision_gap_pct=decision_gap_pct,
        )

    for trial in trials.values():
        trial_routes = routes.get(trial.route_set_id, {})
        for path_id in (trial.release_path, trial.reroute_path):
            if path_id not in trial_routes:
                raise ConfigurationError("Trial %s缺少路线%s" % (trial.trial_id, path_id))
        candidate_routes = [
            trial_routes[trial.release_path], trial_routes[trial.reroute_path],
        ]
        for route in candidate_routes:
            if route.waypoints[0].waypoint_id != trial.start_node:
                raise ConfigurationError(
                    "Trial %s路线%s必须从%s开始"
                    % (trial.trial_id, route.path_id, trial.start_node)
                )
            if route.waypoints[-1].waypoint_id != trial.goal_node:
                raise ConfigurationError(
                    "Trial %s路线%s必须在%s结束"
                    % (trial.trial_id, route.path_id, trial.goal_node)
                )
        candidate_starts = {
            (round(route.waypoints[0].x, 6), round(route.waypoints[0].y, 6))
            for route in candidate_routes
        }
        if len(candidate_starts) != 1:
            raise ConfigurationError(
                "Trial %s的候选路线必须共享无人机初始坐标" % trial.trial_id
            )
        layers = wind_zones.get(trial.trial_id, {})
        if set(layers) != {0, 1, 2}:
            raise ConfigurationError("Trial %s必须包含风场层0、1、2" % trial.trial_id)
        if any(not 1 <= len(layers[layer]) <= 2 for layer in layers):
            raise ConfigurationError("Trial %s每个风场层必须包含1–2个风区" % trial.trial_id)
        unique_points = {
            (round(point.x, 6), round(point.y, 6))
            for route in trial_routes.values()
            for point in route.waypoints
        }
        if not MIN_VISIBLE_WAYPOINTS <= len(unique_points) <= MAX_VISIBLE_WAYPOINTS:
            raise ConfigurationError(
                "Trial %s可见航路点必须为%s–%s个，当前为%s个"
                % (trial.trial_id, MIN_VISIBLE_WAYPOINTS, MAX_VISIBLE_WAYPOINTS, len(unique_points))
            )
        truth = ground_truth.get(trial.trial_id)
        if not truth:
            raise ConfigurationError("Trial %s缺少ActionGroundTruth" % trial.trial_id)
    # Freeze forecast-optimal ground truth. Participants see these forecast means
    # plus uncertainty; hidden actual realizations are used only during playback.
    provisional = ExperimentData(trials, routes, wind_zones, parameters, ground_truth)
    from .simulator import Simulator
    simulator = Simulator(provisional)
    simulated_truth: Dict[int, ActionGroundTruth] = {}
    for trial_id in trials:
        results = {
            action: simulator.run(
                trial_id, action, include_frames=False, wind_mode="forecast",
            )
            for action in ("release", "delay", "reroute")
        }
        times = {action: result.airborne_time for action, result in results.items()}
        energies = {action: result.energy_wh for action, result in results.items()}
        best_action, intervenability_pct, decision_gap_pct = _action_metrics(times)
        simulated_truth[trial_id] = ActionGroundTruth(
            trial_id=trial_id,
            release_time_s=times["release"], delay_time_s=times["delay"],
            reroute_time_s=times["reroute"], release_energy_wh=energies["release"],
            delay_energy_wh=energies["delay"], reroute_energy_wh=energies["reroute"],
            best_time_action=best_action,
            best_energy_action=min(energies, key=energies.get),
            intervenability_pct=intervenability_pct,
            decision_gap_pct=decision_gap_pct,
        )
    ground_truth = simulated_truth
    counts = {action: sum(gt.best_time_action == action for gt in ground_truth.values())
              for action in ("release", "delay", "reroute")}
    if counts != {"release": 6, "delay": 6, "reroute": 6}:
        raise ConfigurationError("最优行动必须平衡为6/6/6，当前为%s" % counts)
    factorial_counts = Counter(
        (trial.predictability, trial.intervenability)
        for trial in trials.values()
    )
    if set(factorial_counts.values()) != {3} or len(factorial_counts) != 6:
        raise ConfigurationError("3×2条件必须每格3个trial")
    for trial_id, truth in ground_truth.items():
        trial = trials[trial_id]
        if truth.best_time_action != trial.planned_best_action:
            raise ConfigurationError(
                f"Trial {trial_id}校准后最优行动不是{trial.planned_best_action}"
            )
        low, high = (
            HIGH_INTERVENABILITY_RANGE_PCT
            if trial.intervenability == "H"
            else LOW_INTERVENABILITY_RANGE_PCT
        )
        if not low <= truth.intervenability_pct <= high:
            raise ConfigurationError(
                f"Trial {trial_id}可干预性{truth.intervenability_pct:.2f}%不在{low:.0f}%–{high:.0f}%"
            )
        if not DECISION_GAP_RANGE_PCT[0] <= truth.decision_gap_pct <= DECISION_GAP_RANGE_PCT[1]:
            raise ConfigurationError(
                f"Trial {trial_id}最优—次优差值{truth.decision_gap_pct:.2f}%超出平衡范围"
            )
        reference_zone = next(
            zone for zone in wind_zones[trial_id][0] if zone.zone_id == "W1"
        )
        _, _, heading = _point_on_route(
            routes[trial.route_set_id][trial.release_path], WIND_ZONE_ROUTE_FRACTIONS[0],
        )
        expected_angle = ANGLE_CLASS_CENTERS_DEG[_angle_class_index(trial)]
        if abs(_relative_angle(reference_zone.direction_deg, heading) - expected_angle) > 1e-6:
            raise ConfigurationError(f"Trial {trial_id}参考风向夹角未按交叉轮换配置")
    return ExperimentData(trials, routes, wind_zones, parameters, ground_truth)
