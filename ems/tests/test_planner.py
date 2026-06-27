from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from ems.domain import BatteryIntent
from ems.planner.rule_based import PlannerConfig, plan_rule_based
from ems.sources.prices import MockPriceSource, PriceSlot

AMS = ZoneInfo("Europe/Amsterdam")
MIDNIGHT = datetime(2026, 6, 27, 0, 0, tzinfo=AMS)


def _flat(now, n=96, price=0.20):
    return [PriceSlot(start=now + i * timedelta(minutes=15), eur_per_kwh=price) for i in range(n)]


def _arbitrage_prices():
    return MockPriceSource(AMS, clock=lambda: MIDNIGHT).slots()


def test_arbitrage_day_has_charge_discharge_and_hold():
    plan = plan_rule_based(_arbitrage_prices(), MIDNIGHT)
    intents = {s.intent for s in plan.slots}
    assert BatteryIntent.GRID_CHARGE_TO_TARGET in intents
    assert BatteryIntent.DISCHARGE_FOR_LOAD in intents
    assert BatteryIntent.HOLD_RESERVE in intents


def test_flat_prices_is_no_trade():
    plan = plan_rule_based(_flat(MIDNIGHT), MIDNIGHT)
    assert plan.slots  # non-empty
    assert all(s.intent is BatteryIntent.ALLOW_SELF_CONSUMPTION for s in plan.slots)


def test_charge_slots_are_cheaper_than_discharge_slots():
    prices = _arbitrage_prices()
    plan = plan_rule_based(prices, MIDNIGHT)
    price_by_start = {p.start: p.eur_per_kwh for p in prices}
    charge = [price_by_start[s.start] for s in plan.slots
              if s.intent is BatteryIntent.GRID_CHARGE_TO_TARGET]
    discharge = [price_by_start[s.start] for s in plan.slots
                 if s.intent is BatteryIntent.DISCHARGE_FOR_LOAD]
    assert max(charge) < min(discharge)  # never buy dearer than we sell


def test_intent_at_returns_covering_slot():
    plan = plan_rule_based(_arbitrage_prices(), MIDNIGHT)
    s = plan.intent_at(MIDNIGHT)
    assert s is not None
    assert s.start <= MIDNIGHT < s.start + timedelta(minutes=15)


def test_empty_prices_gives_empty_plan():
    plan = plan_rule_based([], MIDNIGHT)
    assert plan.slots == ()


def test_no_charge_after_last_discharge():
    # A cheap slot that occurs AFTER all profitable peaks must not be scheduled to charge
    # (nothing to discharge into -> no wasted cycle).
    slots = [
        PriceSlot(MIDNIGHT + timedelta(minutes=0), 0.05),  # cheap A -> charge
        PriceSlot(MIDNIGHT + timedelta(minutes=15), 0.05),  # cheap A -> charge
        PriceSlot(MIDNIGHT + timedelta(minutes=30), 0.50),  # peak -> discharge
        PriceSlot(MIDNIGHT + timedelta(minutes=45), 0.05),  # cheap B AFTER peak -> must be AUTO
    ]
    plan = plan_rule_based(slots, MIDNIGHT, PlannerConfig(charge_slots=3, discharge_slots=1))
    by_start = {s.start: s.intent for s in plan.slots}
    assert by_start[MIDNIGHT] is BatteryIntent.GRID_CHARGE_TO_TARGET
    assert by_start[MIDNIGHT + timedelta(minutes=30)] is BatteryIntent.DISCHARGE_FOR_LOAD
    assert by_start[MIDNIGHT + timedelta(minutes=45)] is BatteryIntent.ALLOW_SELF_CONSUMPTION
