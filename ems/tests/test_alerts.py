from ems.alerts import Alert, data_quality, derive_alerts

ALL_FRESH = {"grid": "fresh", "solar": "fresh", "ev": "fresh", "battery": "fresh", "soc": "fresh"}
SIGNALS = ("grid", "soc", "solar", "ev", "battery")
STATES = ("missing", "stale")


def test_dry_run_yields_info_alert():
    alerts = derive_alerts(ALL_FRESH, dry_run=True, decision_outcome="dry_run")
    keys = {a.key: a.severity for a in alerts}
    assert keys["dry_run_active"] == "info"


def test_stale_critical_signal_is_critical():
    fr = {**ALL_FRESH, "soc": "stale", "ev": "stale"}
    alerts = {a.key: a.severity for a in derive_alerts(fr, dry_run=False, decision_outcome=None)}
    assert alerts["soc_stale"] == "critical"  # soc is critical
    assert alerts["ev_stale"] == "warning"  # ev is not


def test_battery_failure_outcomes_map_to_severity():
    a1 = derive_alerts(ALL_FRESH, dry_run=False, decision_outcome="failed_unrecovered")
    assert any(a.key == "battery_write_failed_unrecovered" and a.severity == "critical" for a in a1)
    a2 = derive_alerts(ALL_FRESH, dry_run=False, decision_outcome="failed_recovered")
    assert any(a.key == "battery_write_failed_recovered" and a.severity == "warning" for a in a2)


def test_data_quality_precedence_price_fallback_over_degraded():
    # Missing price + a stale non-critical signal -> price_fallback (more severe sibling).
    fr = {**ALL_FRESH, "solar": "stale"}
    assert data_quality(fr, prices_ok=False, forecast_ok=True) == "price_fallback"


def test_data_quality_levels():
    assert data_quality(ALL_FRESH, prices_ok=True, forecast_ok=True) == "complete"
    assert data_quality(ALL_FRESH, prices_ok=False, forecast_ok=True) == "price_fallback"
    assert data_quality(ALL_FRESH, prices_ok=True, forecast_ok=False) == "degraded"
    grid_stale = data_quality({**ALL_FRESH, "grid": "stale"}, prices_ok=True, forecast_ok=True)
    assert grid_stale == "unsafe"
    solar_stale = data_quality({**ALL_FRESH, "solar": "stale"}, prices_ok=True, forecast_ok=True)
    assert solar_stale == "degraded"


# --- B-37 calm actionable warnings: every alert answers what happened / is it safe / what next ---

def _collect_all_alerts() -> list[Alert]:
    """Every distinct alert `derive_alerts` can emit, deduplicated by key."""
    seen: dict[str, Alert] = {}
    for dry_run in (False, True):
        for outcome in (None, "failed_recovered", "failed_unrecovered"):
            for a in derive_alerts(ALL_FRESH, dry_run=dry_run, decision_outcome=outcome):
                seen[a.key] = a
    for sig in SIGNALS:
        for state in STATES:
            fr = {**ALL_FRESH, sig: state}
            for a in derive_alerts(fr, dry_run=False, decision_outcome=None):
                seen[a.key] = a
    return list(seen.values())


def test_every_alert_has_non_empty_safe_and_action():
    alerts = _collect_all_alerts()
    # Sanity: dry_run + 2 battery-write outcomes + 5 signals x 2 states = 13 distinct keys.
    assert len(alerts) == 13
    for a in alerts:
        assert a.safe.strip(), f"{a.key} has no safe answer"
        assert a.action.strip(), f"{a.key} has no action"


def test_alert_copy_style_guard():
    """No alert may describe a condition without a next step: the action either states an
    automatic behaviour (so 'nothing needed' is an honest, explicit answer) or points to a
    concrete place to act, and always reads as a complete sentence."""
    for a in _collect_all_alerts():
        action = a.action.strip()
        safe = a.safe.strip()
        assert len(action) <= 160, f"{a.key} action too long ({len(action)})"
        assert len(safe) <= 160, f"{a.key} safe too long ({len(safe)})"
        assert action.endswith((".", "!", "?")), f"{a.key} action isn't a full sentence: {action!r}"
        automatic = "nothing needed" in action.lower() or "automatically" in action.lower()
        concrete_place = any(
            term in action for term in ("Settings", "Home Assistant", "support", "Manual control")
        ) or "check" in action.lower()
        assert automatic or concrete_place, (
            f"{a.key} action has no automatic behaviour and no concrete place to act: {action!r}"
        )


def test_signal_alerts_mention_state_appropriately():
    # Regression: existing message copy/format behaviour must be untouched by the new fields.
    fr = {**ALL_FRESH, "grid": "missing"}
    alerts = {a.key: a for a in derive_alerts(fr, dry_run=False, decision_outcome=None)}
    assert "unavailable" in alerts["grid_missing"].message
    fr2 = {**ALL_FRESH, "grid": "stale"}
    alerts2 = {a.key: a for a in derive_alerts(fr2, dry_run=False, decision_outcome=None)}
    assert "delayed" in alerts2["grid_stale"].message
