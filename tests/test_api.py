from pathlib import Path
import csv
import io
import json
import math

import pytest

from fastapi.testclient import TestClient
from pydantic import ValidationError

from uav_sim.api import BackgroundQuestionnaire, NOT_IN_WIND_ZONE, create_app, intermediate_spec
from uav_sim.config import load_experiment_data
from uav_sim.simulator import Simulator


def _questionnaire_payload(**updates):
    payload = {
        "major": "工程与技术", "uavExperience": "none", "aviationCourse": "some",
        "systemManagementExperience": "some", "gameExperience": "none", "mapAbility": 5,
        "sustainedAttention": 4, "overallEffort": 4, "scenarioComprehension": 4,
        "windRouteReliance": 4, "windRelationImportance": 4, "mentalSimulationUse": 4,
        "perceivedRealism": 4, "uncertaintyAnalysisEffort": 4,
        "intervenabilityComparisonEffort": 4,
        "simulationTriggerSelections": ["未来风场的变化范围较大时"],
        "simulationTriggerOther": "",
        "decisionInformationSelections": ["风速大小"],
        "decisionInformationOther": "",
    }
    payload.update(updates)
    return payload


def test_questionnaire_multiselect_limits_and_other_explanations():
    with pytest.raises(ValidationError):
        BackgroundQuestionnaire(**_questionnaire_payload(simulationTriggerSelections=[
            "未来风场的变化范围较大时", "不同调度方案可能造成较大的飞行时间差异时",
            "路线、风区和时间层之间的关系较复杂时", "第一眼看不出哪个方案更合适时",
        ]))
    with pytest.raises(ValidationError):
        BackgroundQuestionnaire(**_questionnaire_payload(
            decisionInformationSelections=["其他，请说明"], decisionInformationOther="",
        ))


def test_intermediate_questions_use_one_explicit_answerable_scenario():
    data = load_experiment_data()
    simulator = Simulator(data)
    for trial in data.trials.values():
        spec = intermediate_spec(data, trial, simulator)
        assert spec["text"] == "假设无人机立即沿 Path-A 起飞，第120秒时，你认为无人机位于哪个航段？"
        assert "预计最优方案" not in spec["text"]
        assert "累计影响" not in spec["text"]
        assert "无风基准" not in spec["text"]
        assert len(spec["options"]) == len(set(spec["options"]))
        assert spec["options"].count(spec["answer"]) == 1
        assert NOT_IN_WIND_ZONE not in spec["options"]


def test_every_intermediate_question_has_one_unambiguous_answer():
    data = load_experiment_data()
    simulator = Simulator(data)
    for trial in data.trials.values():
        spec = intermediate_spec(data, trial, simulator)
        assert spec["options"].count(spec["answer"]) == 1


def test_prediction_questions_use_plain_language():
    data = load_experiment_data()
    from uav_sim.api import default_prediction_spec
    for trial in data.trials.values():
        spec = default_prediction_spec(data, trial)
        assert "无风基准" not in spec["text"]
        assert spec["text"] == (
            "假设无人机立即沿 Path-A 起飞。根据预测风场，"
            "其飞行时间与 Path-A 的无风飞行时间相比会如何变化？"
        )
        assert "暴露" not in spec["text"]
        assert set(spec["options"]) == {"更短", "更长", "基本不变"}
        assert spec["referenceAction"] == "release"
        forecast_time = data.ground_truth[trial.trial_id].time_for(
            spec["referenceAction"]
        )
        difference = (forecast_time - spec["baselineTimeSeconds"]) \
            / spec["baselineTimeSeconds"]
        expected = "更短" if difference < -0.05 else "更长" if difference > 0.05 else "基本不变"
        assert spec["answer"] == expected


def test_prediction_question_formats_each_dispatch_scheme_consistently():
    data = load_experiment_data()
    from uav_sim.api import _prediction_scheme_text

    trial = next(iter(data.trials.values()))
    expected = {
        "release": "沿 Path-A，立即起飞",
        "delay": f"沿 Path-A，等待 {trial.delay_seconds:g} 秒后起飞",
        "reroute": "沿 Path-B，立即起飞",
    }
    for action, text in expected.items():
        assert _prediction_scheme_text(trial, action) == text


