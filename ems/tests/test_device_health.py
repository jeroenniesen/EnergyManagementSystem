"""B-38 / issue #79 — consumer device-health summary + freshness detail."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from ems.alerts import data_quality, derive_alerts
from ems.device_health import build_device_health, format_source_detail
from ems.freshness import FreshnessTracker
from ems.sources.forecast import MockSolarForecastSource
from ems.sources.solcast import SolcastSource

T0 = datetime(2026, 9, 27, 14, 0, tzinfo=UTC)
AMS = ZoneInfo("Europe/Amsterdam")


def _detail(tr: FreshnessTracker, now: datetime = T0) -> dict:
    return tr.detail_snapshot(now)


def test_format_source_detail_hhmm():
    assert format_source_detail("prices", "14:00") == "prijzen van 14:00"
    assert format_source_detail("grid", None) == "P1-meter"


def test_partially_stale_summary_names_source():
    tr = FreshnessTracker()
    tr.register("battery", "grid", "prices", "forecast")
    tr.mark("battery", T0)
    tr.mark("grid", T0)
    tr.mark("forecast", T0)
    tr.mark("prices", T0 - timedelta(hours=7))  # past prices stale_after (6h)
    health = build_device_health(
        _detail(tr), now=T0, tz=AMS, prices_kind="live", dry_run=True, demo=False,
    )
    assert health["summary"]["label"] == "Deels verouderd"
    assert "prijzen" in health["summary"]["detail"]
    assert health["summary"]["badge"] == "partially_stale"


def test_demo_summary_label():
    tr = FreshnessTracker()
    for sig in ("battery", "grid", "prices", "forecast"):
        tr.register(sig)
        tr.mark(sig, T0)
    health = build_device_health(
        _detail(tr), now=T0, tz=AMS, prices_kind="mock", dry_run=True, demo=True,
    )
    assert health["summary"]["label"] == "Demo"
    assert "niet van jouw huis" in health["summary"]["detail"]


def test_operational_mock_prices_badge_is_honest_not_self_use():
    """Critical label for operational+mock (#79 #4) — no false self-use / 'je hoeft niets' claim."""
    tr = FreshnessTracker()
    for sig in ("battery", "grid", "prices", "forecast"):
        tr.register(sig)
        tr.mark(sig, T0)
    health = build_device_health(
        _detail(tr), now=T0, tz=AMS, prices_kind="mock", dry_run=False,
        operational_mock_prices=True, demo=False,
    )
    assert health["summary"]["badge"] == "mock_prices"
    assert health["summary"]["severity"] == "critical"
    detail = health["summary"]["detail"].lower()
    assert "demoprijzen" in detail or "tibber" in detail
    assert "zelfverbruik" not in detail
    assert "je hoeft niets" not in detail
    # data_quality badge path: not complete when armed on mock prices.
    fr = {"grid": "fresh", "soc": "fresh", "battery": "fresh", "solar": "fresh"}
    assert data_quality(
        fr, prices_ok=True, forecast_ok=True, prices_live=False, operational=True,
    ) == "price_fallback"
    # No mock_prices alert from this PR — #126 / PR #148 owns enforcement alerts.
    alerts = derive_alerts(fr, dry_run=False, decision_outcome=None)
    assert not any(a.key == "mock_prices" for a in alerts)


def test_prices_forecast_alerts_have_honest_three_answers():
    fr = {"grid": "fresh", "soc": "fresh", "prices": "stale", "forecast": "stale"}
    alerts = derive_alerts(fr, dry_run=False, decision_outcome=None)
    by_key = {a.key: a for a in alerts}
    for key in ("prices_stale", "forecast_stale"):
        a = by_key[key]
        assert a.severity == "warning"
        assert "signal delayed" not in a.message.lower()
        # May mention self-use only to deny it — never claim a fallback/aim.
        assert "falls back" not in a.safe.lower()
        assert "aims for" not in a.ems_doing.lower()
        assert "fallback" not in a.ems_doing.lower()
        assert a.action
        assert a.ems_doing


def test_battery_unreachable_downgrades_row():
    tr = FreshnessTracker()
    for sig in ("battery", "grid", "prices", "forecast"):
        tr.register(sig)
        tr.mark(sig, T0)
    health = build_device_health(
        _detail(tr), now=T0, tz=AMS, battery_reachable=False, prices_kind="live", demo=False,
    )
    batt = next(s for s in health["sources"] if s["key"] == "battery")
    assert batt["state"] == "missing"
    assert batt["note"] == "niet bereikbaar"


def test_freshness_per_signal_thresholds_forecast_days_old():
    tr = FreshnessTracker()
    tr.register("forecast")
    tr.mark("forecast", T0 - timedelta(days=2))
    assert tr.state("forecast", T0).value == "stale"
    assert tr.detail_snapshot(T0)["forecast"]["age_seconds"] == 2 * 86400


def test_solcast_overnight_gap_not_labeled_cached():
    """Healthy 12h overnight gap (after 19:00 refresh) must not look like a reserve forecast."""
    clock = {"t": T0}

    def _now():
        return clock["t"]

    src = SolcastSource(
        api_key="k", resource_id="r", tz=AMS,
        fallback=MockSolarForecastSource(AMS, clock=_now),
        clock=_now,
        http_get=lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("offline")),
    )
    slots = MockSolarForecastSource(AMS, clock=_now).slots()
    src._cache = (T0 - timedelta(hours=12), slots)
    src._last_fetch_at = T0 - timedelta(hours=12)
    out = src._serve_cache()  # not a missed refresh
    assert out is not None
    assert src.source_label == "solcast"


def test_solcast_days_old_or_missed_refresh_is_cached():
    clock = {"t": T0}

    def _now():
        return clock["t"]

    src = SolcastSource(
        api_key="k", resource_id="r", tz=AMS,
        fallback=MockSolarForecastSource(AMS, clock=_now),
        clock=_now,
        http_get=lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("offline")),
    )
    slots = MockSolarForecastSource(AMS, clock=_now).slots()
    src._cache = (T0 - timedelta(days=2), slots)
    src._last_fetch_at = T0 - timedelta(days=2)
    assert src._serve_cache() is not None
    assert src.source_label == "solcast (cached)"
    src._last_fetch_at = T0 - timedelta(hours=2)
    src._cache = (T0 - timedelta(hours=2), slots)
    assert src._serve_cache(missed_refresh=True) is not None
    assert src.source_label == "solcast (cached)"
