"""Layered readiness: each layer implies the ones above; the summary is calm + honest."""
from ems.readiness import compute_readiness


def _r(**kw):
    base = dict(store_ok=True, sensing_ok=True, plan_ok=True, data_quality="complete",
                plan_valid=True, operational=False, capability_ok=True)
    base.update(kw)
    return compute_readiness(**base)


def test_dry_run_install_is_dashboard_sensing_planning_ready_but_not_control():
    r = _r(operational=False)
    assert r.alive and r.dashboard_ready and r.sensing_ready and r.planning_ready
    assert r.control_ready is False  # never control while dry-run/observing
    assert "watching only" in r.summary


def test_no_store_is_only_alive():
    r = _r(store_ok=False)
    assert r.alive and not r.dashboard_ready and not r.sensing_ready
    assert "Starting up" in r.summary


def test_stale_sensing_blocks_planning_and_control():
    r = _r(sensing_ok=False)
    assert r.dashboard_ready and not r.sensing_ready and not r.planning_ready
    assert not r.control_ready and "Needs attention" in r.summary


def test_unsafe_data_quality_blocks_planning():
    r = _r(data_quality="unsafe")
    assert not r.planning_ready and not r.control_ready


def test_control_ready_only_when_everything_holds_and_operational():
    r = _r(operational=True, plan_valid=True, capability_ok=True)
    assert r.control_ready and "Live control is ready" in r.summary


def test_operational_but_invalid_plan_is_paused_not_controlling():
    r = _r(operational=True, plan_valid=False)
    assert r.planning_ready and not r.control_ready and "paused safely" in r.summary


def test_operational_but_no_capability_is_not_control_ready():
    r = _r(operational=True, capability_ok=False)
    assert not r.control_ready
