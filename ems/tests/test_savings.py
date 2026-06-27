from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from ems.domain import BatteryIntent
from ems.planner.rule_based import plan_rule_based
from ems.planner.schedule import Plan, PlanSlot
from ems.savings import estimate_daily_savings_eur
from ems.sources.prices import MockPriceSource, PriceSlot

AMS = ZoneInfo("Europe/Amsterdam")
MIDNIGHT = datetime(2026, 6, 27, 0, 0, tzinfo=AMS)


def test_arbitrage_day_has_positive_savings():
    prices = MockPriceSource(AMS, clock=lambda: MIDNIGHT).slots()
    plan = plan_rule_based(prices, MIDNIGHT)
    by_start = {p.start: p.eur_per_kwh for p in prices}
    assert estimate_daily_savings_eur(plan, by_start) > 0


def test_no_trade_day_has_zero_savings():
    flat = [PriceSlot(MIDNIGHT + i * timedelta(minutes=15), 0.20) for i in range(96)]
    plan = plan_rule_based(flat, MIDNIGHT)
    by_start = {p.start: p.eur_per_kwh for p in flat}
    assert estimate_daily_savings_eur(plan, by_start) == 0.0


def test_thin_spread_killed_by_efficiency_and_wear_is_zero():
    # Discharge above charge but below charge/eff + degradation + risk -> 0 net (no overclaim).
    s0, s1 = MIDNIGHT, MIDNIGHT + timedelta(minutes=15)
    plan = Plan(
        created_at=MIDNIGHT,
        slots=(
            PlanSlot(s0, BatteryIntent.GRID_CHARGE_TO_TARGET, "charge"),
            PlanSlot(s1, BatteryIntent.DISCHARGE_FOR_LOAD, "discharge"),
        ),
    )
    by_start = {s0: 0.20, s1: 0.21}  # 0.21 < 0.20/0.9 + 0.05 + 0.02 = 0.292
    assert estimate_daily_savings_eur(plan, by_start) == 0.0
