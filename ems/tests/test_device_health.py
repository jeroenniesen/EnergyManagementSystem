"""B-38 / issue #79 — consumer device-health summary + freshness detail."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from ems.alerts import data_quality, derive_alerts
from ems.device_health import build_device_health, format_source_detail
from ems.freshness import FreshnessTracker
from ems.sources.forecast import MockSolarForecastSource
from ems.sources.prices import MockPriceSource
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


def test_operational_mock_prices_critical_not_complete():
    fr = {"grid": "fresh", "soc": "fresh", "battery": "fresh", "solar": "fresh"}
    assert data_quality(
        fr, prices_ok=True, forecast_ok=True, prices_live=False, operational=True,
    ) == "price_fallback"
    assert data_quality(
        fr, prices_ok=True, forecast_ok=True, prices_live=False, operational=False,
    ) == "complete"
    alerts = derive_alerts(
        fr, dry_run=False, decision_outcome=None, prices_live=False, confirmed_auto=True,
    )
    assert any(a.key == "mock_prices" and a.severity == "critical" for a in alerts)


def test_watch_only_mock_prices_no_critical_alert():
    fr = {"grid": "fresh", "soc": "fresh"}
    alerts = derive_alerts(fr, dry_run=True, decision_outcome=None, prices_live=False)
    assert not any(a.key == "mock_prices" for a in alerts)


def test_freshness_per_signal_thresholds_forecast_days_old():
    tr = FreshnessTracker()
    tr.register("forecast")
    tr.mark("forecast", T0 - timedelta(days=2))
    assert tr.state("forecast", T0).value == "stale"
    assert tr.detail_snapshot(T0)["forecast"]["age_seconds"] == 2 * 86400


def test_solcast_serve_cache_labels_old_cache():
    clock = {"t": T0}

    def _now():
        return clock["t"]

    src = SolcastSource(
        api_key="k", resource_id="r", tz=AMS,
        fallback=MockSolarForecastSource(AMS, clock=_now),
        clock=_now,
        http_get=lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("offline")),
    )
    # Seed an in-memory cache from two days ago, then serve it.
    slots = MockSolarForecastSource(AMS, clock=_now).slots()
    src._cache = (T0 - timedelta(days=2), slots)
    src._last_fetch_at = T0 - timedelta(days=2)
    out = src._serve_cache()
    assert out is not None
    assert src.source_label == "solcast (cached)"
    assert src.issued_at == T0 - timedelta(days=2)


def test_mock_price_source_is_detectable():
    assert isinstance(MockPriceSource(AMS), MockPriceSource)
