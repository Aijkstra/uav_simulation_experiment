from dataclasses import dataclass
import math
from typing import Dict, List, Optional


@dataclass(frozen=True)
class Waypoint:
    waypoint_id: str
    x: float
    y: float


@dataclass(frozen=True)
class Route:
    route_set_id: str
    path_id: str
    waypoints: List[Waypoint]

    @property
    def task_role(self) -> str:
        """All loaded routes are UAV decision options."""
        return "focused_candidate"


@dataclass(frozen=True)
class WindZone:
    trial_id: int
    time_layer: int
    zone_id: str
    cx: float
    cy: float
    radius: float
    mean_speed: float
    direction_deg: float
    speed_sd: float
    direction_sd: float

    def contains(self, x: float, y: float) -> bool:
        # A deterministic, softly irregular boundary is more representative than a
        # perfect circle while remaining reproducible for every participant.
        dx, dy = x - self.cx, y - self.cy
        angle = math.atan2(dy, dx)
        phase = (self.trial_id * 17 + self.time_layer * 11 + sum(map(ord, self.zone_id))) % 31
        boundary = self.radius * (
            1.0 + .13 * math.sin(3 * angle + phase) + .07 * math.sin(5 * angle - phase * .4)
        )
        return dx * dx + dy * dy <= boundary * boundary


@dataclass(frozen=True)
class TrialConfig:
    trial_id: int
    block: int
    predictability: str
    intervenability: str
    complexity: str
    drone_count: int
    route_set_id: str
    start_node: str
    goal_node: str
    release_path: str
    delay_seconds: float
    reroute_path: str
    planned_best_action: str
    intermediate_question: str
    seed: int


@dataclass
class DroneState:
    x: float
    y: float
    ground_speed: float
    battery_wh: float
    energy_wh: float = 0.0
    segment_index: int = 0
    status: str = "waiting"
    airborne_time: float = 0.0


@dataclass(frozen=True)
class SimulationFrame:
    time: float
    x: float
    y: float
    ground_speed: float
    battery_wh: float
    energy_wh: float
    eta: float
    status: str
    actual_wind_layer: int
    segment_index: int


@dataclass(frozen=True)
class SimulationResult:
    trial_id: int
    action: str
    seed: int
    path_id: str
    total_time: float
    airborne_time: float
    energy_wh: float
    min_battery_wh: float
    max_crosswind: float
    strongest_zone: Optional[str]
    frames: List[SimulationFrame]


@dataclass(frozen=True)
class ActionGroundTruth:
    trial_id: int
    release_time_s: float
    delay_time_s: float
    reroute_time_s: float
    release_energy_wh: float
    delay_energy_wh: float
    reroute_energy_wh: float
    best_time_action: str
    best_energy_action: str
    intervenability_pct: float
    decision_gap_pct: float

    def time_for(self, action: str) -> float:
        return {
            "release": self.release_time_s,
            "delay": self.delay_time_s,
            "reroute": self.reroute_time_s,
        }[action]


@dataclass(frozen=True)
class ExperimentData:
    trials: Dict[int, TrialConfig]
    routes: Dict[str, Dict[str, Route]]
    wind_zones: Dict[int, Dict[int, List[WindZone]]]
    parameters: Dict[str, object]
    ground_truth: Dict[int, ActionGroundTruth]