def test_practice_uses_state_prediction_instead_of_predicted_action(tmp_path: Path):
    client = TestClient(create_app(db_path=tmp_path / "practice-prediction.db"))
    assert client.post("/api/participants", json={
        "participantId": "PRACTICE",
    }).status_code == 201
    invalid = client.post("/api/participants/PRACTICE/practice/1", json={
        "defaultPrediction": "release", "finalAction": "release",
    })
    assert invalid.status_code == 400

    practice = client.get("/api/practice/1").json()
    valid = client.post("/api/participants/PRACTICE/practice/1", json={
        "defaultPrediction": practice["defaultPredictionOptions"][0],
        "finalAction": "release",
    })
    assert valid.status_code == 200
    feedback = valid.json()["feedback"]
    assert feedback.startswith("第1题：回答")
    assert "\n\n第2题：回答" in feedback
    assert "\n\n第3题：回答" not in feedback
    assert "该题只比较同一调度方案" not in feedback
    assert "可回放三种方案" not in feedback
    assert "三种方案的最终飞行时间" in feedback
    assert "simulation" not in valid.json()


def test_practice_payload_never_mutates_authoritative_routes(tmp_path: Path):
    data = load_experiment_data()
    before = {
        (route_set_id, path_id): [(point.x, point.y) for point in route.waypoints]
        for route_set_id, routes in data.routes.items()
        for path_id, route in routes.items()
    }
    client = TestClient(create_app(db_path=tmp_path / "practice-copy.db"))
    first = client.get("/api/practice/1").json()
    second = client.get("/api/practice/1").json()
    assert first["routes"] == second["routes"]

    # A fresh load must still match after repeated practice payload generation.
    after_data = load_experiment_data()
    after = {
        (route_set_id, path_id): [(point.x, point.y) for point in route.waypoints]
        for route_set_id, routes in after_data.routes.items()
        for path_id, route in routes.items()
    }
    assert after == before


def test_api_flow(tmp_path: Path):
    client = TestClient(create_app(db_path=tmp_path / "test.db"))
    homepage = client.get("/")
    assert homepage.status_code == 200
    assert "UAV" in homepage.text
    health = client.get("/api/health")
    assert health.status_code == 200
    assert health.json()["trialCount"] == 18
    trials = client.get("/api/trials").json()
    assert len(trials) == 18
    detail = client.get("/api/trials/1").json()
    assert len(detail["windLayers"]) == 3
    assert len(detail["initialDrones"]) == detail["droneCount"]
    assert "plannedBestAction" not in detail
    assert "groundTruth" not in detail
    assert "complexity" not in detail
    assert detail["droneCount"] == 1
    assert detail["noWindBenchmarks"]["delay"]["startTimeSeconds"] == 120
    for benchmark in detail["noWindBenchmarks"].values():
        assert benchmark["flightTimeSeconds"] > 0
        assert benchmark["arrivalTimeSeconds"] == pytest.approx(
            benchmark["startTimeSeconds"] + benchmark["flightTimeSeconds"]
        )
        assert {interval["zoneId"] for interval in benchmark["zoneIntervals"]} == {
            "W1", "W2",
        }
    assert detail["routeBaselineTimeSeconds"]["Path-A"] == pytest.approx(143.8, abs=0.2)
    assert detail["routeBaselineTimeSeconds"]["Path-B"] == pytest.approx(186.5, abs=0.2)
    assert {route["taskRole"] for route in detail["routes"]} == {"focused_candidate"}
    assert {route["pathId"] for route in detail["routes"]} == {"Path-A", "Path-B"}
    unique_points = {(p["x"], p["y"]) for route in detail["routes"] for p in route["waypoints"]}
    assert len(unique_points) >= 2
    response = client.post("/api/simulations", json={"trialId": 2, "action": "delay"})
    assert response.status_code == 200
    body = response.json()
    assert body["frames"][-1]["status"] == "finished"
    expected = Simulator(load_experiment_data()).run(
        2, "delay", wind_mode="forecast",
    )
    assert body["summary"]["airborneTime"] == pytest.approx(expected.airborne_time)
    assert client.post("/api/events", json={
        "eventType": "test_event", "sessionId": body["sessionId"], "trialId": 1,
    }).status_code == 204


def test_api_rejects_bad_requests(tmp_path: Path):
    client = TestClient(create_app(db_path=tmp_path / "test.db"))
    assert client.get("/api/trials/999").status_code == 404
    assert client.post("/api/simulations", json={"trialId": 1, "action": "bad"}).status_code == 422


