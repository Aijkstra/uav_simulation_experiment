import sqlite3
from collections import Counter

from uav_sim.config import load_experiment_data
from uav_sim.database import EventStore


def test_two_mirrored_orders_preserve_content_and_block_balance(tmp_path):
    data = load_experiment_data()
    trials_by_block = {}
    for trial in data.trials.values():
        trials_by_block.setdefault(trial.block, []).append(trial.trial_id)
    path = tmp_path / "orders.db"
    store = EventStore(path)
    for number in range(1, 5):
        created = store.create_participant(f"P{number:03d}", trials_by_block)
        assert created["orderId"] == (number - 1) % 2 + 1
    with sqlite3.connect(path) as connection:
        orders = []
        for number in range(1, 5):
            participant_id = f"P{number:03d}"
            rows = connection.execute(
                "SELECT trial_id, block FROM trial_assignments "
                "WHERE participant_id=? ORDER BY global_order", (participant_id,),
            ).fetchall()
            assert {trial_id for trial_id, _ in rows} == set(range(1, 19))
            for block in (1, 2, 3):
                trial_ids = [trial_id for trial_id, assigned_block in rows if assigned_block == block]
                conditions = Counter(
                    (data.trials[trial_id].predictability, data.trials[trial_id].intervenability)
                    for trial_id in trial_ids
                )
                assert len(trial_ids) == 6
                assert set(conditions.values()) == {1}
            orders.append(tuple(trial_id for trial_id, _ in rows))
        assert orders[0] == orders[2]
        assert orders[1] == orders[3]
        assert len(set(orders)) == 2
        for block_index in range(3):
            start = block_index * 6
            assert orders[1][start:start + 6] == tuple(reversed(orders[0][start:start + 6]))


def test_initialize_migrates_legacy_measurement_tables_without_rewriting_old_rows(tmp_path):
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as connection:
        connection.executescript("""
            CREATE TABLE interaction_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, participant_id TEXT,
                trial_id INTEGER, event_type TEXT NOT NULL, event_value TEXT,
                client_time_ms REAL, created_at TEXT NOT NULL
            );
            CREATE TABLE participants (
                participant_id TEXT PRIMARY KEY, experiment_version TEXT NOT NULL,
                material_version TEXT NOT NULL, order_id INTEGER NOT NULL,
                status TEXT NOT NULL, started_at TEXT NOT NULL, completed_at TEXT
            );
            CREATE TABLE trial_responses (
                participant_id TEXT NOT NULL, trial_id INTEGER NOT NULL, predicted_best_action TEXT NOT NULL,
                prediction_rt_ms REAL NOT NULL, intermediate_answer TEXT NOT NULL,
                intermediate_correct INTEGER, intermediate_rt_ms REAL NOT NULL, final_action TEXT NOT NULL,
                decision_rt_ms REAL NOT NULL, perceived_predictability INTEGER NOT NULL,
                perceived_intervenability INTEGER NOT NULL, rating_rt_ms REAL NOT NULL,
                prediction_decision_consistency INTEGER NOT NULL, optimal_action_accuracy INTEGER NOT NULL,
                trial_started_at TEXT NOT NULL, trial_submitted_at TEXT NOT NULL,
                PRIMARY KEY(participant_id, trial_id)
            );
            CREATE TABLE block_ratings (
                participant_id TEXT NOT NULL, block INTEGER NOT NULL, mental_effort INTEGER NOT NULL,
                fatigue INTEGER NOT NULL, task_difficulty INTEGER NOT NULL, created_at TEXT NOT NULL,
                PRIMARY KEY(participant_id, block)
            );
            CREATE TABLE questionnaires (
                participant_id TEXT PRIMARY KEY, answers_json TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE TABLE practice_responses (
                participant_id TEXT NOT NULL, practice_no INTEGER NOT NULL, predicted_action TEXT NOT NULL,
                final_action TEXT NOT NULL, correct INTEGER NOT NULL, created_at TEXT NOT NULL,
                PRIMARY KEY(participant_id, practice_no)
            );
        """)
        connection.execute(
            "INSERT INTO participants VALUES ('LEGACY', '4.1', 'old-material', 1, 'completed', 't', 't')"
        )
        connection.execute(
            "INSERT INTO questionnaires VALUES ('LEGACY', ?, 't')",
            ('{"uavExperience":"some","mapAbility":5}',),
        )

    store = EventStore(path)
    with sqlite3.connect(path) as connection:
        participant_columns = {row[1] for row in connection.execute("PRAGMA table_info(participants)")}
        response_columns = {row[1] for row in connection.execute("PRAGMA table_info(trial_responses)")}
        block_columns = {row[1] for row in connection.execute("PRAGMA table_info(block_ratings)")}
        questionnaire_columns = {row[1] for row in connection.execute("PRAGMA table_info(questionnaires)")}
        assert "questionnaire_version" in participant_columns
        assert {"perceived_uncertainty", "flight_time_confidence", "action_choice_confidence",
                "layer_t0_dwell_ms", "decision_change_count",
                "default_prediction_difference_s",
                "default_prediction_difference_pct"} <= response_columns
        assert {"mental_simulation_strategy", "multi_action_comparison_strategy",
                "simple_cue_strategy"} <= block_columns
        assert "questionnaire_version" in questionnaire_columns
        assert connection.execute("SELECT answers_json FROM questionnaires WHERE participant_id='LEGACY'").fetchone()[0] == \
            '{"uavExperience":"some","mapAbility":5}'

    columns, rows = store.export_questionnaire_rows()
    exported = dict(zip(columns, rows[0]))
    assert exported["uav_experience"] == "some"
    assert exported["map_ability"] == 5
    assert exported["system_management_experience"] is None
