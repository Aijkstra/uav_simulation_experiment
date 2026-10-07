"""Pre-pilot material audit. Run with: python -m uav_sim.audit"""

import json
import math
from collections import Counter
from collections import defaultdict

from .api import default_prediction_spec, intermediate_spec
from .config import (
    ANGLE_CLASS_CENTERS_DEG,
    ANGLE_CLASS_NAMES,
    DECISION_GAP_RANGE_PCT,
    HIGH_INTERVENABILITY_RANGE_PCT,
    LOW_INTERVENABILITY_RANGE_PCT,
    MAX_VISIBLE_WAYPOINTS,
    MIN_VISIBLE_WAYPOINTS,
    WIND_ZONE_ROUTE_FRACTIONS,
    _point_on_route,
    _relative_angle,
    load_experiment_data,
)
from .simulator import Simulator


def build_audit() -> dict:
    data = load_experiment_data()
    errors: list[str] = []
    warnings: list[str] = []
    condition_counts = Counter((t.predictability, t.intervenability) for t in data.trials.values())
    factorial_counts = Counter((t.predictability, t.intervenability) for t in data.trials.values())
    drone_counts = Counter(t.drone_count for t in data.trials.values())
    prediction_answer_counts = Counter(
        default_prediction_spec(data, trial)["answer"]
        for trial in data.trials.values()
    )
    prediction_answer_counts_by_block = {
        block: Counter(
            default_prediction_spec(data, trial)["answer"]
            for trial in data.trials.values() if trial.block == block
        )
        for block in (1, 2, 3)
    }
    block_counts = Counter(t.block for t in data.trials.values())
    block_conditions = {
        block: Counter((t.predictability, t.intervenability)
                       for t in data.trials.values() if t.block == block)
        for block in (1, 2, 3)
    }
    action_counts = Counter(gt.best_time_action for gt in data.ground_truth.values())
    waypoint_counts = {}
    question_counts = Counter()
    answer_positions = defaultdict(Counter)
    simulator = Simulator(data)
    simulation_action_counts = Counter()
    simulation_decision_gaps = {}
    simulation_intervenability = {}
    realized_action_counts = Counter()
    realized_decision_gaps = {}
    realized_intervenability = {}
    realized_actions_by_cell = defaultdict(set)
    action_angle_counts = Counter()
    for trial in data.trials.values():
        routes = data.routes[trial.route_set_id]
        points = {(round(p.x, 6), round(p.y, 6)) for route in routes.values() for p in route.waypoints}
        waypoint_counts[trial.trial_id] = len(points)
        if not MIN_VISIBLE_WAYPOINTS <= len(points) <= MAX_VISIBLE_WAYPOINTS:
            errors.append(f"Trial {trial.trial_id}: waypoint count {len(points)}")
        question = intermediate_spec(data, trial, simulator)
        question_counts[trial.intermediate_question] += 1
        answer_positions[trial.intermediate_question][
            question["options"].index(question["answer"]) + 1
        ] += 1
        minimum_options = 3
        if (question["answer"] not in question["options"]
                or len(set(question["options"])) < minimum_options):
            errors.append(f"Trial {trial.trial_id}: invalid intermediate question")
        action_times = {
            action: simulator.run(
                trial.trial_id, action, include_frames=False, wind_mode="forecast",
            ).airborne_time
            for action in ("release", "delay", "reroute")
        }
        ranked = sorted(action_times, key=action_times.get)
        simulation_action_counts[ranked[0]] += 1
        simulation_decision_gaps[trial.trial_id] = (
            action_times[ranked[1]] - action_times[ranked[0]]
        ) / action_times[ranked[0]] * 100.0
        simulation_intervenability[trial.trial_id] = (
            action_times[ranked[-1]] - action_times[ranked[0]]
        ) / action_times[ranked[-1]] * 100.0
        _, _, heading = _point_on_route(
            routes[trial.release_path], WIND_ZONE_ROUTE_FRACTIONS[0],
        )
        reference_zone = next(
            zone for zone in data.wind_zones[trial.trial_id][0] if zone.zone_id == "W1"
        )
        angle = _relative_angle(reference_zone.direction_deg, heading)
        angle_class = ANGLE_CLASS_NAMES[min(
            range(len(ANGLE_CLASS_CENTERS_DEG)),
            key=lambda index: abs(angle - ANGLE_CLASS_CENTERS_DEG[index]),
        )]
        action_angle_counts[(trial.planned_best_action, angle_class)] += 1
        if ranked[0] != trial.planned_best_action:
            errors.append(
                f"Trial {trial.trial_id}: planned {trial.planned_best_action}, "
                f"simulation supports {ranked[0]}"
            )
        realized_winds = simulator.actual_winds(trial.trial_id)
        for layer in (1, 2):
            for zone, wx, wy in realized_winds[layer]:
                realized_speed = math.hypot(wx, wy)
                realized_direction = math.degrees(math.atan2(wy, wx)) % 360.0
                direction_error = abs(
                    (realized_direction - zone.direction_deg + 180.0) % 360.0 - 180.0
                )
                if abs(realized_speed - zone.mean_speed) > zone.speed_sd + 1e-9:
                    errors.append(
                        f"Trial {trial.trial_id} L{layer} {zone.zone_id}: realized speed "
                        "falls outside the displayed range"
                    )
                if direction_error > zone.direction_sd + 1e-9:
                    errors.append(
                        f"Trial {trial.trial_id} L{layer} {zone.zone_id}: realized direction "
                        "falls outside the displayed range"
                    )
        realized_times = {
            action: simulator.run(
                trial.trial_id, action, include_frames=False, wind_mode="actual",
            ).airborne_time
            for action in ("release", "delay", "reroute")
        }
        realized_ranked = sorted(realized_times, key=realized_times.get)
        realized_best, realized_second, realized_worst = realized_ranked
        realized_action_counts[realized_best] += 1
        realized_actions_by_cell[(trial.predictability, trial.intervenability)].add(
            realized_best
        )
        realized_decision_gaps[trial.trial_id] = (
            realized_times[realized_second] - realized_times[realized_best]
        ) / realized_times[realized_best] * 100.0
        realized_intervenability[trial.trial_id] = (
            realized_times[realized_worst] - realized_times[realized_best]
        ) / realized_times[realized_worst] * 100.0
        if realized_best != trial.planned_best_action:
            errors.append(
                f"Trial {trial.trial_id}: planned {trial.planned_best_action}, "
                f"formal realization supports {realized_best}"
            )
        prediction = default_prediction_spec(data, trial)
        prediction_time = data.ground_truth[trial.trial_id].time_for(
            prediction["referenceAction"]
        )
        prediction_difference = (
            prediction_time - prediction["baselineTimeSeconds"]
        ) / prediction["baselineTimeSeconds"]
        expected_prediction = (
            "更短" if prediction_difference < -0.05
            else "更长" if prediction_difference > 0.05
            else "基本不变"
        )
        if prediction["referenceAction"] != "release" or prediction["answer"] != expected_prediction:
            errors.append(
                f"Trial {trial.trial_id}: invalid fixed Path-A prediction answer"
            )
    if len(data.trials) != 18:
        errors.append("Formal trial count is not 18")
    if block_counts != Counter({1: 6, 2: 6, 3: 6}):
        errors.append(f"Invalid block counts: {dict(block_counts)}")
    expected_conditions = Counter({
        (predictability, intervenability): 3
        for predictability in ("H", "M", "L")
        for intervenability in ("H", "L")
    })
    if condition_counts != expected_conditions:
        errors.append(f"Invalid condition counts: {dict(condition_counts)}")
    if len(factorial_counts) != 6 or set(factorial_counts.values()) != {3}:
        errors.append(f"Invalid 3x2 counts: {dict(factorial_counts)}")
    if drone_counts != Counter({1: 18}):
        errors.append(f"Only the UAV should be present: {dict(drone_counts)}")
    for cell in factorial_counts:
        cell_trials = [
            trial for trial in data.trials.values()
            if (trial.predictability, trial.intervenability) == cell
        ]
        if {data.ground_truth[trial.trial_id].best_time_action for trial in cell_trials} != {
            "release", "delay", "reroute",
        }:
            errors.append(f"Cell {cell}: best actions are not balanced")
        if {trial.intermediate_question for trial in cell_trials} != {
            "第120秒时所在航段",
        }:
            errors.append(f"Cell {cell}: process question is not fixed")
    for block, counts in block_conditions.items():
        if any(counts[condition] != 1 for condition in expected_conditions):
            errors.append(f"Block {block}: conditions are not 1 each")
    if action_counts != Counter({"release": 6, "delay": 6, "reroute": 6}):
        errors.append(f"Invalid best action balance: {dict(action_counts)}")
    if simulation_action_counts != Counter({"release": 6, "delay": 6, "reroute": 6}):
        errors.append(f"Invalid simulation action balance: {dict(simulation_action_counts)}")
    if realized_action_counts != Counter({"release": 6, "delay": 6, "reroute": 6}):
        errors.append(f"Invalid formal-realization action balance: {dict(realized_action_counts)}")
    for cell, actions in realized_actions_by_cell.items():
        if actions != {"release", "delay", "reroute"}:
            errors.append(f"Cell {cell}: formal-realization best actions are not balanced")
    if max(prediction_answer_counts.values()) - min(prediction_answer_counts.values()) > 2:
        warnings.append(
            f"Default prediction answers are imbalanced: {dict(prediction_answer_counts)}"
        )
    for block, counts in prediction_answer_counts_by_block.items():
        if max(counts.values()) - min(counts.values()) > 2:
            warnings.append(
                f"Block {block}: default prediction answers are imbalanced: {dict(counts)}"
            )
    for trial in data.trials.values():
        truth = data.ground_truth[trial.trial_id]
        low, high = (
            HIGH_INTERVENABILITY_RANGE_PCT
            if trial.intervenability == "H"
            else LOW_INTERVENABILITY_RANGE_PCT
        )
        if not low <= truth.intervenability_pct <= high:
            errors.append(
                f"Trial {trial.trial_id}: intervenability {truth.intervenability_pct:.2f}%"
            )
        if not DECISION_GAP_RANGE_PCT[0] <= truth.decision_gap_pct <= DECISION_GAP_RANGE_PCT[1]:
            errors.append(f"Trial {trial.trial_id}: decision gap {truth.decision_gap_pct:.2f}%")
        realized_gap = realized_decision_gaps[trial.trial_id]
        realized_control = realized_intervenability[trial.trial_id]
        if not DECISION_GAP_RANGE_PCT[0] <= realized_gap <= DECISION_GAP_RANGE_PCT[1]:
            errors.append(
                f"Trial {trial.trial_id}: formal-realization decision gap "
                f"{realized_gap:.2f}%"
            )
        if not low <= realized_control <= high:
            errors.append(
                f"Trial {trial.trial_id}: formal-realization intervenability "
                f"{realized_control:.2f}%"
            )
    if max(action_angle_counts.values()) - min(action_angle_counts.values()) > 1:
        errors.append(f"Best-action/reference-angle pairing is imbalanced: {dict(action_angle_counts)}")
    for question_type, positions in answer_positions.items():
        counts = [positions[position] for position in range(1, max(positions) + 1)]
        if max(counts) - min(counts) > 1:
            errors.append(f"{question_type}: answer positions are imbalanced: {dict(positions)}")
    return {
        "status": "pass" if not errors else "fail",
        "errors": errors,
        "warnings": warnings,
        "formalTrialCount": len(data.trials),
        "blockCounts": dict(sorted(block_counts.items())),
        "conditionCounts": {"".join(key): value for key, value in sorted(condition_counts.items())},
        "factorialCellCounts": {"-".join(key): value for key, value in sorted(factorial_counts.items())},
        "droneCounts": dict(sorted(drone_counts.items())),
        "defaultPredictionAnswerCounts": dict(sorted(prediction_answer_counts.items())),
        "defaultPredictionAnswerCountsByBlock": {
            str(block): dict(sorted(counts.items()))
            for block, counts in prediction_answer_counts_by_block.items()
        },
        "bestActionCounts": dict(sorted(action_counts.items())),
        "simulationBestActionCounts": dict(sorted(simulation_action_counts.items())),
        "formalRealizationBestActionCounts": dict(sorted(realized_action_counts.items())),
        "simulationIntervenabilityPctRange": [
            min(simulation_intervenability.values()), max(simulation_intervenability.values()),
        ],
        "simulationDecisionGapPctRange": [
            min(simulation_decision_gaps.values()), max(simulation_decision_gaps.values()),
        ],
        "formalRealizationIntervenabilityPctRange": [
            min(realized_intervenability.values()), max(realized_intervenability.values()),
        ],
        "formalRealizationDecisionGapPctRange": [
            min(realized_decision_gaps.values()), max(realized_decision_gaps.values()),
        ],
        "bestActionReferenceAngleCounts": {
            f"{action}-{angle_class}": count
            for (action, angle_class), count in sorted(action_angle_counts.items())
        },
        "waypointCountRange": [min(waypoint_counts.values()), max(waypoint_counts.values())],
        "waypointCounts": waypoint_counts,
        "intermediateQuestionCounts": dict(question_counts),
        "intermediateAnswerPositions": {
            question_type: dict(sorted(positions.items()))
            for question_type, positions in answer_positions.items()
        },
    }


def main() -> None:
    report = build_audit()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["status"] != "pass":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
