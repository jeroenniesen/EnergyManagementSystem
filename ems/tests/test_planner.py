import hashlib
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from ems.domain import BatteryIntent
from ems.planner.reason import build_decision_reason
from ems.planner.rule_based import PlannerConfig, late_pack_charge_starts, plan_rule_based
from ems.planner.schedule import SLOT
from ems.sources.prices import MockPriceSource, PriceSlot

AMS = ZoneInfo("Europe/Amsterdam")
MIDNIGHT = datetime(2026, 6, 27, 0, 0, tzinfo=AMS)


def _flat(now, n=96, price=0.20):
    return [PriceSlot(start=now + i * timedelta(minutes=15), eur_per_kwh=price) for i in range(n)]


def _arbitrage_prices():
    return MockPriceSource(AMS, clock=lambda: MIDNIGHT).slots()


def test_mark_floor_holds_leaves_winter_arbitrage_structure_intact():
    """#312: floor-hold rewrite is a pure post-pass — planner output shape unchanged."""
    from ems.planner.projection import BatteryModel, mark_floor_holds, project_energy

    prices = _arbitrage_prices()
    plan = plan_rule_based(prices, MIDNIGHT + timedelta(hours=18), soc_pct=10.5)
    assert plan.slots
    model = BatteryModel(
        usable_kwh=10.0, max_charge_w=4000.0, max_discharge_w=4000.0,
        round_trip_efficiency=0.9, reserve_soc_pct=10.0,
    )
    load_by = {s.start: 1000.0 for s in plan.slots}
    proj = project_energy(
        plan.slots, start_soc_pct=10.5, solar_w_by={}, load_w_by=load_by, model=model,
    )
    marked = mark_floor_holds(plan, proj, 10.0, start_soc_pct=10.5)
    assert len(marked.slots) == len(plan.slots)
    for ps, ms, pj in zip(plan.slots, marked.slots, proj, strict=True):
        if pj.battery_w > 0:
            assert ms.intent is ps.intent  # real discharge — never floor-hold (#312 r1 / AC5)


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


def test_late_pack_prefers_strictly_cheaper_early_window():
    """Pure helper: a unique cheaper early valley beats a later plateau
    (cost order, not always-late)."""
    t0 = datetime(2026, 10, 5, 8, 0, tzinfo=AMS)
    prices = (
        [PriceSlot(t0 + i * SLOT, 0.10) for i in range(4)]
        + [PriceSlot(t0 + (4 + i) * SLOT, 0.20) for i in range(12)]
    )
    starts = late_pack_charge_starts(
        prices, 4, price_of=lambda p: p.eur_per_kwh, max_buy=0.30,
    )
    assert starts == {p.start for p in prices[:4]}, (
        "strictly cheaper early window must win over a later plateau"
    )


def test_winter_late_pack_shifts_charge_past_early_medium_cheap():
    """Live pattern 2026-10-05: early medium-cheap (€0.23) + deeper midday valley (€0.17).

    Cheapest-first marks both; execution then starts at 12:00 @ max_charge_w and finishes
    before the deepest hour is fully used. Late-pack must choose the cost-minimal contiguous
    ~10-slot window 12:30–14:45 (slot-price sum minimum on this curve). No battery I/O.
    """
    day = datetime(2026, 10, 5, 8, 0, tzinfo=AMS)  # morning replan, before the valley

    def block(hour: int, n: int, price: float) -> list[PriceSlot]:
        start = datetime(2026, 10, 5, hour, 0, tzinfo=AMS)
        return [PriceSlot(start + i * SLOT, price) for i in range(n)]

    prices = (
        block(8, 16, 0.32)   # morning shoulder (above useful buy)
        + block(12, 4, 0.228)  # early "cheap" — 12:00/12:15 must stay out of the window
        + block(13, 4, 0.172)  # deepest valley
        + block(14, 4, 0.182)  # second valley
        + block(15, 8, 0.239)  # afternoon shoulder (still buyable vs peak)
        + block(17, 8, 0.30)   # pre-peak
        + block(19, 8, 0.449)  # evening peak
    )
    # Peak load sized for demand-sized n_charge == 10 (~2.5 h @ 4 kW / ~9.5 kWh DC shortfall)
    # — locks the live early-start bug shape (not the weaker 5-slot / 13:00-only case).
    load = {
        p.start: (4500.0 if p.eur_per_kwh >= 0.40 else 400.0) for p in prices
    }
    plan = plan_rule_based(
        prices, day, PlannerConfig(charge_slots=12, discharge_slots=24),
        soc_pct=8.0, load_w_by=load, usable_kwh=10.8, reserve_soc_pct=10.0,
        max_charge_w=4000.0,
    )
    charge = [s for s in plan.slots if s.intent is BatteryIntent.GRID_CHARGE_TO_TARGET]
    assert len(charge) == 10, f"expected 10-slot late-pack window, got {len(charge)}"
    first = min(s.start for s in charge)
    last = max(s.start for s in charge)
    expect_first = datetime(2026, 10, 5, 12, 30, tzinfo=AMS)
    expect_last = datetime(2026, 10, 5, 14, 45, tzinfo=AMS)
    assert first == expect_first, (
        f"10-slot cost-min window must start 12:30 (not 12:00/13:00), got {first:%H:%M}"
    )
    assert last == expect_last, (
        f"10-slot cost-min window must end 14:45, got {last:%H:%M}"
    )
    # 12:00 and 12:15 stay ALLOW_SELF_CONSUMPTION (outside the packed window).
    by_start = {s.start: s for s in plan.slots}
    for hhmm in ((12, 0), (12, 15)):
        t = datetime(2026, 10, 5, hhmm[0], hhmm[1], tzinfo=AMS)
        assert by_start[t].intent is BatteryIntent.ALLOW_SELF_CONSUMPTION, (
            f"{t:%H:%M} must not be GRID_CHARGE (early medium-cheap left out)"
        )
    # Contiguous window (mode-switch model still: intent + target + deadline, no power loop).
    starts = sorted(s.start for s in charge)
    assert all(starts[i] + SLOT == starts[i + 1] for i in range(len(starts) - 1))
    assert all(s.deadline is not None and s.target_soc is not None for s in charge)


