import pytest

from ems.diagnostics import Check, build_diagnostics, overall_status


def test_check_rejects_invalid_status():
    # A typo'd status must fail loudly at construction, not silently rank as ok.
    with pytest.raises(ValueError):
        Check("x", "X", "greenish", "detail")


def _facts(**over):
    base = dict(
        dev_mode="mock", dry_run=True, data_quality="complete",
        prices_ok=True, forecast_ok=True, battery_ok=True, p1_paired=True,
        plan_ok=True, store_ok=True, settings_store_ok=True, auth_required=False,
    )
    base.update(over)
    return base


def test_mode_check_explains_config_forced_dry_run():
    # #136 F3: when config blocks UI operational, System Run mode shows why (warn + reason).
    checks = build_diagnostics(
        **_facts(dry_run=True),
        dry_run_block_reason="config forces watch-only; UI operational is ON",
    )
    mode = next(c for c in checks if c.key == "mode")
    assert mode.status == "warn"
    assert "config forces watch-only" in mode.detail
    assert "dry-run on" in mode.detail


def test_mode_check_ok_without_block_reason():
    mode = next(c for c in build_diagnostics(**_facts(dry_run=True)) if c.key == "mode")
    assert mode.status == "ok"
    assert mode.detail == "mock, dry-run on"



def test_unreachable_history_store_fails_overall():
    checks = build_diagnostics(**_facts(store_ok=False))
    store = next(c for c in checks if c.key == "history_store")
    assert store.status == "fail"
    assert overall_status(checks) == "fail"


def test_missing_prices_is_a_warning_not_a_failure():
    checks = build_diagnostics(**_facts(prices_ok=False, plan_ok=False))
    assert next(c for c in checks if c.key == "prices").status == "warn"
    assert overall_status(checks) == "warn"


def test_unsafe_data_quality_fails():
    checks = build_diagnostics(**_facts(data_quality="unsafe"))
    assert next(c for c in checks if c.key == "data_quality").status == "fail"
    assert overall_status(checks) == "fail"


def test_auth_check_reflects_protection():
    # Legacy shared-token mode (identity_auth defaults False): open vs. token-protected copy.
    open_checks = build_diagnostics(**_facts(auth_required=False))
    assert "open" in next(c for c in open_checks if c.key == "auth").detail
    prot = build_diagnostics(**_facts(auth_required=True))
    assert "protected" in next(c for c in prot if c.key == "auth").detail


def test_auth_check_is_identity_aware_when_identity_store_wired():
    # Once the identity store is wired (production always) the row reports the truthful state at ok,
    # regardless of whether a legacy shared token happens to be set (auth_required is ignored).
    for auth_required in (False, True):
        checks = build_diagnostics(**_facts(auth_required=auth_required, identity_auth=True))
        auth = next(c for c in checks if c.key == "auth")
        assert auth.status == "ok"
        assert "identity auth active" in auth.detail
        assert "open" not in auth.detail  # never the stale "open — set a token" copy


def test_overall_status_empty_is_ok():
    assert overall_status([]) == "ok"


def test_per_signal_sensor_checks_from_freshness():
    fr = {"grid": "fresh", "solar": "fresh", "ev": "fresh",
          "battery": "missing", "soc": "missing"}
    checks = build_diagnostics(**_facts(), freshness=fr)
    by_key = {c.key: c for c in checks}
    assert by_key["sensor.grid"].status == "ok"
    assert by_key["sensor.battery"].status == "warn"  # non-critical signal missing -> warn
    assert by_key["sensor.soc"].status == "fail"  # critical signal missing -> fail
    # A stale critical signal is also a failure.
    stale = {c.key: c for c in build_diagnostics(**_facts(), freshness={"grid": "stale"})}
    assert stale["sensor.grid"].status == "fail"


def test_car_guard_blind_warns():
    checks = build_diagnostics(**_facts(), ev_guard_blind=True)
    guard = next(c for c in checks if c.key == "car_guard")
    assert guard.status == "warn"
    assert "EV meter" in guard.detail


def test_grid_fuse_unset_warns():
    """#197: unconfirmed main fuse is a System CHECK — never present 1×25 default as known."""
    checks = build_diagnostics(**_facts(), grid_fuse_unset=True)
    fuse = next(c for c in checks if c.key == "grid_fuse")
    assert fuse.status == "warn"
    assert "not confirmed" in fuse.detail
    assert "5750" in fuse.detail
    assert overall_status(checks) == "warn"
    # Confirmed (flag false) → no row.
    keys = {c.key for c in build_diagnostics(**_facts(), grid_fuse_unset=False)}
    assert "grid_fuse" not in keys


def test_no_sensor_checks_without_freshness():
    keys = {c.key for c in build_diagnostics(**_facts())}
    assert not any(k.startswith("sensor.") for k in keys)


def test_battery_missing_vs_unreachable_detail():
    """Probe failure must not be mislabeled as 'no battery driver' (father UI false positive)."""
    missing = next(c for c in build_diagnostics(**_facts(battery_ok=False)) if c.key == "battery")
    assert missing.status == "warn"
    assert missing.detail == "no battery driver — read-only"
    unreachable = next(
        c for c in build_diagnostics(
            **_facts(battery_ok=False), battery_present=True,
        ) if c.key == "battery"
    )
    assert unreachable.status == "warn"
    assert "unreachable" in unreachable.detail
    assert "no battery driver" not in unreachable.detail
    ok = next(c for c in build_diagnostics(**_facts(battery_ok=True)) if c.key == "battery")
    assert ok.status == "ok"
    assert "probed" in ok.detail
