import math

import pytest

from uav_sim.api import default_prediction_spec
from uav_sim.config import (
    ANGLE_CLASS_CENTERS_DEG,
    ANGLE_CLASS_NAMES,
    DECISION_GAP_RANGE_PCT,
    HIGH_INTERVENABILITY_RANGE_PCT,
    HIGH_PREDICTABILITY_DIRECTION_SD_DEG,
    LOW_INTERVENABILITY_RANGE_PCT,
    LOW_PREDICTABILITY_DIRECTION_SD_DEG,
    MEDIUM_PREDICTABILITY_DIRECTION_SD_DEG,
    RANK_PRESERVING_REALIZATION_SEEDS,
    WIND_ZONE_ROUTE_FRACTIONS,
    _point_on_route,
    _relative_angle,
    load_experiment_data,
)
from uav_sim.simulator import Simulator


def test_workbook_is_complete():
    data = load_experiment_data()
    assert len(data.trials) == 18
    assert set(data.wind_zones[1]) == {0, 1, 2}
    assert all(1 <= len(zones) <= 2 for zones in data.wind_zones[1].values())
    assert set(data.routes["RS01"]) == {"Path-A", "Path-B"}
    assert data.parameters["windLayer1Start_s"] == 120.0
    assert data.parameters["windLayer2Start_s"] == 240.0


def test_trials_reference_valid_routes():
    data = load_experiment_data()
    for trial in data.trials.values():
        assert trial.route_set_id in data.routes
        assert trial.release_path in data.routes[trial.route_set_id]
        assert trial.reroute_path in data.routes[trial.route_set_id]


def test_predictability_conditions_have_visually_distinct_direction_ranges():
    data = load_experiment_data()
    expected = {
        "H": HIGH_PREDICTABILITY_DIRECTION_SD_DEG,
        "M": MEDIUM_PREDICTABILITY_DIRECTION_SD_DEG,
        "L": LOW_PREDICTABILITY_DIRECTION_SD_DEG,
    }
    for trial in data.trials.values():
        for layer in (1, 2):
            assert {zone.direction_sd for zone in data.wind_zones[trial.trial_id][layer]} == {
                expected[trial.predictability]
            }
    assert (
        HIGH_PREDICTABILITY_DIRECTION_SD_DEG
        < MEDIUM_PREDICTABILITY_DIRECTION_SD_DEG
        < LOW_PREDICTABILITY_DIRECTION_SD_DEG
    )
    assert LOW_PREDICTABILITY_DIRECTION_SD_DEG >= 60


def test_each_trial_has_only_candidate_routes():
    data = load_experiment_data()
    for trial in data.trials.values():
        routes = data.routes[trial.route_set_id]
        points = {(round(p.x, 6), round(p.y, 6)) for route in routes.values() for p in route.waypoints}
        assert 8 <= len(points) <= 12
        assert set(routes) == {"Path-A", "Path-B"}
        assert routes[trial.release_path].task_role == "focused_candidate"


def test_fixed_ground_truth_is_complete_and_balanced():
    data = load_experiment_data()
    assert set(data.ground_truth) == set(data.trials)
    assert {action: sum(gt.best_time_action == action for gt in data.ground_truth.values())
            for action in ("release", "delay", "reroute")} == {
                "release": 6, "delay": 6, "reroute": 6,
            }
    assert all(gt.intervenability_pct > 0 for gt in data.ground_truth.values())
    assert all(gt.decision_gap_pct > 0 for gt in data.ground_truth.values())
    cells = {}
    for trial in data.trials.values():
        cells.setdefault(
            (trial.predictability, trial.intervenability), []
        ).append(trial)
    assert len(cells) == 6
    assert all(len(trials) == 3 for trials in cells.values())
    assert {trial.complexity for trial in data.trials.values()} == {"Fixed"}
    assert {trial.drone_count for trial in data.trials.values()} == {1}
    assert all(
        {data.ground_truth[t.trial_id].best_time_action for t in trials}
        == {"release", "delay", "reroute"}
        for trials in cells.values()
    )
    prediction_counts = {
        answer: sum(
            default_prediction_spec(data, trial)["answer"] == answer
            for trial in data.trials.values()
        )
        for answer in ("更短", "更长", "基本不变")
    }
    assert sum(prediction_counts.values()) == 18
    for trial_id, truth in data.ground_truth.items():
        condition = data.trials[trial_id].intervenability
        low, high = (
            HIGH_INTERVENABILITY_RANGE_PCT
            if condition == "H" else LOW_INTERVENABILITY_RANGE_PCT
        )
        assert low <= truth.intervenability_pct <= high
        assert DECISION_GAP_RANGE_PCT[0] <= truth.decision_gap_pct <= DECISION_GAP_RANGE_PCT[1]