def test_api_displays_forecasts_and_keeps_future_realizations_hidden(tmp_path: Path):
    data = load_experiment_data()
    simulator = Simulator(data)
    client = TestClient(create_app(db_path=tmp_path / "wind-consistency.db"))
    displayed = client.get("/api/trials/1").json()["windLayers"]
    forecast = simulator.forecast_winds(1)
    actual = simulator.actual_winds(1)
    for layer_payload in displayed:
        layer = layer_payload["layer"]
        for zone_payload, (_, wx, wy) in zip(layer_payload["zones"], forecast[layer]):
            assert zone_payload["meanSpeed"] == pytest.approx(math.hypot(wx, wy))
            assert zone_payload["directionDeg"] == pytest.approx(math.degrees(math.atan2(wy, wx)) % 360)
            assert zone_payload["shapeSeed"] == (
                1 * 17 + layer * 11 + sum(map(ord, zone_payload["zoneId"]))
            ) % 31
            assert zone_payload["isForecast"] is (layer > 0)
            if layer > 0:
                assert zone_payload["speedSD"] > 0
    assert actual[0][0][1:] == pytest.approx(forecast[0][0][1:])
    assert actual[1][0][1:] != pytest.approx(forecast[1][0][1:])


def test_participant_flow_uses_fixed_content_and_randomized_order(tmp_path: Path):
    client = TestClient(create_app(db_path=tmp_path / "experiment.db"))
    created = client.post("/api/participants", json={"participantId": "P001"})
    assert created.status_code == 201
    assert created.json()["orderId"] == 1
    assert client.post("/api/participants", json={"participantId": "P001"}).status_code == 409
    assert client.post("/api/events", json={
        "eventType": "trial_entered", "participantId": "P001", "trialId": 1,
        "eventValue": {"block": 1}, "clientTimeMs": 100.0,
    }).status_code == 204
    assert client.get("/api/participants/P001/next-trial").status_code == 409
    assert client.post("/api/participants/P001/trials/1/simulation", json={
        "action": "release",
    }).status_code == 409
    comprehension = client.post("/api/participants/P001/comprehension", json={
        "answers": {
            "wind": "B", "layers": "C", "delay": "A", "goal": "B",
            "flight_layers": "C", "wind_effect": "B",
        },
    })
    assert comprehension.json()["passed"] is True
    assert comprehension.json()["correctCount"] == 6
    assert comprehension.json()["total"] == 6
    for practice_no in range(1, 5):
        practice = client.get(f"/api/practice/{practice_no}")
        assert practice.status_code == 200
        body = practice.json()
        assert set(body["defaultPredictionOptions"]) == {"更短", "更长"}
        assert "基本不变" not in body["defaultPredictionOptions"]
        assert set(body["noWindBenchmarks"]) == {"release", "delay", "reroute"}
        points = {(p["x"], p["y"]) for route in body["routes"] for p in route["waypoints"]}
        assert 8 <= len(points) <= 12
        simulation = client.post(f"/api/practice/{practice_no}/simulation", json={
            "action": "delay",
        })
        assert simulation.status_code == 200
        assert simulation.json()["frames"][0]["time"] == pytest.approx(120.0)
        assert simulation.json()["frames"][0]["actualWindLayer"] == 1
        assert simulation.json()["frames"][-1]["status"] == "finished"
        practice_action = "delay" if practice_no == 1 else "release"
        response = client.post(f"/api/participants/P001/practice/{practice_no}", json={
            "defaultPrediction": body["defaultPredictionOptions"][0],
            "finalAction": practice_action,
        })
        assert response.status_code == 200
        assert "feedback" in response.json()
        assert "simulation" not in response.json()
        if practice_no == 1:
            assert "原因提示" in response.json()["feedback"]
        else:
            assert "原因提示" not in response.json()["feedback"]

    seen = []
    timed_out_trial_id = None
    for trial_index in range(18):
        next_response = client.get("/api/participants/P001/next-trial")
        assert next_response.status_code == 200
        trial = next_response.json()
        assert not trial["complete"]
        assert "predictability" not in trial
        assert "intervenability" not in trial
        assert "plannedBestAction" not in trial
        assert "groundTruth" not in trial
        assert trial["intermediateOptions"]
        assert "intermediateCorrectAnswer" not in trial
        assert set(trial["noWindBenchmarks"]) == {"release", "delay", "reroute"}
        trial_id = trial["trialId"]
        seen.append(trial_id)
        payload = {
            "trialId": trial_id, "defaultPrediction": trial["defaultPredictionOptions"][0], "predictionRtMs": 1000,
            "intermediateAnswer": trial["intermediateOptions"][0], "intermediateRtMs": 800,
            "finalAction": "release", "decisionRtMs": 500,
            "perceivedUncertainty": 4, "perceivedIntervenability": 4,
            "flightTimeConfidence": 4, "actionChoiceConfidence": 4,
            "ratingRtMs": 600, "trialStartedAt": "2026-08-11T00:00:00Z",
            "layerDwellMs": [100, 200, 200], "layerSwitchCount": 2,
            "layerSequence": [0, 1, 2], "firstFutureLayerOpenedMs": 100,
            "predictionChangeCount": 1, "intermediateChangeCount": 2,
            "decisionChangeCount": 1,
            "timedOut": trial_index == 0,
        }
        if trial_index == 0:
            timed_out_trial_id = trial_id
            payload["decisionRtMs"] = 95_500
        if trial_index == 1:
            incomplete = dict(payload)
            incomplete.pop("perceivedUncertainty")
            assert client.post(
                f"/api/participants/P001/trials/{trial_id}/submit", json=incomplete,
            ).status_code == 422
            assert client.post(
                f"/api/participants/P001/trials/{trial_id}/simulation",
                json={"action": "delay"},
            ).status_code == 409
        submitted = client.post(
            f"/api/participants/P001/trials/{trial_id}/submit", json=payload,
        )
        assert submitted.status_code == 200
        formal_body = submitted.json()
        assert formal_body["action"] == "release"
        assert formal_body["frames"][-1]["status"] == "finished"
        assert set(formal_body["actionTimes"]) == {"release", "delay", "reroute"}
        if trial_index == 1:
            assert formal_body["frames"][-1]["status"] == "finished"
            actual_winds = Simulator(load_experiment_data()).actual_winds(trial_id)
            for layer_payload in formal_body["actualWindLayers"]:
                layer = layer_payload["layer"]
                for zone_payload, (_, wx, wy) in zip(
                    layer_payload["zones"], actual_winds[layer],
                ):
                    assert zone_payload["meanSpeed"] == pytest.approx(math.hypot(wx, wy))
                    assert zone_payload["speedSD"] == 0
                    assert zone_payload["directionSD"] == 0
                    assert zone_payload["isForecast"] is False
            expected_actual = Simulator(load_experiment_data()).run(
                trial_id, "release", wind_mode="actual",
            )
            assert formal_body["summary"]["airborneTime"] == pytest.approx(
                expected_actual.airborne_time
            )
            for action in ("release", "delay", "reroute"):
                expected_action = Simulator(load_experiment_data()).run(
                    trial_id, action, include_frames=False, wind_mode="actual",
                )
                assert formal_body["actionTimes"][action] == pytest.approx(
                    expected_action.airborne_time
                )
        assert client.post(
            f"/api/participants/P001/trials/{trial_id}/submit", json=payload
        ).status_code == 409

    assert set(seen) == set(range(1, 19))
    assert client.get("/api/participants/P001/next-trial").json() == {"complete": True}
    for block in (1, 2, 3):
        assert client.post(f"/api/participants/P001/blocks/{block}/ratings", json={
            "mentalEffort": 4, "fatigue": 3, "taskDifficulty": 4,
            "mentalSimulationStrategy": 4, "multiActionComparisonStrategy": 4,
            "simpleCueStrategy": 4,
        }).status_code == 204
    assert client.post("/api/participants/P001/complete", json={
        "questionnaire": {"major": "工程与技术",
                          "uavExperience": "none", "aviationCourse": "some",
                          "systemManagementExperience": "some", "gameExperience": "none", "mapAbility": 5,
                          "sustainedAttention": 4, "overallEffort": 4,
                          "scenarioComprehension": 4, "windRouteReliance": 4,
                          "windRelationImportance": 4, "mentalSimulationUse": 4,
                          "perceivedRealism": 4, "uncertaintyAnalysisEffort": 4,
                          "intervenabilityComparisonEffort": 4,
                          "simulationTriggerSelections": [
                              "未来风场的变化范围较大时", "第一眼看不出哪个方案更合适时",
                          ],
                          "simulationTriggerOther": "",
                          "decisionInformationSelections": [
                              "风速大小", "风向与飞行路线的相对关系", "无风benchmark",
                          ],
                          "decisionInformationOther": ""},
    }).status_code == 204
    state = client.get("/api/participants/P001/state").json()
    assert state["status"] == "completed"
    assert state["completedTrials"] == 18
    export = client.get("/api/admin/export/trials.csv")
    assert export.status_code == 200
    assert len(export.text.strip().splitlines()) == 19
    header = export.text.splitlines()[0]
    assert "objective_uncertainty" in header
    assert "objective_predictability" in header
    assert "background_drone_count" not in header
    assert "objective_complexity" not in header
    assert "best_time_action" in header
    assert "simulation_action_alignment" in header
    assert "default_prediction_correct" in header
    assert "default_prediction_difference_s" in header
    assert "default_prediction_difference_pct" in header
    assert "timed_out" in header
    assert "selected_option" in header
    assert "actual_release_time_s" in header
    assert "actual_delay_time_s" in header
    assert "actual_reroute_time_s" in header
    assert "forecast_selected_time_s" in header
    assert "decision_regret_seconds" in header
    assert "normalized_decision_regret" in header
    assert "layer_t0_dwell_ms" in header
    assert "mental_simulation_strategy" in header
    assert "fatigue" in header
    exported_rows = {
        int(row["trial_id"]): row
        for row in csv.DictReader(io.StringIO(export.text))
    }
    assert timed_out_trial_id is not None
    assert exported_rows[timed_out_trial_id]["timed_out"] == "1"
    assert float(exported_rows[timed_out_trial_id]["decision_rt_ms"]) > 90_000
    assert exported_rows[timed_out_trial_id]["intermediate_answer"]
    assert exported_rows[timed_out_trial_id]["perceived_uncertainty"] == "4"
    assert exported_rows[timed_out_trial_id]["perceived_intervenability"] == "4"
    assert exported_rows[timed_out_trial_id]["flight_time_confidence"] == "4"
    assert exported_rows[timed_out_trial_id]["action_choice_confidence"] == "4"
    assert exported_rows[timed_out_trial_id]["layer_sequence_json"] == "[0, 1, 2]"
    assert exported_rows[timed_out_trial_id]["actual_release_time_s"]
    assert exported_rows[timed_out_trial_id]["selected_option"] == "release"
    data = load_experiment_data()
    regrets = []
    for trial_id, row in exported_rows.items():
        expected_accuracy = int(data.ground_truth[trial_id].best_time_action == "release")
        assert row["optimal_action_accuracy"] == str(expected_accuracy)
        truth = data.ground_truth[trial_id]
        path_a_baseline = data.routes[data.trials[trial_id].route_set_id]["Path-A"]
        path_a_length = sum(
            math.hypot(end.x-start.x, end.y-start.y)
            for start, end in zip(path_a_baseline.waypoints, path_a_baseline.waypoints[1:])
        )
        baseline_time = path_a_length / float(data.parameters["vAir_mps"])
        expected_prediction_difference = truth.release_time_s - baseline_time
        assert float(row["default_prediction_difference_s"]) == pytest.approx(
            expected_prediction_difference
        )
        assert float(row["default_prediction_difference_pct"]) == pytest.approx(
            expected_prediction_difference / baseline_time * 100.0
        )
        forecast_times = [truth.release_time_s, truth.delay_time_s, truth.reroute_time_s]
        assert float(row["forecast_selected_time_s"]) == pytest.approx(truth.release_time_s)
        assert float(row["forecast_best_time_s"]) == pytest.approx(min(forecast_times))
        expected_regret = truth.release_time_s - min(forecast_times)
        regrets.append(expected_regret)
        assert float(row["decision_regret_seconds"]) == pytest.approx(expected_regret)
        assert float(row["normalized_decision_regret"]) == pytest.approx(
            expected_regret / min(forecast_times)
        )
    assert any(regret == pytest.approx(0) for regret in regrets)
    assert any(regret > 0 for regret in regrets)
    questionnaire_export = client.get("/api/admin/export/questionnaires.csv")
    questionnaire_row = next(csv.DictReader(io.StringIO(questionnaire_export.text)))
    assert questionnaire_row["questionnaire_version"] == "2026-09-measures-v7"
    assert questionnaire_row["time_pressure"] == ""
    assert questionnaire_row["age"] == ""
    assert questionnaire_row["gender"] == ""
    assert questionnaire_row["major"] == "工程与技术"
    assert questionnaire_row["system_management_experience"] == "some"
    assert questionnaire_row["wind_relation_importance"] == "4"
    assert questionnaire_row["perceived_realism"] == "4"
    assert questionnaire_row["uncertainty_analysis_effort"] == "4"
    assert questionnaire_row["intervenability_comparison_effort"] == "4"
    assert json.loads(questionnaire_row["simulation_trigger_selections"]) == [
        "未来风场的变化范围较大时", "第一眼看不出哪个方案更合适时",
    ]
    assert questionnaire_row["simulation_trigger_other"] == ""
    assert json.loads(questionnaire_row["decision_information_selections"]) == [
        "风速大小", "风向与飞行路线的相对关系", "无风benchmark",
    ]
    assert questionnaire_row["decision_information_other"] == ""
    assert questionnaire_row["strategy"] == ""
    assert questionnaire_row["additional_information"] == ""
    event_export = client.get("/api/admin/export/events.csv")
    assert "participant_id" in event_export.text.splitlines()[0]
    assert "P001" in event_export.text
