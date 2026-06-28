"""Strategy selection (auto by season, or forced) + the dispatcher that maps a strategy name to
its planner. Both planners emit the same Plan, so everything downstream is unchanged."""
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from ems.domain import BatteryIntent
from ems.planner.rule_based import PlannerConfig
from ems.planner.schedule import SLOT
from ems.planner.strategy import build_plan, select_strategy
from ems.planner.summer import SummerConfig
from ems.sources.forecast import ForecastSlot
from ems.sources.prices import PriceSlot

AMS = ZoneInfo("Europe/Amsterdam")


def test_explicit_mode_wins():
    jan = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)
    assert select_strategy(jan, "summer", AMS) == "summer"  # forced summer in January
    assert select_strategy(datetime(2026, 7, 1, tzinfo=UTC), "winter", AMS) == "winter"


def test_auto_picks_by_season():
    assert select_strategy(datetime(2026, 6, 28, tzinfo=UTC), "auto", AMS) == "summer"
    assert select_strategy(datetime(2026, 1, 10, tzinfo=UTC), "auto", AMS) == "winter"
    assert select_strategy(datetime(2026, 10, 5, tzinfo=UTC), "auto", AMS) == "winter"


def test_auto_uses_local_month_not_utc():
    # 2026-03-31 23:30 UTC is 2026-04-01 01:30 in Amsterdam (summer DST) -> April -> summer.
    near_midnight = datetime(2026, 3, 31, 23, 30, tzinfo=UTC)
    assert select_strategy(near_midnight, "auto", AMS) == "summer"


def test_unknown_mode_falls_back_to_auto():
    assert select_strategy(datetime(2026, 7, 1, tzinfo=UTC), "banana", AMS) == "summer"
    assert select_strategy(datetime(2026, 1, 1, tzinfo=UTC), None, AMS) == "winter"


def _prices(n):
    t0 = datetime(2026, 6, 28, 12, 0, tzinfo=UTC)
    # cheap morning, pricey afternoon -> winter planner will find an arbitrage spread
    return [PriceSlot(t0 + i * SLOT, 0.05 if i < n // 2 else 0.40) for i in range(n)]


def test_build_plan_dispatches_to_summer():
    t0 = datetime(2026, 6, 28, 12, 0, tzinfo=UTC)
    prices = _prices(16)
    fc = [ForecastSlot(t0 + i * SLOT, 3000.0, 3000.0, 3000.0) for i in range(16)]  # sunny
    plan = build_plan(
        "summer", prices=prices, forecast=fc, now=t0, soc_pct=50.0,
        winter_cfg=PlannerConfig(), summer_cfg=SummerConfig(usable_kwh=10.0, target_soc_pct=80.0),
    )
    # Sunny summer -> solar-first, no forced grid charge.
    assert BatteryIntent.GRID_CHARGE_TO_TARGET not in {s.intent for s in plan.slots}
    assert plan.slots  # non-empty


def test_build_plan_dispatches_to_winter():
    t0 = datetime(2026, 1, 10, 0, 0, tzinfo=UTC)
    # A clear cheap window (>= the 12-slot charge default) then an expensive peak -> arbitrage.
    prices = [PriceSlot(t0 + i * SLOT, 0.05 if i < 24 else 0.40) for i in range(48)]
    plan = build_plan(
        "winter", prices=prices, forecast=[], now=t0, soc_pct=50.0,
        winter_cfg=PlannerConfig(), summer_cfg=SummerConfig(usable_kwh=10.0, target_soc_pct=80.0),
    )
    # Winter arbitrage charges the cheap window for the expensive peak.
    assert BatteryIntent.GRID_CHARGE_TO_TARGET in {s.intent for s in plan.slots}
