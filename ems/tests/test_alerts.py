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


def test_unconfirmed_outcome_yields_warning_alert():
    alerts = derive_alerts(ALL_FRESH, dry_run=False, decision_outcome="unconfirmed")
    a = next(x for x in alerts if x.key == "battery_command_unconfirmed")
    assert a.severity == "warning"
    assert "Indevolt" in a.message


def test_price_horizon_incomplete_alert():
    alerts = derive_alerts(
        ALL_FRESH, dry_run=False, decision_outcome=None, price_horizon_ok=False,
    )
    a = next(x for x in alerts if x.key == "price_horizon_incomplete")
    assert a.severity == "warning"
    assert "Tibber" in a.message


def test_control_overrun_alert():
    alerts = derive_alerts(
        ALL_FRESH, dry_run=False, decision_outcome=None, control_overrun=True,
    )
    a = next(x for x in alerts if x.key == "control_overrun")
    assert a.severity == "warning"
    assert "overrun" in a.message.lower() or "too long" in a.message.lower()


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


# --- B-37 / B-09 calm actionable warnings: every alert answers what / safe / EMS doing / next ---

def _collect_all_alerts() -> list[Alert]:
    """Every distinct alert `derive_alerts` can emit, deduplicated by key."""
    seen: dict[str, Alert] = {}
    for dry_run in (False, True):
        for outcome in (None, "failed_recovered", "failed_unrecovered", "unconfirmed"):
            for a in derive_alerts(ALL_FRESH, dry_run=dry_run, decision_outcome=outcome):
                seen[a.key] = a
    for sig in SIGNALS:
        for state in STATES:
            fr = {**ALL_FRESH, sig: state}
            for a in derive_alerts(fr, dry_run=False, decision_outcome=None):
                seen[a.key] = a
    for a in derive_alerts(
        ALL_FRESH, dry_run=False, decision_outcome=None, price_horizon_ok=False,
    ):
        seen[a.key] = a
    for a in derive_alerts(
        ALL_FRESH, dry_run=False, decision_outcome=None, control_overrun=True,
    ):
        seen[a.key] = a
    return list(seen.values())


def test_every_alert_has_non_empty_safe_action_and_ems_doing():
    alerts = _collect_all_alerts()
    # dry_run + 3 battery-write outcomes + 5 signals×2 states + price_horizon + overrun = 16.
    assert len(alerts) == 16
    for a in alerts:
        assert a.safe.strip(), f"{a.key} has no safe answer"
        assert a.action.strip(), f"{a.key} has no action"
        assert a.ems_doing.strip(), f"{a.key} has no ems_doing"


def test_stale_without_confirmed_auto_never_says_safe_mode():
    """B-09 / #73: without confirmed AUTO, stale/missing critical copy must not say 'safe mode'."""
    for sig in ("grid", "soc"):
        for state in STATES:
            fr = {**ALL_FRESH, sig: state}
            for a in derive_alerts(
                fr, dry_run=False, decision_outcome=None, confirmed_auto=False,
            ):
                blob = f"{a.message} {a.safe} {a.action} {a.ems_doing}".lower()
                assert "safe mode" not in blob, f"{a.key} mentions safe mode without AUTO: {blob}"


def test_stale_with_confirmed_auto_may_mention_self_use_not_battery_is_safe():
    fr = {**ALL_FRESH, "grid": "stale"}
    alerts = derive_alerts(fr, dry_run=False, decision_outcome=None, confirmed_auto=True)
    a = next(x for x in alerts if x.key == "grid_stale")
    assert "self-use" in a.safe.lower()
    blob = f"{a.message} {a.safe} {a.action} {a.ems_doing}".lower()
    assert "the battery is safe" not in blob
    assert "nothing changes" not in blob


def test_alert_copy_never_mentions_home_assistant():
    """B-09 / #73: name the real device (P1 meter, battery), not Home Assistant."""
    for a in _collect_all_alerts():
        blob = f"{a.message} {a.safe} {a.action} {a.ems_doing}"
        assert "Home Assistant" not in blob, f"{a.key} still points at Home Assistant: {blob}"


def test_alert_copy_style_guard():
    """No alert may describe a condition without a next step: the action either states an
    automatic behaviour (so 'nothing needed' is an honest, explicit answer) or points to a
    concrete place to act, and always reads as a complete sentence."""
    for a in _collect_all_alerts():
        action = a.action.strip()
        safe = a.safe.strip()
        ems_doing = a.ems_doing.strip()
        assert len(action) <= 180, f"{a.key} action too long ({len(action)})"
        assert len(safe) <= 180, f"{a.key} safe too long ({len(safe)})"
        assert len(ems_doing) <= 180, f"{a.key} ems_doing too long ({len(ems_doing)})"
        assert action.endswith((".", "!", "?")), f"{a.key} action isn't a full sentence: {action!r}"
        automatic = "nothing needed" in action.lower() or "automatically" in action.lower()
        concrete_place = any(
            term in action for term in (
                "Settings", "Indevolt", "P1", "HomeWizard", "support", "Manual control", "Tibber",
            )
        ) or "check" in action.lower()
        assert automatic or concrete_place, (
            f"{a.key} action has no automatic behaviour and no concrete place to act: {action!r}"
        )


def test_signal_alerts_mention_state_appropriately():
    # Regression: existing message copy/format behaviour must be untouched by the new fields.
    fr = {**ALL_FRESH, "grid": "missing"}
    alerts = {a.key: a for a in derive_alerts(fr, dry_run=False, decision_outcome=None)}
    assert "unavailable" in alerts["grid_missing"].message
    assert "P1" in alerts["grid_missing"].message
    fr2 = {**ALL_FRESH, "grid": "stale"}
    alerts2 = {a.key: a for a in derive_alerts(fr2, dry_run=False, decision_outcome=None)}
    assert "delayed" in alerts2["grid_stale"].message
