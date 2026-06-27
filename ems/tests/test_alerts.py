from ems.alerts import data_quality, derive_alerts

ALL_FRESH = {"grid": "fresh", "solar": "fresh", "ev": "fresh", "battery": "fresh", "soc": "fresh"}


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
