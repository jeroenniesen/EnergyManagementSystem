"""Layered readiness: each layer implies the ones above; the summary is calm + honest."""
from ems.readiness import compute_readiness, home_state


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


# ---- home_state headline ----------------------------------------------------------------------

def test_home_state_attention_when_sensing_down():
    hs = home_state(_r(sensing_ok=False), intent="allow_self_consumption", override_active=False)
    assert hs["tone"] == "attention" and "Needs attention" in hs["headline"]


def test_home_state_override_is_manual_control():
    hs = home_state(_r(), intent="grid_charge_to_target", override_active=True)
    assert hs["tone"] == "controlling" and "manual control" in hs["headline"]


def test_home_state_observe_only_is_watching_not_controlling():
    # dry-run install: EMS never claims to control — it's watching.
    hs = home_state(_r(operational=False), intent="grid_charge_to_target", override_active=False)
    assert hs["tone"] == "watching" and hs["headline"].startswith("Watching")


def test_home_state_control_ready_self_consumption_is_good():
    hs = home_state(_r(operational=True), intent="allow_self_consumption", override_active=False)
    assert hs["tone"] == "good" and "All good" in hs["headline"]


def test_home_state_carries_simulated_flag():
    assert home_state(_r(), intent="hold_reserve", override_active=False, simulated=True)[
        "simulated"] is True