def _control_signature(plan):
    return [
        (s.intent, s.target_soc, s.target_kwh, s.power_w)
        for s in plan.slots
    ]


def test_buy_pool_golden_plan_slots_unchanged_fixed_arbitrage():
    """AC4: intents/targets unchanged on the fixed-count arbitrage fixture (#315)."""
    plan = plan_rule_based(_arbitrage_prices(), MIDNIGHT)
    digest = hashlib.sha256(repr(_control_signature(plan)).encode()).hexdigest()
    assert digest == (
        "11422461a3e8184006d9b06ae8ed53b34bd78d9457e8f5837a3ecf86870bd99b"
    )


def test_buy_pool_golden_plan_slots_unchanged_late_pack_demand():
    """AC4: demand-sized late-pack fixture — control payload identical (#315)."""
    day = datetime(2026, 10, 5, 8, 0, tzinfo=AMS)

    def block(hour: int, n: int, price: float) -> list[PriceSlot]:
        start = datetime(2026, 10, 5, hour, 0, tzinfo=AMS)
        return [PriceSlot(start + i * SLOT, price) for i in range(n)]

    prices = (
        block(8, 16, 0.32)
        + block(12, 4, 0.228)
        + block(13, 4, 0.172)
        + block(14, 4, 0.182)
        + block(15, 8, 0.239)
        + block(17, 8, 0.30)
        + block(19, 8, 0.449)
    )
    load = {p.start: (4500.0 if p.eur_per_kwh >= 0.40 else 400.0) for p in prices}
    plan = plan_rule_based(
        prices, day, PlannerConfig(charge_slots=12, discharge_slots=24),
        soc_pct=8.0, load_w_by=load, usable_kwh=10.8, reserve_soc_pct=10.0,
        max_charge_w=4000.0,
    )
    digest = hashlib.sha256(repr(_control_signature(plan)).encode()).hexdigest()
    assert digest == (
        "1ecb2fd66948725ff0f6c4d044c230d5b056b785a2467b461f987a314d51c89e"
    )


def test_empty_buy_pool_fixture_summary_in_decision_reason():
    """AC1: empty pool + shortfall → one Dutch summary with max buy and cheapest slot."""
    t0 = datetime(2026, 10, 16, 6, 0, tzinfo=AMS)
    # Pre-peak slots above max_buy, profitable peak, cheap post-peak slots lower break-even only.
    prices = (
        [PriceSlot(t0 + i * SLOT, 0.37) for i in range(32)]
        + [PriceSlot(t0 + 32 * SLOT + i * SLOT, 0.45) for i in range(8)]
        + [PriceSlot(t0 + 40 * SLOT + i * SLOT, 0.05) for i in range(12)]
    )
    load = {p.start: (3500.0 if p.eur_per_kwh >= 0.44 else 500.0) for p in prices}
    plan = plan_rule_based(
        prices, t0, PlannerConfig(horizon_slots=52), soc_pct=5.5, load_w_by=load,
        usable_kwh=10.8, reserve_soc_pct=10.0, max_charge_w=4000.0,
    )
    assert plan.buy_pool is not None
    assert plan.buy_pool.state == "empty"
    assert plan.buy_pool.shortfall_kwh > 0
    assert plan.buy_pool.pool_size == 0
    price_by = {p.start: p.eur_per_kwh for p in prices}
    summary = build_decision_reason(plan, price_by=price_by).summary
    assert "Geen koopmoment" in summary
    assert "max-inkoopprijs" in summary
    assert plan.buy_pool.cheapest_slot is not None
    assert plan.buy_pool.next_chance_start is None
    assert f"{plan.buy_pool.cheapest_slot.eur_per_kwh:.2f}".replace(".", ",") in summary
    # Wall clock of the Amsterdam slot, not UTC.
    assert plan.buy_pool.cheapest_slot.start.strftime("%H:%M") in summary
    assert "€0,27" in summary
    assert summary.count("Geen koopmoment") == 1
    assert all("Geen koopmoment" not in s.reason for s in plan.slots)


