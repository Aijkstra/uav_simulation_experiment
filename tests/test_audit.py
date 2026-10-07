from uav_sim.audit import build_audit


def test_pre_pilot_material_audit_passes():
    report = build_audit()
    assert report["status"] == "pass", report["errors"]
    assert report["formalTrialCount"] == 18
    assert report["blockCounts"] == {1: 6, 2: 6, 3: 6}
    assert report["bestActionCounts"] == {"delay": 6, "release": 6, "reroute": 6}
    assert report["formalRealizationBestActionCounts"] == {
        "delay": 6, "release": 6, "reroute": 6,
    }
    assert report["droneCounts"] == {1: 18}
    assert report["waypointCountRange"] == [9, 9]
