"""Adaptive demand-aware charger: peak-shave from cheap pre-peak charging, and DON'T grid-charge
when upcoming solar will cover the need (the overnight target-chasing bug the backtest exposed)."""
from datetime import UTC, datetime

from ems.domain import BatteryIntent
from ems.planner.adaptive import AdaptiveConfig, plan_adaptive
from ems.planner.schedule import SLOT
from ems.sources.forecast import ForecastSlot
from ems.sources.prices import PriceSlot

T0 = datetime(2026, 6, 28, 12, 0, tzinfo=UTC)


def _prices(eur: list[float]) -> list[PriceSlot]:
    return [PriceSlot(T0 + i * SLOT, e) for i, e in enumerate(eur)]


def _fc(watts: list[float]) -> list[ForecastSlot]:
    return [ForecastSlot(T0 + i * SLOT, w, w, w) for i, w in enumerate(watts)]


def _load(watts: list[float]) -> dict:
    return {T0 + i * SLOT: w for i, w in enumerate(watts)}


def _cfg(**kw) -> AdaptiveConfig:
    base = dict(usable_kwh=10.0, reserve_soc_pct=10.0, round_trip_efficiency=1.0,
                max_charge_w=4000.0)
    base.update(kw)
    return AdaptiveConfig(**base)


def test_adaptive_plan_exposes_buy_pool_open_when_charging():
    prices = _prices([0.10] * 4 + [0.40] * 4)
    fc = _fc([0.0] * 8)
    load = _load([200.0] * 4 + [3000.0] * 4)
    plan = plan_adaptive(prices, fc, T0, soc_pct=15.0, load_w_by=load, cfg=_cfg())
    assert plan.buy_pool is not None
    assert plan.buy_pool.state == "open"
    assert plan.buy_pool.pool_size > 0
    assert plan.buy_pool.shortfall_kwh > 0


def test_peak_shave_charges_cheap_before_the_peak_and_discharges_it():
    # Cheap morning (€0.10), expensive evening (€0.40) with a big deficit, no solar, low SoC.
    prices = _prices([0.10] * 4 + [0.40] * 4)
    fc = _fc([0.0] * 8)
    load = _load([200.0] * 4 + [3000.0] * 4)
    plan = plan_adaptive(prices, fc, T0, soc_pct=15.0, load_w_by=load, cfg=_cfg())
    charge = [s for s in plan.slots if s.intent is BatteryIntent.GRID_CHARGE_TO_TARGET]
    discharge = [s for s in plan.slots if s.intent is BatteryIntent.DISCHARGE_FOR_LOAD]
    assert charge, "should grid-charge cheaply to cover the peak"
    assert all(s.start < T0 + 4 * SLOT for s in charge)  # only in the cheap pre-peak window
    assert len(discharge) == 4  # the four expensive deficit slots are shaved from the battery


def test_no_grid_charge_when_upcoming_solar_covers_the_need():
    # Same prices/peak, but a strong solar forecast lands in the cheap window -> no grid charge.
    prices = _prices([0.10] * 4 + [0.40] * 4)
    fc = _fc([6000.0] * 4 + [0.0] * 4)  # plenty of sun before the peak
    load = _load([200.0] * 4 + [2000.0] * 4)
    plan = plan_adaptive(prices, fc, T0, soc_pct=60.0, load_w_by=load, cfg=_cfg())
    assert not [s for s in plan.slots if s.intent is BatteryIntent.GRID_CHARGE_TO_TARGET]


def test_already_charged_needs_no_grid():
    prices = _prices([0.10] * 4 + [0.40] * 4)
    fc = _fc([0.0] * 8)
    load = _load([200.0] * 4 + [1000.0] * 4)
    plan = plan_adaptive(prices, fc, T0, soc_pct=95.0, load_w_by=load, cfg=_cfg())
    assert not [s for s in plan.slots if s.intent is BatteryIntent.GRID_CHARGE_TO_TARGET]


def test_sizes_charge_to_the_deficit_not_a_fixed_window():
    # A small deficit needs few charge slots; a big one needs more. Demand-aware, not fixed-N.
    prices = _prices([0.10] * 8 + [0.40] * 4)
    fc = _fc([0.0] * 12)
    small = plan_adaptive(prices, fc, T0, soc_pct=40.0,
                          load_w_by=_load([100.0] * 8 + [1200.0] * 4), cfg=_cfg())
    big = plan_adaptive(prices, fc, T0, soc_pct=15.0,
                        load_w_by=_load([100.0] * 8 + [3500.0] * 4), cfg=_cfg())
    n_small = sum(1 for s in small.slots if s.intent is BatteryIntent.GRID_CHARGE_TO_TARGET)
    n_big = sum(1 for s in big.slots if s.intent is BatteryIntent.GRID_CHARGE_TO_TARGET)
    assert n_big > n_small


def test_charge_and_discharge_slots_carry_the_energy_contract():
    # charge slots → target SoC + power + reserve floor + deadline; discharge slots → floor + power.
    prices = _prices([0.10] * 4 + [0.40] * 4)
    fc = _fc([0.0] * 8)
    load = _load([200.0] * 4 + [3000.0] * 4)
    plan = plan_adaptive(prices, fc, T0, soc_pct=15.0, load_w_by=load, cfg=_cfg(reserve_soc_pct=10))
    # Direct call: the all-seasons planner leaves strategy unlabelled (build_plan stamps it).
    assert plan.strategy is None and plan.target_soc is not None and plan.deadline is not None
    for s in plan.slots:
        if s.intent is BatteryIntent.GRID_CHARGE_TO_TARGET:
            assert s.target_soc is not None and 0 < s.target_soc <= 100
            assert s.power_w == 4000.0 and s.floor_soc == 10.0 and s.deadline is not None
        if s.intent is BatteryIntent.DISCHARGE_FOR_LOAD:
            assert s.floor_soc == 10.0 and s.power_w is not None