def test_buy_pool_not_needed_when_nothing_to_buy_and_absent_without_prices():
    """n_charge == 0 → not_needed; an empty future price list leaves buy_pool unset."""
    t0 = datetime(2026, 10, 16, 6, 0, tzinfo=AMS)
    prices = (
        [PriceSlot(t0 + i * SLOT, 0.10) for i in range(20)]
        + [PriceSlot(t0 + 20 * SLOT + i * SLOT, 0.50) for i in range(8)]
    )
    load = {p.start: (800.0 if p.eur_per_kwh >= 0.40 else 200.0) for p in prices}
    full = plan_rule_based(
        prices, t0, PlannerConfig(horizon_slots=28), soc_pct=100.0, load_w_by=load,
        usable_kwh=10.8, reserve_soc_pct=10.0, max_charge_w=4000.0,
    )
    assert full.buy_pool is not None
    assert full.buy_pool.state == "not_needed"
    assert full.buy_pool.shortfall_kwh <= 1e-9
    assert not any(s.intent is BatteryIntent.GRID_CHARGE_TO_TARGET for s in full.slots)
    empty = plan_rule_based([], t0, PlannerConfig())
    assert empty.slots == ()
    assert empty.buy_pool is None


def test_short_cheap_pool_stays_open_not_daily_cap():
    """A partial cheap window is ``open``. ``cap`` is the daily kWh ceiling (#317), not this."""
    t0 = datetime(2026, 10, 16, 6, 0, tzinfo=AMS)
    # One pre-peak slot under max_buy (~€0.27); the rest sit above it. Post-peak €0.05
    # does not refill the pool. n_charge stays above that single slot.
    prices = (
        [PriceSlot(t0, 0.20)]
        + [PriceSlot(t0 + (i + 1) * SLOT, 0.37) for i in range(31)]
        + [PriceSlot(t0 + 32 * SLOT + i * SLOT, 0.45) for i in range(8)]
        + [PriceSlot(t0 + 40 * SLOT + i * SLOT, 0.05) for i in range(12)]
    )
    load = {p.start: (3500.0 if p.eur_per_kwh >= 0.44 else 500.0) for p in prices}
    plan = plan_rule_based(
        prices, t0, PlannerConfig(horizon_slots=52), soc_pct=5.5, load_w_by=load,
        usable_kwh=10.8, reserve_soc_pct=10.0, max_charge_w=4000.0,
    )
    assert plan.buy_pool is not None
    assert plan.buy_pool.pool_size == 1
    assert plan.buy_pool.shortfall_kwh > 1
    assert plan.buy_pool.state == "open"
    assert plan.buy_pool.next_chance_start is not None


def test_winter_daily_grid_charge_cap_is_12_kwh():
    """AC1: cap 12 → the winter plan never schedules more than 12 kWh of grid charge."""
    t0 = datetime(2026, 1, 15, 0, 0, tzinfo=AMS)
    prices = (
        [PriceSlot(t0 + i * SLOT, 0.05) for i in range(40)]
        + [PriceSlot(t0 + (40 + i) * SLOT, 0.80) for i in range(16)]
    )
    cfg = PlannerConfig(
        charge_slots=30, discharge_slots=8, round_trip_efficiency=1.0,
        degradation_eur_per_kwh=0.0, risk_margin_eur_per_kwh=0.0,
        max_daily_grid_charge_kwh=12.0,
    )
    plan = plan_rule_based(prices, t0, cfg, max_charge_w=4000.0)
    charged = [s for s in plan.slots if s.intent is BatteryIntent.GRID_CHARGE_TO_TARGET]
    # Fixed-count path has no target_kwh; 4 kW × 0.25 h × η=1 = 1 kWh/slot.
    assert len(charged) * 1.0 <= 12.0 + 1e-9
    assert len(charged) == 12
    dropped = [s for s in plan.slots if s.reason.startswith("Dagplafond")]
    assert dropped
    assert all(s.intent is BatteryIntent.ALLOW_SELF_CONSUMPTION for s in dropped)
    assert "Dagplafond 12 kWh bereikt" in dropped[0].reason


def test_winter_already_bought_8_kwh_leaves_at_most_4():
    """AC2: injected already-bought history of 8 kWh, cap 12 → ≤ 4 kWh new."""
    t0 = datetime(2026, 1, 15, 0, 0, tzinfo=AMS)
    prices = (
        [PriceSlot(t0 + i * SLOT, 0.05) for i in range(40)]
        + [PriceSlot(t0 + (40 + i) * SLOT, 0.80) for i in range(16)]
    )
    cfg = PlannerConfig(
        charge_slots=30, discharge_slots=8, round_trip_efficiency=1.0,
        degradation_eur_per_kwh=0.0, risk_margin_eur_per_kwh=0.0,
        max_daily_grid_charge_kwh=12.0, already_bought_today_kwh=8.0,
    )
    plan = plan_rule_based(prices, t0, cfg, max_charge_w=4000.0)
    charged = [s for s in plan.slots if s.intent is BatteryIntent.GRID_CHARGE_TO_TARGET]
    assert len(charged) <= 4
    assert len(charged) == 4