def test_reference_wind_heading_classes_are_crossed_with_best_action():
    data = load_experiment_data()
    counts = {}
    for trial in data.trials.values():
        route = data.routes[trial.route_set_id][trial.release_path]
        _, _, heading = _point_on_route(route, WIND_ZONE_ROUTE_FRACTIONS[0])
        zone = next(
            zone for zone in data.wind_zones[trial.trial_id][0] if zone.zone_id == "W1"
        )
        angle = _relative_angle(zone.direction_deg, heading)
        angle_class = ANGLE_CLASS_NAMES[min(
            range(3), key=lambda index: abs(angle - ANGLE_CLASS_CENTERS_DEG[index]),
        )]
        key = (trial.planned_best_action, angle_class)
        counts[key] = counts.get(key, 0) + 1
    assert len(counts) == 9
    assert max(counts.values()) - min(counts.values()) <= 1


def test_ground_truth_is_generated_by_the_displayed_scene_simulator():
    data = load_experiment_data()
    simulator = Simulator(data)
    for trial_id, truth in data.ground_truth.items():
        results = {
            action: simulator.run(
                trial_id, action, include_frames=False, wind_mode="forecast",
            )
            for action in ("release", "delay", "reroute")
        }
        assert truth.release_time_s == pytest.approx(results["release"].airborne_time)
        assert truth.delay_time_s == pytest.approx(results["delay"].airborne_time)
        assert truth.reroute_time_s == pytest.approx(results["reroute"].airborne_time)
        assert truth.best_time_action == min(results, key=lambda action: results[action].airborne_time)


def test_formal_realizations_preserve_planned_best_actions_and_displayed_ranges():
    data = load_experiment_data()
    simulator = Simulator(data)
    assert set(RANK_PRESERVING_REALIZATION_SEEDS) == set(data.trials)
    realized_best_counts = {action: 0 for action in ("release", "delay", "reroute")}
    realized_by_cell = {}
    for trial in data.trials.values():
        assert trial.seed == RANK_PRESERVING_REALIZATION_SEEDS[trial.trial_id]
        for layer in (1, 2):
            for zone, wx, wy in simulator.actual_winds(trial.trial_id)[layer]:
                realized_speed = math.hypot(wx, wy)
                realized_direction = math.degrees(math.atan2(wy, wx)) % 360.0
                direction_error = abs(
                    (realized_direction - zone.direction_deg + 180.0) % 360.0 - 180.0
                )
                assert abs(realized_speed - zone.mean_speed) <= zone.speed_sd + 1e-9
                assert direction_error <= zone.direction_sd + 1e-9
        times = {
            action: simulator.run(
                trial.trial_id, action, include_frames=False, wind_mode="actual",
            ).airborne_time
            for action in ("release", "delay", "reroute")
        }
        ranked = sorted(times, key=times.get)
        best, second, worst = ranked
        decision_gap = (times[second] - times[best]) / times[best] * 100.0
        intervenability = (times[worst] - times[best]) / times[worst] * 100.0
        low, high = (
            HIGH_INTERVENABILITY_RANGE_PCT
            if trial.intervenability == "H" else LOW_INTERVENABILITY_RANGE_PCT
        )
        assert best == trial.planned_best_action
        assert DECISION_GAP_RANGE_PCT[0] <= decision_gap <= DECISION_GAP_RANGE_PCT[1]
        assert low <= intervenability <= high
        realized_best_counts[best] += 1
        realized_by_cell.setdefault(
            (trial.predictability, trial.intervenability), set()
        ).add(best)
    assert realized_best_counts == {"release": 6, "delay": 6, "reroute": 6}
    assert all(
        actions == {"release", "delay", "reroute"}
        for actions in realized_by_cell.values()
    )


def test_every_wind_field_is_anchored_on_the_release_route():
    data = load_experiment_data()
    for trial in data.trials.values():
        route = data.routes[trial.route_set_id][trial.release_path]
        for zones in data.wind_zones[trial.trial_id].values():
            for zone in zones:
                assert any(
                    _point_to_segment_distance(zone.cx, zone.cy, start.x, start.y, end.x, end.y) < 1e-6
                    for start, end in zip(route.waypoints, route.waypoints[1:])
                )


def _point_to_segment_distance(px, py, ax, ay, bx, by):
    dx, dy = bx - ax, by - ay
    ratio = max(0.0, min(1.0, ((px-ax)*dx + (py-ay)*dy) / max(dx*dx + dy*dy, 1e-9)))
    return ((px-(ax+ratio*dx))**2 + (py-(ay+ratio*dy))**2) ** 0.5
