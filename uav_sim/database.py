import json
import random
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional


DEFAULT_DB = Path(__file__).resolve().parents[1] / "data" / "uav_sim_3x2.db"


class EventStore:
    def __init__(self, path: Path = DEFAULT_DB):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.path), timeout=10)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS simulation_sessions (
                    session_id TEXT PRIMARY KEY,
                    trial_id INTEGER NOT NULL,
                    action TEXT NOT NULL,
                    seed INTEGER NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS interaction_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT,
                    participant_id TEXT,
                    trial_id INTEGER,
                    event_type TEXT NOT NULL,
                    event_value TEXT,
                    client_time_ms REAL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(session_id) REFERENCES simulation_sessions(session_id)
                );
                CREATE INDEX IF NOT EXISTS idx_events_session_created
                    ON interaction_events(session_id, created_at);
                CREATE INDEX IF NOT EXISTS idx_events_trial
                    ON interaction_events(trial_id);
                CREATE TABLE IF NOT EXISTS participants (
                    participant_id TEXT PRIMARY KEY,
                    experiment_version TEXT NOT NULL,
                    material_version TEXT NOT NULL,
                    questionnaire_version TEXT,
                    order_id INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'in_progress',
                    started_at TEXT NOT NULL,
                    completed_at TEXT
                );
                CREATE TABLE IF NOT EXISTS trial_assignments (
                    participant_id TEXT NOT NULL,
                    trial_id INTEGER NOT NULL,
                    block INTEGER NOT NULL,
                    position_in_block INTEGER NOT NULL,
                    global_order INTEGER NOT NULL,
                    PRIMARY KEY(participant_id, trial_id),
                    UNIQUE(participant_id, global_order),
                    FOREIGN KEY(participant_id) REFERENCES participants(participant_id)
                );
                CREATE TABLE IF NOT EXISTS trial_responses (
                    participant_id TEXT NOT NULL,
                    trial_id INTEGER NOT NULL,
                    predicted_best_action TEXT NOT NULL,
                    prediction_rt_ms REAL NOT NULL,
                    intermediate_answer TEXT NOT NULL,
                    intermediate_correct INTEGER,
                    intermediate_rt_ms REAL NOT NULL,
                    final_action TEXT NOT NULL,
                    decision_rt_ms REAL NOT NULL,
                    perceived_predictability INTEGER NOT NULL,
                    perceived_intervenability INTEGER NOT NULL,
                    rating_rt_ms REAL NOT NULL,
                    prediction_decision_consistency INTEGER NOT NULL,
                    optimal_action_accuracy INTEGER NOT NULL,
                    trial_started_at TEXT NOT NULL,
                    trial_submitted_at TEXT NOT NULL,
                    timed_out INTEGER NOT NULL DEFAULT 0,
                    default_prediction_difference_s REAL,
                    default_prediction_difference_pct REAL,
                    actual_release_time_s REAL,
                    actual_delay_time_s REAL,
                    actual_reroute_time_s REAL,
                    perceived_uncertainty INTEGER,
                    flight_time_confidence INTEGER,
                    action_choice_confidence INTEGER,
                    layer_t0_dwell_ms REAL,
                    layer_t1_dwell_ms REAL,
                    layer_t2_dwell_ms REAL,
                    layer_switch_count INTEGER,
                    layer_sequence_json TEXT,
                    first_future_layer_opened_ms REAL,
                    prediction_change_count INTEGER,
                    intermediate_change_count INTEGER,
                    decision_change_count INTEGER,
                    PRIMARY KEY(participant_id, trial_id),
                    FOREIGN KEY(participant_id, trial_id)
                        REFERENCES trial_assignments(participant_id, trial_id)
                );
                CREATE TABLE IF NOT EXISTS comprehension_attempts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    participant_id TEXT NOT NULL,
                    attempt_no INTEGER NOT NULL,
                    answers_json TEXT NOT NULL,
                    correct_count INTEGER NOT NULL,
                    passed INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(participant_id) REFERENCES participants(participant_id)
                );
                CREATE TABLE IF NOT EXISTS practice_responses (
                    participant_id TEXT NOT NULL,
                    practice_no INTEGER NOT NULL,
                    predicted_action TEXT NOT NULL,
                    final_action TEXT NOT NULL,
                    correct INTEGER NOT NULL,
                    intermediate_answer TEXT,
                    perceived_predictability INTEGER,
                    perceived_intervenability INTEGER,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(participant_id, practice_no),
                    FOREIGN KEY(participant_id) REFERENCES participants(participant_id)
                );
                CREATE TABLE IF NOT EXISTS block_ratings (
                    participant_id TEXT NOT NULL,
                    block INTEGER NOT NULL,
                    mental_effort INTEGER NOT NULL,
                    fatigue INTEGER NOT NULL,
                    task_difficulty INTEGER NOT NULL,
                    mental_simulation_strategy INTEGER,
                    multi_action_comparison_strategy INTEGER,
                    simple_cue_strategy INTEGER,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(participant_id, block),
                    FOREIGN KEY(participant_id) REFERENCES participants(participant_id)
                );
                CREATE TABLE IF NOT EXISTS questionnaires (
                    participant_id TEXT PRIMARY KEY,
                    answers_json TEXT NOT NULL,
                    questionnaire_version TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(participant_id) REFERENCES participants(participant_id)
                );
                PRAGMA optimize;
            """)
            event_columns = {
                row[1] for row in connection.execute("PRAGMA table_info(interaction_events)")
            }
            if "participant_id" not in event_columns:
                connection.execute("ALTER TABLE interaction_events ADD COLUMN participant_id TEXT")
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_events_participant "
                "ON interaction_events(participant_id, created_at)"
            )
            participant_columns = {
                row[1] for row in connection.execute("PRAGMA table_info(participants)")
            }
            if "questionnaire_version" not in participant_columns:
                connection.execute("ALTER TABLE participants ADD COLUMN questionnaire_version TEXT")
            response_columns = {
                row[1] for row in connection.execute("PRAGMA table_info(trial_responses)")
            }
            if "default_prediction" not in response_columns:
                connection.execute("ALTER TABLE trial_responses ADD COLUMN default_prediction TEXT")
            if "default_prediction_correct" not in response_columns:
                connection.execute(
                    "ALTER TABLE trial_responses ADD COLUMN default_prediction_correct INTEGER"
                )
            if "timed_out" not in response_columns:
                connection.execute(
                    "ALTER TABLE trial_responses ADD COLUMN timed_out INTEGER NOT NULL DEFAULT 0"
                )
            for column in (
                "default_prediction_difference_s", "default_prediction_difference_pct",
            ):
                if column not in response_columns:
                    connection.execute(
                        f"ALTER TABLE trial_responses ADD COLUMN {column} REAL"
                    )
            for column in (
                "actual_release_time_s", "actual_delay_time_s", "actual_reroute_time_s",
            ):
                if column not in response_columns:
                    connection.execute(
                        f"ALTER TABLE trial_responses ADD COLUMN {column} REAL"
                    )
            for column, column_type in (
                ("perceived_uncertainty", "INTEGER"),
                ("flight_time_confidence", "INTEGER"),
                ("action_choice_confidence", "INTEGER"),
                ("layer_t0_dwell_ms", "REAL"),
                ("layer_t1_dwell_ms", "REAL"),
                ("layer_t2_dwell_ms", "REAL"),
                ("layer_switch_count", "INTEGER"),
                ("layer_sequence_json", "TEXT"),
                ("first_future_layer_opened_ms", "REAL"),
                ("prediction_change_count", "INTEGER"),
                ("intermediate_change_count", "INTEGER"),
                ("decision_change_count", "INTEGER"),
            ):
                if column not in response_columns:
                    connection.execute(
                        f"ALTER TABLE trial_responses ADD COLUMN {column} {column_type}"
                    )
            block_columns = {
                row[1] for row in connection.execute("PRAGMA table_info(block_ratings)")
            }
            for column in (
                "mental_simulation_strategy", "multi_action_comparison_strategy",
                "simple_cue_strategy",
            ):
                if column not in block_columns:
                    connection.execute(f"ALTER TABLE block_ratings ADD COLUMN {column} INTEGER")
            questionnaire_columns = {
                row[1] for row in connection.execute("PRAGMA table_info(questionnaires)")
            }
            if "questionnaire_version" not in questionnaire_columns:
                connection.execute("ALTER TABLE questionnaires ADD COLUMN questionnaire_version TEXT")
            practice_columns = {
                row[1] for row in connection.execute("PRAGMA table_info(practice_responses)")
            }
            for column, column_type in (
                ("intermediate_answer", "TEXT"),
                ("perceived_predictability", "INTEGER"),
                ("perceived_intervenability", "INTEGER"),
            ):
                if column not in practice_columns:
                    connection.execute(
                        f"ALTER TABLE practice_responses ADD COLUMN {column} {column_type}"
                    )

    def create_participant(
        self, participant_id: str, trials_by_block: Dict[int, list[int]],
        experiment_version: str = "4.1",
        material_version: str = "2026-08-pretest-flow-v2",
        questionnaire_version: str = "2026-09-measures-v7",
    ) -> Dict[str, Any]:
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as connection:
            existing = connection.execute(
                "SELECT participant_id FROM participants WHERE participant_id=?", (participant_id,)
            ).fetchone()
            if existing:
                raise ValueError("participant_id已存在")
            participant_count = connection.execute("SELECT COUNT(*) FROM participants").fetchone()[0]
            order_id = participant_count % 2 + 1
            connection.execute(
                "INSERT INTO participants "
                "(participant_id, experiment_version, material_version, questionnaire_version, order_id, status, "
                "started_at) VALUES (?, ?, ?, ?, ?, 'in_progress', ?)",
                (participant_id, experiment_version, material_version, questionnaire_version, order_id, now),
            )
            global_order = 1
            for block in sorted(trials_by_block):
                ordered = sorted(trials_by_block[block])
                random.Random(20260826 + block).shuffle(ordered)
                if order_id == 2:
                    ordered.reverse()
                for position, trial_id in enumerate(ordered, 1):
                    connection.execute(
                        "INSERT INTO trial_assignments VALUES (?, ?, ?, ?, ?)",
                        (participant_id, trial_id, block, position, global_order),
                    )
                    global_order += 1
        return {"participantId": participant_id, "orderId": order_id,
                "experimentVersion": experiment_version, "materialVersion": material_version,
                "questionnaireVersion": questionnaire_version}

    def participant_state(self, participant_id: str) -> Dict[str, Any]:
        with self.connect() as connection:
            connection.row_factory = sqlite3.Row
            participant = connection.execute(
                "SELECT * FROM participants WHERE participant_id=?", (participant_id,)
            ).fetchone()
            if not participant:
                raise ValueError("participant不存在")
            completed = connection.execute(
                "SELECT COUNT(*) FROM trial_responses WHERE participant_id=?", (participant_id,)
            ).fetchone()[0]
            return {**dict(participant), "completed_trials": completed}

    def next_assignment(self, participant_id: str) -> Optional[Dict[str, int]]:
        with self.connect() as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                "SELECT a.* FROM trial_assignments a "
                "LEFT JOIN trial_responses r ON r.participant_id=a.participant_id AND r.trial_id=a.trial_id "
                "WHERE a.participant_id=? AND r.trial_id IS NULL ORDER BY a.global_order LIMIT 1",
                (participant_id,),
            ).fetchone()
            return dict(row) if row else None

    def add_trial_response(self, participant_id: str, response: Dict[str, Any]) -> None:
        assignment = self.next_assignment(participant_id)
        if not assignment or assignment["trial_id"] != response["trial_id"]:
            raise ValueError("trial顺序无效或已提交")
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO trial_responses "
                "(participant_id, trial_id, predicted_best_action, prediction_rt_ms, "
                "intermediate_answer, intermediate_correct, intermediate_rt_ms, "
                "final_action, decision_rt_ms, perceived_predictability, "
                "perceived_intervenability, rating_rt_ms, prediction_decision_consistency, "
                "optimal_action_accuracy, trial_started_at, trial_submitted_at, "
                "default_prediction, default_prediction_correct, "
                "default_prediction_difference_s, default_prediction_difference_pct, timed_out, "
                "actual_release_time_s, actual_delay_time_s, actual_reroute_time_s, "
                "perceived_uncertainty, flight_time_confidence, action_choice_confidence, "
                "layer_t0_dwell_ms, layer_t1_dwell_ms, layer_t2_dwell_ms, layer_switch_count, "
                "layer_sequence_json, first_future_layer_opened_ms, prediction_change_count, "
                "intermediate_change_count, decision_change_count) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
                "?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (participant_id, response["trial_id"], response["predicted_best_action"],
                 response["prediction_rt_ms"], response["intermediate_answer"],
                 response.get("intermediate_correct"), response["intermediate_rt_ms"],
                 response["final_action"], response["decision_rt_ms"],
                 response["perceived_predictability"], response["perceived_intervenability"],
                 response["rating_rt_ms"], response["prediction_decision_consistency"],
                 response["optimal_action_accuracy"], response["trial_started_at"], now,
                 response["default_prediction"], response["default_prediction_correct"],
                 response.get("default_prediction_difference_s"),
                 response.get("default_prediction_difference_pct"),
                 response.get("timed_out", 0), response.get("actual_release_time_s"),
                 response.get("actual_delay_time_s"), response.get("actual_reroute_time_s"),
                 response.get("perceived_uncertainty"), response.get("flight_time_confidence"),
                 response.get("action_choice_confidence"), response.get("layer_t0_dwell_ms"),
                 response.get("layer_t1_dwell_ms"), response.get("layer_t2_dwell_ms"),
                 response.get("layer_switch_count"), response.get("layer_sequence_json"),
                 response.get("first_future_layer_opened_ms"), response.get("prediction_change_count"),
                 response.get("intermediate_change_count"), response.get("decision_change_count")),
            )

    def trial_response(self, participant_id: str, trial_id: int) -> Optional[Dict[str, Any]]:
        with self.connect() as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                "SELECT * FROM trial_responses WHERE participant_id=? AND trial_id=?",
                (participant_id, trial_id),
            ).fetchone()
            return dict(row) if row else None

    def add_block_rating(self, participant_id: str, block: int, values: Dict[str, int]) -> None:
        with self.connect() as connection:
            completed = connection.execute(
                "SELECT COUNT(*) FROM trial_responses r JOIN trial_assignments a "
                "ON a.participant_id=r.participant_id AND a.trial_id=r.trial_id "
                "WHERE r.participant_id=? AND a.block=?", (participant_id, block),
            ).fetchone()[0]
            expected = connection.execute(
                "SELECT COUNT(*) FROM trial_assignments WHERE participant_id=? AND block=?",
                (participant_id, block),
            ).fetchone()[0]
            if expected == 0 or completed != expected:
                raise ValueError(f"该block尚未完成{expected or 6}个trial")
            connection.execute(
                "INSERT INTO block_ratings "
                "(participant_id, block, mental_effort, fatigue, task_difficulty, "
                "mental_simulation_strategy, multi_action_comparison_strategy, simple_cue_strategy, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (participant_id, block, values["mental_effort"], values["fatigue"],
                 values["task_difficulty"], values["mental_simulation_strategy"],
                 values["multi_action_comparison_strategy"], values["simple_cue_strategy"],
                 datetime.now(timezone.utc).isoformat()),
            )

    def add_comprehension_attempt(
        self, participant_id: str, answers: Dict[str, str], correct_count: int, passed: bool,
    ) -> None:
        with self.connect() as connection:
            attempt = connection.execute(
                "SELECT COUNT(*) FROM comprehension_attempts WHERE participant_id=?", (participant_id,)
            ).fetchone()[0] + 1
            connection.execute(
                "INSERT INTO comprehension_attempts "
                "(participant_id, attempt_no, answers_json, correct_count, passed, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (participant_id, attempt, json.dumps(answers, ensure_ascii=False), correct_count,
                 int(passed), datetime.now(timezone.utc).isoformat()),
            )

    def add_practice_response(
        self, participant_id: str, practice_no: int, predicted_action: str,
        final_action: str, correct: bool, intermediate_answer: Optional[str] = None,
        perceived_predictability: Optional[int] = None,
        perceived_intervenability: Optional[int] = None,
    ) -> None:
        with self.connect() as connection:
            participant = connection.execute(
                "SELECT 1 FROM participants WHERE participant_id=?", (participant_id,)
            ).fetchone()
            if not participant:
                raise ValueError("participant不存在")
            connection.execute(
                "INSERT INTO practice_responses "
                "(participant_id, practice_no, predicted_action, final_action, correct, "
                "intermediate_answer, perceived_predictability, perceived_intervenability, "
                "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (participant_id, practice_no, predicted_action, final_action, int(correct),
                 intermediate_answer, perceived_predictability, perceived_intervenability,
                 datetime.now(timezone.utc).isoformat()),
            )

    def formal_ready(self, participant_id: str) -> bool:
        with self.connect() as connection:
            passed = connection.execute(
                "SELECT 1 FROM comprehension_attempts WHERE participant_id=? AND passed=1 LIMIT 1",
                (participant_id,),
            ).fetchone()
            practices = connection.execute(
                "SELECT COUNT(*) FROM practice_responses WHERE participant_id=?", (participant_id,)
            ).fetchone()[0]
            return bool(passed) and practices == 4

    def complete(self, participant_id: str, questionnaire: Dict[str, Any]) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as connection:
            count = connection.execute(
                "SELECT COUNT(*) FROM trial_responses WHERE participant_id=?", (participant_id,)
            ).fetchone()[0]
            expected = connection.execute(
                "SELECT COUNT(*) FROM trial_assignments WHERE participant_id=?", (participant_id,)
            ).fetchone()[0]
            if expected == 0 or count != expected:
                raise ValueError(f"尚未完成{expected or 18}个正式trial")
            block_count = connection.execute(
                "SELECT COUNT(*) FROM block_ratings WHERE participant_id=?", (participant_id,)
            ).fetchone()[0]
            if block_count != 3:
                raise ValueError("尚未完成3个block评分")
            connection.execute(
                "INSERT INTO questionnaires (participant_id, answers_json, questionnaire_version, created_at) "
                "VALUES (?, ?, ?, ?)",
                (participant_id, json.dumps(questionnaire, ensure_ascii=False),
                 questionnaire.get("questionnaire_version"), now),
            )
            connection.execute(
                "UPDATE participants SET status='completed', completed_at=? WHERE participant_id=?",
                (now, participant_id),
            )

    def export_trial_rows(self) -> tuple[list[str], list[tuple[Any, ...]]]:
        columns = [
            "participant_id", "experiment_version", "material_version", "order_id",
            "trial_id", "block", "position_in_block", "global_order",
            "questionnaire_version",
            "timed_out", "default_prediction", "default_prediction_correct",
            "default_prediction_difference_s", "default_prediction_difference_pct",
            "prediction_rt_ms", "intermediate_answer",
            "intermediate_correct", "intermediate_rt_ms", "final_action", "selected_option",
            "decision_rt_ms",
            "perceived_predictability", "perceived_uncertainty", "perceived_intervenability",
            "flight_time_confidence", "action_choice_confidence", "rating_rt_ms",
            "layer_t0_dwell_ms", "layer_t1_dwell_ms", "layer_t2_dwell_ms", "layer_switch_count",
            "layer_sequence_json", "first_future_layer_opened_ms", "prediction_change_count",
            "intermediate_change_count", "decision_change_count",
            "actual_release_time_s", "actual_delay_time_s", "actual_reroute_time_s",
            "simulation_action_alignment", "optimal_action_accuracy",
            "trial_started_at", "trial_submitted_at", "mental_effort", "fatigue",
            "task_difficulty", "mental_simulation_strategy", "multi_action_comparison_strategy",
            "simple_cue_strategy",
        ]
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT p.participant_id, p.experiment_version, p.material_version, p.order_id, "
                "a.trial_id, a.block, a.position_in_block, a.global_order, "
                "p.questionnaire_version, "
                "r.timed_out, NULLIF(r.default_prediction, ''), r.default_prediction_correct, "
                "r.default_prediction_difference_s, r.default_prediction_difference_pct, "
                "CASE WHEN r.prediction_rt_ms >= 0 THEN r.prediction_rt_ms END, NULLIF(r.intermediate_answer, ''), "
                "r.intermediate_correct, CASE WHEN r.intermediate_rt_ms >= 0 THEN r.intermediate_rt_ms END, "
                "NULLIF(r.final_action, ''), NULLIF(r.final_action, ''), "
                "CASE WHEN r.decision_rt_ms >= 0 THEN r.decision_rt_ms END, "
                "CASE WHEN r.perceived_predictability > 0 THEN r.perceived_predictability END, "
                "CASE WHEN r.perceived_uncertainty > 0 THEN r.perceived_uncertainty END, "
                "CASE WHEN r.perceived_intervenability > 0 THEN r.perceived_intervenability END, "
                "CASE WHEN r.flight_time_confidence > 0 THEN r.flight_time_confidence END, "
                "CASE WHEN r.action_choice_confidence > 0 THEN r.action_choice_confidence END, "
                "CASE WHEN r.rating_rt_ms >= 0 THEN r.rating_rt_ms END, "
                "CASE WHEN r.layer_t0_dwell_ms >= 0 THEN r.layer_t0_dwell_ms END, "
                "CASE WHEN r.layer_t1_dwell_ms >= 0 THEN r.layer_t1_dwell_ms END, "
                "CASE WHEN r.layer_t2_dwell_ms >= 0 THEN r.layer_t2_dwell_ms END, "
                "r.layer_switch_count, r.layer_sequence_json, r.first_future_layer_opened_ms, "
                "r.prediction_change_count, r.intermediate_change_count, r.decision_change_count, "
                "r.actual_release_time_s, r.actual_delay_time_s, r.actual_reroute_time_s, "
                "CASE WHEN r.prediction_decision_consistency >= 0 THEN r.prediction_decision_consistency END, "
                "CASE WHEN r.optimal_action_accuracy >= 0 THEN r.optimal_action_accuracy END, "
                "r.trial_started_at, r.trial_submitted_at, b.mental_effort, b.fatigue, "
                "b.task_difficulty, b.mental_simulation_strategy, "
                "b.multi_action_comparison_strategy, b.simple_cue_strategy "
                "FROM participants p JOIN trial_assignments a ON a.participant_id=p.participant_id "
                "JOIN trial_responses r ON r.participant_id=a.participant_id AND r.trial_id=a.trial_id "
                "LEFT JOIN block_ratings b ON b.participant_id=a.participant_id AND b.block=a.block "
                "ORDER BY p.participant_id, a.global_order"
            ).fetchall()
        return columns, rows

    def export_questionnaire_rows(self) -> tuple[list[str], list[tuple[Any, ...]]]:
        fields = [
            "age", "gender", "major", "uav_experience", "aviation_course",
            "system_management_experience", "game_experience", "map_ability",
            "sustained_attention", "time_pressure", "overall_effort",
            "scenario_comprehension", "wind_route_reliance", "wind_relation_importance",
            "mental_simulation_use", "perceived_realism",
            "uncertainty_analysis_effort", "intervenability_comparison_effort",
            "strategy", "simulation_trigger_selections", "simulation_trigger_other",
            "decision_information_selections", "decision_information_other",
            "additional_information",
        ]
        columns = [
            "participant_id", "experiment_version", "material_version", "order_id",
            "questionnaire_version", *fields, "questionnaire_submitted_at",
        ]
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT p.participant_id, p.experiment_version, p.material_version, p.order_id, "
                "COALESCE(q.questionnaire_version, p.questionnaire_version), q.answers_json, q.created_at "
                "FROM questionnaires q JOIN participants p ON p.participant_id=q.participant_id "
                "ORDER BY p.participant_id"
            ).fetchall()
        formatted = []
        legacy_keys = {
            "uav_experience": "uavExperience",
            "aviation_course": "aviationCourse",
            "system_management_experience": "systemManagementExperience",
            "game_experience": "gameExperience",
            "map_ability": "mapAbility",
            "sustained_attention": "sustainedAttention",
            "time_pressure": "timePressure",
            "overall_effort": "overallEffort",
            "scenario_comprehension": "scenarioComprehension",
            "wind_route_reliance": "windRouteReliance",
            "wind_relation_importance": "windRelationImportance",
            "mental_simulation_use": "mentalSimulationUse",
            "perceived_realism": "perceivedRealism",
            "uncertainty_analysis_effort": "uncertaintyAnalysisEffort",
            "intervenability_comparison_effort": "intervenabilityComparisonEffort",
            "simulation_trigger_selections": "simulationTriggerSelections",
            "simulation_trigger_other": "simulationTriggerOther",
            "decision_information_selections": "decisionInformationSelections",
            "decision_information_other": "decisionInformationOther",
            "additional_information": "additionalInformation",
        }
        for participant_id, experiment_version, material_version, order_id, version, answers_json, created_at in rows:
            answers = json.loads(answers_json)
            values = []
            for field in fields:
                value = answers.get(field, answers.get(legacy_keys.get(field, "")))
                values.append(
                    json.dumps(value, ensure_ascii=False)
                    if isinstance(value, (list, dict)) else value
                )
            formatted.append((
                participant_id, experiment_version, material_version, order_id, version,
                *values, created_at,
            ))
        return columns, formatted

    def export_event_rows(self) -> tuple[list[str], list[tuple[Any, ...]]]:
        columns = ["id", "session_id", "participant_id", "trial_id", "event_type", "event_value",
                   "client_time_ms", "created_at"]
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT id, session_id, participant_id, trial_id, event_type, event_value, "
                "client_time_ms, created_at "
                "FROM interaction_events ORDER BY id"
            ).fetchall()
        return columns, rows

    def add_session(self, session_id: str, trial_id: int, action: str, seed: int) -> None:
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO simulation_sessions VALUES (?, ?, ?, ?, ?)",
                (session_id, trial_id, action, seed, datetime.now(timezone.utc).isoformat()),
            )

    def add_event(
        self, event_type: str, session_id: Optional[str], participant_id: Optional[str],
        trial_id: Optional[int],
        event_value: Optional[Dict[str, Any]], client_time_ms: Optional[float],
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO interaction_events "
                "(session_id, participant_id, trial_id, event_type, event_value, client_time_ms, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (session_id, participant_id, trial_id, event_type,
                 json.dumps(event_value, ensure_ascii=False) if event_value else None,
                 client_time_ms, datetime.now(timezone.utc).isoformat()),
            )
