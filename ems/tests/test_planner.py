from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from ems.domain import BatteryIntent
from ems.planner.rule_based import PlannerConfig, late_pack_charge_starts, plan_rule_based
from ems.planner.schedule import SLOT
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
    # Peak serve-load slots use homeowner "self-consumption:" — not bare "discharge:".
    peak = [s for s in plan.slots if s.intent is BatteryIntent.DISCHARGE_FOR_LOAD]
    assert peak and all(s.reason.lower().startswith("self-consumption:") for s in peak)
    assert all(not s.reason.lower().startswith("discharge:") for s in peak)


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


def test_import_fee_changes_planner_break_even_without_changing_raw_slots():
    prices = _arbitrage_prices()
    baseline = plan_rule_based(prices, MIDNIGHT, PlannerConfig(charge_slots=3, discharge_slots=1))
    fee = plan_rule_based(
        prices, MIDNIGHT,
        PlannerConfig(charge_slots=3, discharge_slots=1, import_fee_eur_per_kwh=0.20),
    )
    assert baseline.slots != fee.slots


def test_negative_raw_price_stays_charge_eligible_with_import_fee():
    prices = [PriceSlot(MIDNIGHT + timedelta(minutes=15 * i), -0.02) for i in range(4)]
    plan = plan_rule_based(
        prices, MIDNIGHT,
        PlannerConfig(charge_slots=2, discharge_slots=1, negative_price_soak=True,
                      import_fee_eur_per_kwh=0.01),
    )
    assert any(s.intent is BatteryIntent.GRID_CHARGE_TO_TARGET for s in plan.slots)


def test_undercharge_commits_honest_partial_target_not_unreachable_shortfall():
    """#162: when too few cheap slots remain before the peak, target_soc is what those slots
    can store — not the full peak shortfall (which used to trip B-22 → AUTO with zero charge)."""
    # Two cheap slots, then an expensive peak with high load; more cheap slots AFTER the peak
    # (so breakeven stays low) but they cannot feed this peak — pool before peak = 2 only.
    prices = []
    for i in range(24):
        if i < 2 or i >= 10:
            price = 0.05
        else:
            price = 0.55
        prices.append(PriceSlot(MIDNIGHT + timedelta(minutes=15 * i), price))
    load = {
        p.start: (4000.0 if 2 <= i < 10 else 300.0) for i, p in enumerate(prices)
    }
    plan = plan_rule_based(
        prices, MIDNIGHT,
        PlannerConfig(charge_slots=12, discharge_slots=12),
        soc_pct=10.0, load_w_by=load, usable_kwh=10.0, reserve_soc_pct=10.0,
        max_charge_w=4000.0,
    )
    charge = [s for s in plan.slots if s.intent is BatteryIntent.GRID_CHARGE_TO_TARGET]
    assert len(charge) == 2, "must still schedule best-effort charge on the 2 pre-peak slots"
    assert plan.target_soc is not None
    # Full shortfall for the 8-slot peak @ 4 kW would need ~94% — honest partial is ~29%.
    assert plan.target_soc < 40.0
    assert all(abs((s.target_soc or 0) - plan.target_soc) < 1e-6 for s in charge)


def test_winter_ev_exogenous_raises_target_and_reason():
    """#181: canned car-load (expected_ev_kwh) enlarges winter top-up vs the same house load alone.
    Validator/guardrails spirit unchanged — still a normal demand-sized winter plan."""
    t0 = datetime(2026, 1, 10, 12, 0, tzinfo=AMS)
    prices = [
        PriceSlot(t0 + i * timedelta(minutes=15), 0.10 if i < 12 else 0.40)
        for i in range(16)
    ]
    # Modest evening peak (~3 kW × 1 h) so baseline shortfall is small; EV addend should clear.
    load = {p.start: (200.0 if i < 12 else 3000.0) for i, p in enumerate(prices)}
    baseline = plan_rule_based(
        prices, t0, PlannerConfig(), soc_pct=50.0, load_w_by=load,
        usable_kwh=10.0, reserve_soc_pct=10.0, max_charge_w=4000.0,
    )
    with_ev = plan_rule_based(
        prices, t0, PlannerConfig(), soc_pct=50.0, load_w_by=load,
        usable_kwh=10.0, reserve_soc_pct=10.0, max_charge_w=4000.0,
        expected_ev_kwh=20.0,
    )
    assert baseline.target_soc is not None and with_ev.target_soc is not None
    assert with_ev.target_soc > baseline.target_soc
    charge = [s for s in with_ev.slots if s.intent is BatteryIntent.GRID_CHARGE_TO_TARGET]
    assert charge
    assert any("EV load expected ~20 kWh" in s.reason for s in charge)
    # Fail-soft: 0 kWh matches baseline (no EV fragment in reasons).
    zero = plan_rule_based(
        prices, t0, PlannerConfig(), soc_pct=50.0, load_w_by=load,
        usable_kwh=10.0, reserve_soc_pct=10.0, max_charge_w=4000.0,
        expected_ev_kwh=0.0,
    )
    assert zero.target_soc == baseline.target_soc
    assert not any("EV load expected" in s.reason for s in zero.slots)


