"""#118: Tibber → last-good → fixed-tariff backup; never silent [] when backup is set."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from ems.alerts import data_quality, prices_ok_for_quality
from ems.domain import BatteryIntent
from ems.planner.rule_based import PlannerConfig, plan_rule_based
from ems.planner.summer import SummerConfig, plan_summer
from ems.price_quality import validate_price_horizon
from ems.settings import SETTINGS_BY_KEY, effective_settings
from ems.sources.fixed_tariff import FixedTariffPriceSource
from ems.sources.forecast import ForecastSlot
from ems.sources.price_factory import build_price_source
from ems.sources.prices import PriceSlot
from ems.sources.tibber import TibberPriceSource

AMS = ZoneInfo("Europe/Amsterdam")
FIXED_NOW = datetime(2026, 7, 29, 10, 0, tzinfo=UTC)


def _failing_post(*_a, **_k):
    raise OSError("tibber down")


def test_fixed_tariff_slots_are_flat_with_backup_origin():
    src = FixedTariffPriceSource(0.28, tz=AMS, clock=lambda: FIXED_NOW)  # two local days
    slots = src.slots()
    assert len(slots) >= 92  # DST-aware; 96 on non-transition days
    assert {s.eur_per_kwh for s in slots} == {0.28}
    assert all(s.origin == "fixed_backup" for s in slots)
    assert validate_price_horizon(slots, now=FIXED_NOW.astimezone(AMS), site_tz=AMS).ok


def test_tibber_transport_error_without_cache_serves_fixed_backup():
    backup = FixedTariffPriceSource(0.31, tz=AMS, clock=lambda: FIXED_NOW, horizon_slots=16)
    src = TibberPriceSource(
        "tok",
        tz=AMS,
        http_post=_failing_post,
        clock=lambda: FIXED_NOW,
        fallback=backup,
        cache_ttl=timedelta(seconds=0),
        retry_ttl=timedelta(seconds=0),
    )
    slots = src.slots()
    assert slots, "must not return silent [] when backup is configured"
    assert all(isinstance(s, PriceSlot) for s in slots)
    assert {s.eur_per_kwh for s in slots} == {0.31}
    assert all(s.origin == "fixed_backup" for s in slots)
    assert src.serving_fixed_backup is True
    assert src.unavailable_since() is not None


def test_tibber_without_backup_still_returns_empty_when_cold():
    src = TibberPriceSource(
        "tok",
        tz=AMS,
        http_post=_failing_post,
        clock=lambda: FIXED_NOW,
        fallback=None,
        cache_ttl=timedelta(seconds=0),
        retry_ttl=timedelta(seconds=0),
    )
    assert src.slots() == []
    assert src.serving_fixed_backup is False


def test_factory_wires_backup_when_rate_configured():
    eff = effective_settings({
        "connection.use_live_prices": True,
        "prices.tibber_token": "tok",
        "prices.backup_fixed_eur_per_kwh": 0.27,
    })
    src = build_price_source(eff, AMS, use_live=True, http_post=_failing_post)
    assert isinstance(src, TibberPriceSource)
    slots = src.slots()
    assert slots and {s.eur_per_kwh for s in slots} == {0.27}
    assert src.serving_fixed_backup
    assert prices_ok_for_quality(src, operational_requested=True) is False
    # Critical signals must be fresh or data_quality prefers `unsafe` over `price_fallback`.
    assert data_quality(
        {"grid": "fresh", "soc": "fresh"},
        prices_ok=False,
        forecast_ok=True,
    ) == "price_fallback"


def test_backup_setting_default_is_disabled():
    field = SETTINGS_BY_KEY["prices.backup_fixed_eur_per_kwh"]
    assert float(field.default) == 0.0


def test_flat_backup_no_grid_charge_winter_and_summer_above_topup_cap():
    """Winter: flat curve has no arbitrage. Summer: rate above max_topup → no GRID_CHARGE."""
    backup = FixedTariffPriceSource(0.45, tz=AMS, clock=lambda: FIXED_NOW, horizon_slots=96)
    prices = backup.slots()
    now = FIXED_NOW.astimezone(AMS)

    winter = plan_rule_based(prices, now, PlannerConfig(), soc_pct=50.0)
    assert all(
        s.intent is not BatteryIntent.GRID_CHARGE_TO_TARGET for s in winter.slots
    )

    # Dark horizon + high flat price above summer top-up cap → no grid charge either.
    forecast = [
        ForecastSlot(start=p.start, p10_w=0.0, p50_w=0.0, p90_w=0.0) for p in prices
    ]
    summer = plan_summer(
        prices,
        forecast,
        now,
        soc_pct=10.0,
        cfg=SummerConfig(
            usable_kwh=10.0,
            target_soc_pct=80.0,
            allow_grid_topup=True,
            max_topup_price_eur_per_kwh=0.30,
        ),
    )
    assert all(
        s.intent is not BatteryIntent.GRID_CHARGE_TO_TARGET for s in summer.slots
    )


def test_serving_backup_marks_prices_unavailable_for_control():
    """Jeroen 2026-09-27: backup prices are reporting-only — control sees unavailable_since."""
    backup = FixedTariffPriceSource(0.20, tz=AMS, clock=lambda: FIXED_NOW, horizon_slots=8)
    src = TibberPriceSource(
        "tok",
        tz=AMS,
        http_post=_failing_post,
        clock=lambda: FIXED_NOW,
        fallback=backup,
        cache_ttl=timedelta(seconds=0),
        retry_ttl=timedelta(seconds=0),
    )
    assert src.slots()
    since = src.unavailable_since()
    assert since is not None
    # Same gate ControlService / DecisionEngine use for #126 Tibber outage → AUTO.
    assert prices_ok_for_quality(src, operational_requested=True) is False
