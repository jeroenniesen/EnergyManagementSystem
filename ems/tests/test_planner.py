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


def test_replanned_mid_peak_buys_the_valley_before_the_next_peak():
    # Live bug (B-30, 2026-07-02): replanned while the evening peak was already in progress, the
    # planner only shopped for charge slots BEFORE the first profitable peak — an empty window —
    # and skipped a deeply profitable €0.14 valley ahead of the NEXT evening's peak.
    start = datetime(2026, 7, 1, 21, 0, tzinfo=AMS)  # first peak starts 21:00
    now = start + timedelta(minutes=20)  # replan at 21:20, mid-peak

    def block(offset_h, n, price):
        return [PriceSlot(start + timedelta(hours=offset_h, minutes=15 * i), price)
                for i in range(n)]

    prices = (
        block(0, 12, 0.30)  # 21:00-23:45 — first peak, in progress
        + block(3, 40, 0.25)  # 00:00-09:45 — shoulder (unprofitable to arbitrage)
        + block(13, 32, 0.14)  # 10:00-17:45 — the valley
        + block(21, 12, 0.30)  # 18:00-20:45 — the next evening peak
    )
    load = {p.start: 1000.0 for p in prices}  # ~1 kW house load, incl. through both peaks
    plan = plan_rule_based(
        prices, now, PlannerConfig(), soc_pct=50.0, load_w_by=load,
        usable_kwh=10.8, reserve_soc_pct=10.0, max_charge_w=4000.0,
    )
    charge = [s for s in plan.slots if s.intent is BatteryIntent.GRID_CHARGE_TO_TARGET]
    assert charge, "mid-peak replan must still buy the upcoming valley for the next peak"
    valley_lo, valley_hi = start + timedelta(hours=13), start + timedelta(hours=21)
    assert all(valley_lo <= s.start < valley_hi for s in charge), (
        "top-up must land in the cheap valley, not the shoulder or a peak")
    last_peak_start = prices[-12].start
    assert all(s.deadline is not None and s.deadline >= s.start for s in charge), (
        "a charge slot's deadline (the peak it feeds) must not lie in the past")
    assert all(s.deadline <= last_peak_start for s in charge)


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