def test_winter_ev_alone_does_not_invent_peak_discharge():
    """EV addend must not create a discharge plan when house peak load is zero (car-guard §4.5)."""
    t0 = datetime(2026, 1, 10, 12, 0, tzinfo=AMS)
    prices = [
        PriceSlot(t0 + i * timedelta(minutes=15), 0.10 if i < 12 else 0.40)
        for i in range(16)
    ]
    load = {p.start: (3000.0 if i < 12 else 0.0) for i, p in enumerate(prices)}
    plan = plan_rule_based(
        prices, t0, PlannerConfig(), soc_pct=20.0, load_w_by=load,
        usable_kwh=10.0, reserve_soc_pct=10.0, max_charge_w=4000.0,
        expected_ev_kwh=30.0,
    )
    assert all(s.intent is BatteryIntent.ALLOW_SELF_CONSUMPTION for s in plan.slots)


def test_late_pack_prefers_later_equal_cost_window():
    """Pure helper: equal-cost contiguous windows tie-break toward the deadline (later)."""
    t0 = datetime(2026, 10, 5, 12, 0, tzinfo=AMS)
    # Two equal valleys of 4 slots separated by a dearer shoulder — both cost the same.
    prices = (
        [PriceSlot(t0 + i * SLOT, 0.20) for i in range(4)]
        + [PriceSlot(t0 + (4 + i) * SLOT, 0.35) for i in range(4)]
        + [PriceSlot(t0 + (8 + i) * SLOT, 0.20) for i in range(4)]
    )
    starts = late_pack_charge_starts(
        prices, 4, price_of=lambda p: p.eur_per_kwh, max_buy=0.30,
    )
    assert starts == {p.start for p in prices[8:12]}, (
        "equal-cost windows must late-pack toward the later valley"
    )


def test_winter_late_pack_shifts_charge_past_early_medium_cheap():
    """Live pattern 2026-10-05: early medium-cheap (€0.23) + deeper midday valley (€0.17).

    Cheapest-first marks both; execution then starts at 12:00 @ max_charge_w and finishes
    before the deepest hour is fully used. Late-pack must choose the cost-minimal contiguous
    window so GRID_CHARGE starts later (toward the valley / peak deadline). No battery I/O.
    """
    day = datetime(2026, 10, 5, 8, 0, tzinfo=AMS)  # morning replan, before the valley

    def block(hour: int, n: int, price: float) -> list[PriceSlot]:
        start = datetime(2026, 10, 5, hour, 0, tzinfo=AMS)
        return [PriceSlot(start + i * SLOT, price) for i in range(n)]

    prices = (
        block(8, 16, 0.32)   # morning shoulder (above useful buy)
        + block(12, 4, 0.228)  # early "cheap" — must NOT be the charge start
        + block(13, 4, 0.172)  # deepest valley
        + block(14, 4, 0.182)  # second valley
        + block(15, 8, 0.239)  # afternoon shoulder (still buyable vs peak)
        + block(17, 8, 0.30)   # pre-peak
        + block(19, 8, 0.449)  # evening peak
    )
    # Peak load sized so demand-sized n_charge ≈ 10 slots (~2.5 h @ 4 kW) — same shape as
    # the live early-start bug (full-ish pack from floor before 19:00).
    load = {
        p.start: (2200.0 if p.eur_per_kwh >= 0.40 else 400.0) for p in prices
    }
    plan = plan_rule_based(
        prices, day, PlannerConfig(charge_slots=12, discharge_slots=24),
        soc_pct=8.0, load_w_by=load, usable_kwh=10.8, reserve_soc_pct=10.0,
        max_charge_w=4000.0,
    )
    charge = [s for s in plan.slots if s.intent is BatteryIntent.GRID_CHARGE_TO_TARGET]
    assert charge, "must still schedule a pre-peak grid charge"
    first = min(s.start for s in charge)
    early_cheap = datetime(2026, 10, 5, 12, 0, tzinfo=AMS)
    deep_valley = datetime(2026, 10, 5, 13, 0, tzinfo=AMS)
    assert first > early_cheap, (
        f"charge must late-pack past the early €0.23 hour, got first={first:%H:%M}"
    )
    assert first <= deep_valley, (
        f"late-pack window should still cover the deep valley, got first={first:%H:%M}"
    )
    # Earliest marked hour must not be the 12:00 medium-cheap block.
    assert all(s.start >= datetime(2026, 10, 5, 12, 30, tzinfo=AMS) for s in charge)
    # Contiguous window (mode-switch model still: intent + target + deadline, no power loop).
    starts = sorted(s.start for s in charge)
    assert all(starts[i] + SLOT == starts[i + 1] for i in range(len(starts) - 1))
    assert all(s.deadline is not None and s.target_soc is not None for s in charge)
