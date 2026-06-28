"""Forward energy projection: simulate SoC + grid flow over the plan, slot by slot, from the
current SoC, the solar forecast, the expected load and the battery's intent. Pure + unit-tested.
"""
import math
from datetime import UTC, datetime

import pytest

from ems.domain import BatteryIntent
from ems.planner.projection import BatteryModel, project_energy
from ems.planner.schedule import SLOT, PlanSlot

T0 = datetime(2026, 6, 28, 12, 0, tzinfo=UTC)


def _model(**kw) -> BatteryModel:
    base = dict(usable_kwh=10.0, max_charge_w=4000.0, max_discharge_w=4000.0,
                round_trip_efficiency=1.0, reserve_soc_pct=10.0)
    base.update(kw)
    return BatteryModel(**base)


def _slots(*intents: BatteryIntent) -> list[PlanSlot]:
    return [PlanSlot(T0 + i * SLOT, intent, "") for i, intent in enumerate(intents)]


def _run(slots, *, start_soc_pct, solar=0.0, load=0.0, model=None):
    solar_by = {s.start: solar for s in slots}
    load_by = {s.start: load for s in slots}
    return project_energy(slots, start_soc_pct=start_soc_pct, solar_w_by=solar_by,
                          load_w_by=load_by, model=model or _model())


def test_empty_plan_projects_nothing():
    assert project_energy([], start_soc_pct=50.0, solar_w_by={}, load_w_by={},
                          model=_model()) == []


def test_grid_charge_raises_soc_and_imports():
    # 4 kW into a 10 kWh pack for 15 min = +1 kWh = +10 %; grid imports the charge power.
    out = _run(_slots(BatteryIntent.GRID_CHARGE_TO_TARGET), start_soc_pct=50.0)
    assert out[0].soc_pct == 60.0
    assert out[0].battery_w == -4000.0  # charging
    assert out[0].grid_w == 4000.0  # imported to charge


def test_self_consumption_deficit_zeroes_the_grid():
    out = _run(_slots(BatteryIntent.ALLOW_SELF_CONSUMPTION), start_soc_pct=50.0, load=1000.0)
    assert out[0].battery_w == 1000.0  # discharging to cover load
    assert out[0].grid_w == 0.0  # vendor self-consumption zeroes the grid
    assert out[0].soc_pct == pytest.approx(47.5)  # -250 Wh of 10 kWh


def test_self_consumption_surplus_charges_and_zeroes_the_grid():
    out = _run(_slots(BatteryIntent.ALLOW_SELF_CONSUMPTION), start_soc_pct=50.0,
               solar=2000.0, load=500.0)
    assert out[0].battery_w == -1500.0  # soaking the 1.5 kW surplus
    assert out[0].grid_w == 0.0
    assert out[0].soc_pct == pytest.approx(53.75)


def test_reserve_floor_blocks_discharge():
    # At the reserve floor there is nothing to give; load is served from the grid instead.
    out = _run(_slots(BatteryIntent.ALLOW_SELF_CONSUMPTION), start_soc_pct=10.0, load=1000.0)
    assert out[0].battery_w == 0.0
    assert out[0].grid_w == 1000.0
    assert out[0].soc_pct == 10.0


def test_full_battery_blocks_charge_and_exports_surplus():
    out = _run(_slots(BatteryIntent.ALLOW_SELF_CONSUMPTION), start_soc_pct=100.0,
               solar=2000.0, load=500.0)
    assert out[0].battery_w == 0.0
    assert out[0].grid_w == -1500.0  # surplus exported
    assert out[0].soc_pct == 100.0


def test_hold_reserve_idles_on_deficit_but_soaks_solar_surplus():
    slots = _slots(BatteryIntent.HOLD_RESERVE, BatteryIntent.HOLD_RESERVE)
    solar_by = {slots[0].start: 0.0, slots[1].start: 2000.0}
    load_by = {slots[0].start: 800.0, slots[1].start: 500.0}
    out = project_energy(slots, start_soc_pct=50.0, solar_w_by=solar_by, load_w_by=load_by,
                         model=_model())
    # Slot 0: deficit, hold -> battery idle, grid imports.
    assert out[0].battery_w == 0.0 and out[0].grid_w == 800.0
    # Slot 1: surplus -> charge (never waste free solar).
    assert out[1].battery_w == -1500.0 and out[1].grid_w == 0.0


def test_discharge_for_load_covers_the_deficit():
    out = _run(_slots(BatteryIntent.DISCHARGE_FOR_LOAD), start_soc_pct=50.0, load=1500.0)
    assert out[0].battery_w == 1500.0
    assert out[0].grid_w == 0.0


def test_round_trip_efficiency_loss_on_charge():
    # rte 0.81 -> one-way eta 0.9: 4 kW * 0.25 h * 0.9 = 0.9 kWh stored = +9 %.
    out = _run(_slots(BatteryIntent.GRID_CHARGE_TO_TARGET), start_soc_pct=50.0,
               model=_model(round_trip_efficiency=0.81))
    assert out[0].soc_pct == pytest.approx(59.0)


def test_grid_balance_identity_holds_every_slot():
    # grid = load - solar - battery, exactly, for any intent/slot.
    slots = _slots(BatteryIntent.GRID_CHARGE_TO_TARGET, BatteryIntent.ALLOW_SELF_CONSUMPTION,
                   BatteryIntent.DISCHARGE_FOR_LOAD, BatteryIntent.HOLD_RESERVE)
    solar_by = {s.start: 700.0 * i for i, s in enumerate(slots)}
    load_by = {s.start: 900.0 for s in slots}
    out = project_energy(slots, start_soc_pct=60.0, solar_w_by=solar_by, load_w_by=load_by,
                         model=_model(round_trip_efficiency=0.9))
    for slot in out:
        assert math.isclose(slot.grid_w, slot.load_w - slot.solar_w - slot.battery_w, abs_tol=1e-6)


def test_soc_never_exceeds_100_across_a_long_charge():
    out = _run([PlanSlot(T0 + i * SLOT, BatteryIntent.GRID_CHARGE_TO_TARGET, "")
                for i in range(40)], start_soc_pct=50.0)
    socs = [s.soc_pct for s in out]
    assert max(socs) <= 100.0
    assert socs[-1] == 100.0  # reaches and holds full
    assert all(a <= b + 1e-9 for a, b in zip(socs, socs[1:], strict=False))  # non-decreasing
