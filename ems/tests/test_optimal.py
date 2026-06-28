"""DP cost-optimizer: produces a valid charge/discharge plan, respects the reserve floor, and is the
yardstick the adaptive heuristic is measured against."""
from datetime import UTC, datetime

from ems.domain import BatteryIntent
from ems.planner.optimal import OptimalConfig, plan_optimal
from ems.planner.projection import BatteryModel, project_energy
from ems.planner.schedule import SLOT
from ems.sources.forecast import ForecastSlot
from ems.sources.prices import PriceSlot

T0 = datetime(2026, 6, 28, 12, 0, tzinfo=UTC)


def _prices(eur):
    return [PriceSlot(T0 + i * SLOT, e) for i, e in enumerate(eur)]


def _fc(watts):
    return [ForecastSlot(T0 + i * SLOT, w, w, w) for i, w in enumerate(watts)]


def _load(watts):
    return {T0 + i * SLOT: w for i, w in enumerate(watts)}


def test_empty_prices_yields_empty_plan():
    cfg = OptimalConfig(usable_kwh=10.0)
    assert plan_optimal([], [], T0, soc_pct=50.0, load_w_by={}, cfg=cfg).slots == ()


def test_charges_cheap_discharges_expensive_and_keeps_reserve():
    # Cheap window then an expensive deficit peak, no solar -> charge cheap, discharge the peak.
    prices = _prices([0.10] * 6 + [0.45] * 6)
    fc = _fc([0.0] * 12)
    load = _load([150.0] * 6 + [2500.0] * 6)
    cfg = OptimalConfig(usable_kwh=10.0, reserve_soc_pct=10.0, round_trip_efficiency=1.0)
    plan = plan_optimal(prices, fc, T0, soc_pct=30.0, load_w_by=load, cfg=cfg)
    intents = [s.intent for s in plan.slots]
    assert BatteryIntent.GRID_CHARGE_TO_TARGET in intents
    assert BatteryIntent.DISCHARGE_FOR_LOAD in intents
    # All charging happens before all discharging (charge the cheap window, spend it on the peak).
    charge_i = [i for i, x in enumerate(intents) if x is BatteryIntent.GRID_CHARGE_TO_TARGET]
    disch_i = [i for i, x in enumerate(intents) if x is BatteryIntent.DISCHARGE_FOR_LOAD]
    assert max(charge_i) < min(disch_i)
    # Realising the plan never drops below the reserve floor.
    model = BatteryModel(10.0, 4000.0, 4000.0, 1.0, 10.0)
    proj = project_energy(plan.slots, start_soc_pct=30.0,
                          solar_w_by={}, load_w_by=load, model=model)
    assert min(p.soc_pct for p in proj) >= 10.0 - 0.5


def test_no_trade_when_flat_prices_and_no_deficit():
    # Flat cheap prices, solar covers the load -> nothing to optimise, no forced grid charge.
    prices = _prices([0.15] * 12)
    fc = _fc([1000.0] * 12)
    load = _load([300.0] * 12)
    cfg = OptimalConfig(usable_kwh=10.0)
    plan = plan_optimal(prices, fc, T0, soc_pct=80.0, load_w_by=load, cfg=cfg)
    assert not [s for s in plan.slots if s.intent is BatteryIntent.GRID_CHARGE_TO_TARGET]