def test_below_reserve_holds_deficit_slots_until_recovered():
    prices = _prices([0.30, 0.30, 0.10, 0.10, 0.40, 0.40])
    fc = _fc([0.0] * 6)
    load = _load([800.0, 800.0, 800.0, 800.0, 3000.0, 3000.0])
    plan = plan_adaptive(prices, fc, T0, soc_pct=8.0, load_w_by=load, cfg=_cfg())
    assert plan.slots[0].intent is BatteryIntent.HOLD_RESERVE
    assert plan.slots[1].intent is BatteryIntent.HOLD_RESERVE
    assert any(s.intent is BatteryIntent.GRID_CHARGE_TO_TARGET for s in plan.slots)


def test_deficit_slot_that_would_cross_reserve_holds_instead():
    prices = _prices([0.30, 0.30, 0.10, 0.10, 0.40, 0.40])
    fc = _fc([0.0] * 6)
    load = _load([3000.0, 800.0, 800.0, 800.0, 3000.0, 3000.0])
    plan = plan_adaptive(prices, fc, T0, soc_pct=12.0, load_w_by=load, cfg=_cfg())
    # Slot 0's full deficit would cross the reserve floor → hold instead of discharging below it.
    assert plan.slots[0].intent is BatteryIntent.HOLD_RESERVE
    # ...but the hold must NOT freeze the pack into a "hold storm": it recovers on the cheap slots
    # and still shaves the real evening peak (slots 4-5), not holds straight through it.
    assert any(s.intent is BatteryIntent.GRID_CHARGE_TO_TARGET for s in plan.slots)
    assert plan.slots[4].intent is BatteryIntent.DISCHARGE_FOR_LOAD
    assert plan.slots[5].intent is BatteryIntent.DISCHARGE_FOR_LOAD


def test_empty_prices_yields_empty_plan():
    assert plan_adaptive([], [], T0, soc_pct=50.0, load_w_by={}, cfg=_cfg()).slots == ()


def test_late_pack_matches_winter_on_early_medium_plus_deep_valley():
    """Same curve as winter late-pack: early €0.22 shoulder + later €0.10 valley.

    Both planners must mark the cost-minimal contiguous block (12:30–13:45), not the
    earliest-of-cheapest set (12:00 start). An earliest-first regression fails here.
    """
    from ems.planner.rule_based import PlannerConfig, plan_rule_based

    day = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)
    prices = (
        [PriceSlot(day + i * SLOT, 0.22) for i in range(4)]
        + [PriceSlot(day + i * SLOT, 0.10) for i in range(4, 8)]
        + [PriceSlot(day + i * SLOT, 0.45) for i in range(8, 12)]
    )
    load = {p.start: (6000.0 if p.eur_per_kwh >= 0.40 else 0.0) for p in prices}
    fc = [ForecastSlot(p.start, 0.0, 0.0, 0.0) for p in prices]
    winter = plan_rule_based(
        prices, day, PlannerConfig(charge_slots=8, round_trip_efficiency=1.0),
        soc_pct=10.0, load_w_by=load, usable_kwh=10.0, reserve_soc_pct=10.0,
        max_charge_w=4000.0,
    )
    adaptive = plan_adaptive(
        prices, fc, day, soc_pct=10.0, load_w_by=load,
        cfg=_cfg(round_trip_efficiency=1.0),
    )
    winter_charge = {
        s.start for s in winter.slots if s.intent is BatteryIntent.GRID_CHARGE_TO_TARGET
    }
    adaptive_charge = {
        s.start for s in adaptive.slots if s.intent is BatteryIntent.GRID_CHARGE_TO_TARGET
    }
    late = {day + i * SLOT for i in range(2, 8)}  # 12:30–13:45
    assert winter_charge == late
    assert adaptive_charge == winter_charge
    assert day not in adaptive_charge  # 12:00 medium-cheap must stay out


def test_no_contiguous_window_falls_back_to_cheapest_n():
    """Isolated buyable slots: no length-n contiguous window → cheapest n, size stays n_charge."""
    # Three cheap slots split by peaks. Demand sizes n_charge=2; the two cheapest are gapped.
    prices = _prices([0.15, 0.50, 0.11, 0.50, 0.12, 0.50])
    fc = _fc([0.0] * 6)
    load = _load([0.0, 2000.0, 0.0, 2000.0, 0.0, 2000.0])
    plan = plan_adaptive(prices, fc, T0, soc_pct=10.0, load_w_by=load, cfg=_cfg())
    charge = [s for s in plan.slots if s.intent is BatteryIntent.GRID_CHARGE_TO_TARGET]
    assert len(charge) == 2
    assert {s.start for s in charge} == {T0 + 2 * SLOT, T0 + 4 * SLOT}
    assert all(s.start not in {T0 + SLOT, T0 + 3 * SLOT, T0 + 5 * SLOT} for s in charge)
