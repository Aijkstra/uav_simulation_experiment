import pytest

from uav_sim.config import load_experiment_data
from uav_sim.simulator import Simulator, clamp


@pytest.fixture(scope="module")
def simulator():
    return Simulator(load_experiment_data())


def test_clamp():
    assert clamp(20, 4, 18) == 18
    assert clamp(2, 4, 18) == 4
    assert clamp(10, 4, 18) == 10


@pytest.mark.parametrize("action", ["release", "delay", "reroute"])
def test_simulation_is_reproducible(simulator, action):
    first = simulator.run(1, action)
    second = simulator.run(1, action)
    assert first == second
    assert first.frames[-1].status == "finished"
    assert first.frames[-1].eta == 0
    assert 0 < first.energy_wh
    assert first.min_battery_wh < 90
    assert all(0 <= f.ground_speed <= 18 for f in first.frames)


def test_delay_includes_wait_and_zero_speed_frames(simulator):
    result = simulator.run(1, "delay")
    assert result.total_time == pytest.approx(result.airborne_time + 120, abs=0.01)
    assert all(frame.ground_speed == 0 for frame in result.frames if frame.time < 120)


def test_action_route_mapping(simulator):
    assert simulator.run(1, "release", include_frames=False).path_id == "Path-A"
    assert simulator.run(1, "delay", include_frames=False).path_id == "Path-A"
    assert simulator.run(1, "reroute", include_frames=False).path_id == "Path-B"


def test_every_trial_initializes_drone_at_shared_depot(simulator):
    for trial_id, trial in simulator.data.trials.items():
        drones = simulator.initial_drones(trial_id)
        assert len(drones) == 1
        assert [drone["id"] for drone in drones] == [1]
        routes = simulator.data.routes[trial.route_set_id]
        release_start = routes[trial.release_path].waypoints[0]
        reroute_start = routes[trial.reroute_path].waypoints[0]
        assert release_start.waypoint_id == trial.start_node == "DEPOT"
        assert reroute_start.waypoint_id == trial.start_node
        assert (release_start.x, release_start.y) == (reroute_start.x, reroute_start.y)
        assert (drones[0]["x"], drones[0]["y"]) == (release_start.x, release_start.y)


def test_invalid_inputs(simulator):
    with pytest.raises(ValueError):
        simulator.run(999, "release")
    with pytest.raises(ValueError):
        simulator.run(1, "invalid")
