from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from ems.planner.rule_based import plan_rule_based
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
