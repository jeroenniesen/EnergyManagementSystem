"""Forward energy projection: simulate SoC + grid flow over the plan, slot by slot, from the
current SoC, the solar forecast, the expected load and the battery's intent. Pure + unit-tested.
"""
import math
from datetime import UTC, datetime

import pytest

from ems.domain import BatteryIntent
from ems.planner.projection import BatteryModel, mark_floor_holds, project_energy
from ems.planner.schedule import SLOT, Plan, PlanSlot

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


def test_discharge_for_load_at_floor_deficit_is_idle_until_marked_hold():
    """#312 AC1: at the reserve floor, discharge-for-load cannot push power — then floor hold."""
    floor = 10.0
    slots = _slots(BatteryIntent.DISCHARGE_FOR_LOAD)
    model = _model(reserve_soc_pct=floor)
    out = _run(slots, start_soc_pct=floor, load=1000.0, model=model)
    assert out[0].battery_w == 0.0
    plan = Plan(created_at=T0, slots=tuple(slots))
    marked = mark_floor_holds(plan, out, floor, start_soc_pct=floor)
    assert marked.slots[0].intent is BatteryIntent.HOLD_RESERVE
    assert "vloer" in marked.slots[0].reason.lower()


def test_discharge_for_load_just_above_floor_stays_discharge_until_marked():
    """#312 AC5: above the anti-flap band, real projected discharge stays DISCHARGE_FOR_LOAD."""
    floor = 10.0
    slots = _slots(BatteryIntent.DISCHARGE_FOR_LOAD)
    model = _model(reserve_soc_pct=floor)
    start = floor + 0.5
    out = _run(slots, start_soc_pct=start, load=1000.0, model=model)
    assert out[0].battery_w > 0.0
    plan = Plan(created_at=T0, slots=tuple(slots))
    marked = mark_floor_holds(plan, out, floor, start_soc_pct=start)
    assert marked.slots[0].intent is BatteryIntent.DISCHARGE_FOR_LOAD


def test_discharge_for_load_surplus_charges_like_self_consumption():
    """#312 AC3 / F-21: surplus under DISCHARGE_FOR_LOAD charges (less export than idle)."""
    slots = _slots(BatteryIntent.DISCHARGE_FOR_LOAD, BatteryIntent.ALLOW_SELF_CONSUMPTION)
    solar_by = {s.start: 2000.0 for s in slots}
    load_by = {s.start: 500.0 for s in slots}
    model = _model()
    dfl = project_energy(
        [slots[0]], start_soc_pct=50.0, solar_w_by=solar_by, load_w_by=load_by, model=model,
    )
    asc = project_energy(
        [slots[1]], start_soc_pct=50.0, solar_w_by=solar_by, load_w_by=load_by, model=model,
    )
    assert dfl[0].battery_w < 0
    assert dfl[0].soc_pct > 50.0
    assert dfl[0].grid_w == 0.0  # surplus soaked — not exported like pre-F-21 (grid_w = −1500 W)
    assert asc[0].battery_w == dfl[0].battery_w


def test_export_for_profit_does_not_charge_surplus():
    """F-21 contrast: deliberate export keeps surplus on the grid."""
    out = _run(_slots(BatteryIntent.EXPORT_FOR_PROFIT), start_soc_pct=50.0,
               solar=2000.0, load=500.0)
    assert out[0].battery_w == 0.0
    assert out[0].grid_w < 0


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


def test_grid_charge_stops_at_the_night_carry_target():
    # With a target of 80%, grid-charging fills 50% -> 80% and then holds; it must NOT reach 100%.
    slots = [PlanSlot(T0 + i * SLOT, BatteryIntent.GRID_CHARGE_TO_TARGET, "") for i in range(40)]
    out = project_energy(
        slots, start_soc_pct=50.0, solar_w_by={}, load_w_by={s.start: 0.0 for s in slots},
        model=_model(), charge_target_soc_pct=80.0,
    )
    socs = [s.soc_pct for s in out]
    assert max(socs) == pytest.approx(80.0)  # capped at the target
    assert socs[-1] == pytest.approx(80.0)  # holds there, never climbs to 100
    # Once at target the battery stops drawing from the grid (no over-buy).
    assert out[-1].battery_w == 0.0
    assert out[-1].grid_w == 0.0  # load is 0 here, nothing imported


def test_no_target_still_charges_to_full():
    # Legacy behaviour preserved when no target is given.
    slots = [PlanSlot(T0 + i * SLOT, BatteryIntent.GRID_CHARGE_TO_TARGET, "") for i in range(40)]
    out = project_energy(slots, start_soc_pct=50.0, solar_w_by={},
                         load_w_by={s.start: 0.0 for s in slots}, model=_model())
    assert max(s.soc_pct for s in out) == 100.0


def test_soc_never_exceeds_100_across_a_long_charge():
    out = _run([PlanSlot(T0 + i * SLOT, BatteryIntent.GRID_CHARGE_TO_TARGET, "")
                for i in range(40)], start_soc_pct=50.0)
    socs = [s.soc_pct for s in out]
    assert max(socs) <= 100.0
    assert socs[-1] == 100.0  # reaches and holds full
    assert all(a <= b + 1e-9 for a, b in zip(socs, socs[1:], strict=False))  # non-decreasing


def test_evening_reserve_kwh_is_the_capped_evening_deficit():
    """#326: DC deficit of the post-15:00 discharge cluster, capped at usable − floor."""
    from datetime import timedelta
    from zoneinfo import ZoneInfo

    from ems.planner.projection import evening_reserve_kwh

    ams = ZoneInfo("Europe/Amsterdam")
    day = datetime(2026, 1, 15, 0, 0, tzinfo=ams)
    morning = [
        PlanSlot(day.replace(hour=7, minute=15 * i), BatteryIntent.DISCHARGE_FOR_LOAD, "x")
        for i in range(4)
    ]
    evening = [
        PlanSlot(day.replace(hour=18, minute=15 * i), BatteryIntent.DISCHARGE_FOR_LOAD, "x")
        for i in range(4)
    ]
    load = {s.start: 1000.0 for s in evening}
    load.update({s.start: 3000.0 for s in morning})
    need, start = evening_reserve_kwh(
        morning + evening, load_w_by=load, usable_kwh=10.0, reserve_soc_pct=10.0,
        max_discharge_w=4000.0, round_trip_efficiency=0.90, site_tz=ams,
    )
    # 4 × 1000 W × 0.25 h = 1.0 kWh AC; DC = AC / √0.90. Under the 9 kWh cap.
    assert start == evening[0].start
    assert need == 1.0 / math.sqrt(0.90)
    only_morning, no_start = evening_reserve_kwh(
        morning, load_w_by={s.start: 3000.0 for s in morning}, usable_kwh=10.0,
        reserve_soc_pct=10.0, max_discharge_w=4000.0, round_trip_efficiency=0.90,
        site_tz=ams,
    )
    assert only_morning == 0.0 and no_start is None
    heavy = [
        PlanSlot(day.replace(hour=18) + i * timedelta(minutes=15),
                 BatteryIntent.DISCHARGE_FOR_LOAD, "x")
        for i in range(40)
    ]
    capped, capped_start = evening_reserve_kwh(
        heavy, load_w_by={s.start: 4000.0 for s in heavy}, usable_kwh=10.0,
        reserve_soc_pct=10.0, max_discharge_w=4000.0, round_trip_efficiency=1.0,
        site_tz=ams,
    )
    assert capped_start == heavy[0].start
    assert capped == 9.0
