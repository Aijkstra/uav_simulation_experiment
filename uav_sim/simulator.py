import math
import random
from typing import Dict, List, Optional, Tuple

from .models import (
    DroneState, ExperimentData, Route, SimulationFrame, SimulationResult, WindZone,
)

ACTIONS = {"release", "delay", "reroute"}


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def distance(x1: float, y1: float, x2: float, y2: float) -> float:
    return math.hypot(x2 - x1, y2 - y1)


class Simulator:
    def __init__(self, data: ExperimentData):
        self.data = data

    def forecast_winds(self, trial_id: int) -> Dict[int, List[Tuple[WindZone, float, float]]]:
        """Return the participant-visible forecast means for every time layer."""
        if trial_id not in self.data.trials:
            raise ValueError("不存在的trialId: %s" % trial_id)
        result: Dict[int, List[Tuple[WindZone, float, float]]] = {}
        for layer in (0, 1, 2):
            result[layer] = []
            for zone in self.data.wind_zones[trial_id][layer]:
                radians = math.radians(zone.direction_deg)
                result[layer].append((
                    zone,
                    zone.mean_speed * math.cos(radians),
                    zone.mean_speed * math.sin(radians),
                ))
        return result

    def actual_winds(
        self, trial_id: int, seed: Optional[int] = None,
    ) -> Dict[int, List[Tuple[WindZone, float, float]]]:
        """Return one reproducible hidden realization of the forecast.

        Participants see forecast means and uncertainty, while simulations use
        this seed-fixed realization. T0 remains exact and only T1/T2 vary.
        """
        if trial_id not in self.data.trials:
            raise ValueError("不存在的trialId: %s" % trial_id)
        run_seed = self.data.trials[trial_id].seed if seed is None else int(seed)
        rng = random.Random(run_seed)
        result: Dict[int, List[Tuple[WindZone, float, float]]] = {}
        for layer in (0, 1, 2):
            result[layer] = []
            for zone in self.data.wind_zones[trial_id][layer]:
                # T0 is a measured current state. T1/T2 are uncertain forecasts
                # whose hidden realization is used by the running simulation.
                if layer == 0:
                    speed, direction = zone.mean_speed, zone.direction_deg
                else:
                    speed = max(0.0, rng.gauss(zone.mean_speed, zone.speed_sd))
                    direction = rng.gauss(zone.direction_deg, zone.direction_sd) % 360.0
                radians = math.radians(direction)
                result[layer].append((zone, speed * math.cos(radians), speed * math.sin(radians)))
        return result

    def _layer(self, time_s: float) -> int:
        p = self.data.parameters
        if time_s >= float(p["windLayer2Start_s"]):
            return 2
        if time_s >= float(p["windLayer1Start_s"]):
            return 1
        return 0

    @staticmethod
    def _remaining_distance(route: Route, state: DroneState) -> float:
        target = route.waypoints[state.segment_index + 1]
        remaining = distance(state.x, state.y, target.x, target.y)
        for index in range(state.segment_index + 1, len(route.waypoints) - 1):
            a, b = route.waypoints[index], route.waypoints[index + 1]
            remaining += distance(a.x, a.y, b.x, b.y)
        return remaining

    def initial_drones(self, trial_id: int) -> List[Dict[str, float]]:
        """Return the UAV position before an action starts."""
        if trial_id not in self.data.trials:
            raise ValueError("不存在的trialId: %s" % trial_id)
        trial = self.data.trials[trial_id]
        routes = self.data.routes[trial.route_set_id]
        drone_start = routes[trial.release_path].waypoints[0]
        return [{"id": 1, "x": drone_start.x, "y": drone_start.y}]

    @staticmethod
    def _point_at_distance(route: Route, wanted: float) -> Tuple[float, float]:
        cursor = 0.0
        for start, end in zip(route.waypoints, route.waypoints[1:]):
            segment = distance(start.x, start.y, end.x, end.y)
            if cursor + segment >= wanted:
                ratio = (wanted - cursor) / max(segment, 1e-9)
                return (
                    start.x + (end.x - start.x) * ratio,
                    start.y + (end.y - start.y) * ratio,
                )
            cursor += segment
        final = route.waypoints[-1]
        return final.x, final.y

    def no_wind_benchmark(self, trial_id: int, action: str) -> Dict[str, object]:
        """Return participant-safe no-wind timing references for one action.

        Zone entry/exit values use the scenario's absolute clock, so the delay
        action starts at 120 seconds. They are geometric references at the fixed
        base airspeed and never use forecast or realized wind outcomes.
        """
        if trial_id not in self.data.trials:
            raise ValueError("不存在的trialId: %s" % trial_id)
        if action not in ACTIONS:
            raise ValueError("action必须是release、delay或reroute")
        trial = self.data.trials[trial_id]
        path_id = trial.reroute_path if action == "reroute" else trial.release_path
        route = self.data.routes[trial.route_set_id][path_id]
        airspeed = float(self.data.parameters["vAir_mps"])
        start_time = trial.delay_seconds if action == "delay" else 0.0
        route_length = sum(
            distance(start.x, start.y, end.x, end.y)
            for start, end in zip(route.waypoints, route.waypoints[1:])
        )
        flight_time = route_length / airspeed
        # Zone geometry is stable across T0/T1/T2 in the frozen material. Use
        # the current layer's named W1/W2 footprint and sample at <= 1 metre.
        zones = self.data.wind_zones[trial_id][0]
        step_count = max(1, math.ceil(route_length))
        zone_intervals = []
        for zone in zones:
            inside_distances = []
            for step in range(step_count + 1):
                travelled = route_length * step / step_count
                x, y = self._point_at_distance(route, travelled)
                if zone.contains(x, y):
                    inside_distances.append(travelled)
            zone_intervals.append({
                "zoneId": zone.zone_id,
                "entryTimeSeconds": (
                    start_time + inside_distances[0] / airspeed
                    if inside_distances else None
                ),
                "exitTimeSeconds": (
                    start_time + inside_distances[-1] / airspeed
                    if inside_distances else None
                ),
            })
        return {
            "action": action,
            "pathId": path_id,
            "startTimeSeconds": start_time,
            "flightTimeSeconds": flight_time,
            "arrivalTimeSeconds": start_time + flight_time,
            "zoneIntervals": zone_intervals,
        }

    def run(
        self, trial_id: int, action: str, seed: Optional[int] = None,
        include_frames: bool = True, wind_mode: str = "actual",
    ) -> SimulationResult:
        if trial_id not in self.data.trials:
            raise ValueError("不存在的trialId: %s" % trial_id)
        if action not in ACTIONS:
            raise ValueError("action必须是release、delay或reroute")
        if wind_mode not in {"actual", "forecast"}:
            raise ValueError("wind_mode必须是actual或forecast")
        trial = self.data.trials[trial_id]
        run_seed = trial.seed if seed is None else int(seed)
        path_id = trial.reroute_path if action == "reroute" else trial.release_path
        route = self.data.routes[trial.route_set_id][path_id]
        p = self.data.parameters
        dt = float(p["windUpdate_s"])
        delay = trial.delay_seconds if action == "delay" else 0.0
        initial = route.waypoints[0]
        state = DroneState(initial.x, initial.y, 0.0, float(p["batteryCapacity_Wh"]))
        actual_winds = (
            self.forecast_winds(trial_id)
            if wind_mode == "forecast"
            else self.actual_winds(trial_id, run_seed)
        )
        time_s = 0.0
        max_crosswind = 0.0
        strongest_zone: Optional[str] = None
        strongest_speed = -1.0
        min_battery = state.battery_wh
        frames: List[SimulationFrame] = []

        def append_frame(eta: float) -> None:
            if include_frames:
                frames.append(SimulationFrame(
                    round(time_s, 3), state.x, state.y, state.ground_speed,
                    state.battery_wh, state.energy_wh, max(0.0, eta), state.status,
                    self._layer(time_s), state.segment_index,
                ))

        state.status = "waiting" if delay else "flying"
        append_frame(delay + self._remaining_distance(route, state) / float(p["vAir_mps"]))
        max_steps = int(7200 / dt)
        for _ in range(max_steps):
            if time_s < delay:
                time_s = min(delay, time_s + dt)
                state.ground_speed = 0.0
                state.status = "waiting"
                append_frame((delay - time_s) + self._remaining_distance(route, state) / float(p["vAir_mps"]))
                continue

            state.status = "flying"
            target = route.waypoints[state.segment_index + 1]
            dx, dy = target.x - state.x, target.y - state.y
            segment_remaining = math.hypot(dx, dy)
            hx, hy = dx / max(segment_remaining, 0.001), dy / max(segment_remaining, 0.001)
            wx = wy = 0.0
            active = []
            layer = self._layer(time_s)
            for zone, zone_wx, zone_wy in actual_winds[layer]:
                if zone.contains(state.x, state.y):
                    wx += zone_wx
                    wy += zone_wy
                    active.append((zone, math.hypot(zone_wx, zone_wy)))
            w_parallel = wx * hx + wy * hy
            w_cross = wx * (-hy) + wy * hx
            max_crosswind = max(max_crosswind, abs(w_cross))
            for zone, speed in active:
                if speed > strongest_speed:
                    strongest_speed, strongest_zone = speed, "%s:L%s" % (zone.zone_id, layer)
            state.ground_speed = clamp(
                float(p["vAir_mps"]) + w_parallel - 0.08 * abs(w_cross),
                float(p["minGroundSpeed_mps"]), float(p["maxGroundSpeed_mps"]),
            )
            step_distance = state.ground_speed * dt
            if step_distance >= segment_remaining:
                used_dt = segment_remaining / state.ground_speed
                state.x, state.y = target.x, target.y
            else:
                used_dt = dt
                state.x += hx * step_distance
                state.y += hy * step_distance
            power = (
                float(p["Pbase_W"])
                + float(p["headwindCoeff_W_per_mps2"]) * max(0.0, -w_parallel) ** 2
                + float(p["crosswindCoeff_W_per_mps2"]) * w_cross ** 2
                + float(p["payloadCoeff_W_per_kg"]) * 1.0
            )
            energy = power * used_dt / 3600.0
            state.energy_wh += energy
            state.battery_wh -= energy
            min_battery = min(min_battery, state.battery_wh)
            state.airborne_time += used_dt
            time_s += used_dt

            arrived = distance(state.x, state.y, target.x, target.y) < 1e-6
            if arrived and state.segment_index + 1 == len(route.waypoints) - 1:
                state.status = "finished"
                state.ground_speed = 0.0
                append_frame(0.0)
                break
            if arrived:
                state.segment_index += 1
            eta = self._remaining_distance(route, state) / max(state.ground_speed, 0.001)
            append_frame(eta)
        else:
            raise RuntimeError("仿真超过2小时限制，可能存在无效路线")

        return SimulationResult(
            trial_id, action, run_seed, path_id, time_s, state.airborne_time,
            state.energy_wh, min_battery, max_crosswind, strongest_zone, frames,
        )
