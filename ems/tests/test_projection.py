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


@pytest.mark.parametrize("fallback", [None, 20.0])
def test_disjoint_economic_charge_windows_project_their_own_targets(fallback):
    from ems.planner.rule_based import PlannerConfig, plan_rule_based
    from ems.planner.validator import validate_plan
    from ems.sources.prices import PriceSlot

    prices = [PriceSlot(T0 + i * SLOT, p) for i, p in enumerate([.1, .5, .05, .6])]
    loads = {p.start: w for p, w in zip(prices, [0, 1000, 0, 4000], strict=True)}
    plan = plan_rule_based(prices, T0, PlannerConfig(
        bill_optimization_enabled=True, round_trip_efficiency=1.0,
        degradation_eur_per_kwh=0, risk_margin_eur_per_kwh=0),
        soc_pct=10, load_w_by=loads, usable_kwh=10)
    out = project_energy(plan.slots, start_soc_pct=10, solar_w_by={}, load_w_by=loads,
                         model=_model(), charge_target_soc_pct=fallback)
    assert out[0].soc_pct == pytest.approx(12.5)
    assert out[0].grid_w == pytest.approx(1000)
    assert out[1].soc_pct == pytest.approx(10)
    assert out[2].soc_pct == pytest.approx(20)
    assert out[2].grid_w == pytest.approx(4000)
    assert out[3].soc_pct == pytest.approx(10)
    assert validate_plan(plan, soc_pct=10, data_quality="complete", min_reserve_soc=10,
                         projection=out, projection_target_margin_pp=0.01).ok


def test_explicit_slot_target_takes_precedence_over_legacy_global_target():
    slots = [PlanSlot(T0, BatteryIntent.GRID_CHARGE_TO_TARGET, "", target_soc=55)]
    out = project_energy(slots, start_soc_pct=50, solar_w_by={}, load_w_by={},
                         model=_model(), charge_target_soc_pct=52)
    assert out[0].soc_pct == pytest.approx(55)


def test_partial_first_charge_slot_preserves_price_and_energy_timing():
    from datetime import timedelta

    from ems.planner.explain import summarize_projection
    from ems.planner.rule_based import PlannerConfig, plan_rule_based
    from ems.sources.prices import PriceSlot

    now = T0 + timedelta(minutes=14)
    prices = [PriceSlot(T0 + i * SLOT, p) for i, p in enumerate([.15, .05, .5, .5])]
    loads = {p.start: w for p, w in zip(prices, [0, 0, 4000, 2000], strict=True)}
    plan = plan_rule_based(prices, now, PlannerConfig(
        bill_optimization_enabled=True, round_trip_efficiency=1,
        degradation_eur_per_kwh=0, risk_margin_eur_per_kwh=0),
        soc_pct=10, load_w_by=loads)
    out = project_energy(plan.slots, start_soc_pct=10, solar_w_by={}, load_w_by=loads,
                         model=_model(), start_at=now)
    assert out[0].duration_hours == pytest.approx(1 / 60)
    assert out[0].soc_pct == pytest.approx(10 + 4 / 6)
    assert out[1].soc_pct == pytest.approx(20 + 4 / 6)
    charge_cost = sum(p.grid_w * p.duration_hours / 1000 * prices[i].eur_per_kwh
                      for i, p in enumerate(out[:2]))
    assert charge_cost == pytest.approx(.06)
    assert summarize_projection(out)["import_kwh"] == pytest.approx(1.5, abs=.01)


def test_partial_slot_projects_remaining_solar_and_house_energy():
    from datetime import timedelta

    from ems.planner.explain import summarize_projection

    slots = _slots(BatteryIntent.ALLOW_SELF_CONSUMPTION, BatteryIntent.HOLD_RESERVE)
    out = project_energy(slots, start_soc_pct=50, solar_w_by={T0: 2000},
                         load_w_by={T0: 500, T0 + SLOT: 1000}, model=_model(),
                         start_at=T0 + timedelta(minutes=14))
    assert out[0].soc_pct == pytest.approx(50.25)
    assert out[0].grid_w == 0
    summary = summarize_projection(out)
    assert summary["import_kwh"] == pytest.approx(.25)
    assert summary["solar_kwh"] == pytest.approx(.03)
    assert summary["load_kwh"] == pytest.approx(.26)


def test_projection_omits_slots_that_have_already_ended():
    slots = _slots(BatteryIntent.GRID_CHARGE_TO_TARGET, BatteryIntent.GRID_CHARGE_TO_TARGET)
    out = project_energy(slots, start_soc_pct=50, solar_w_by={}, load_w_by={},
                         model=_model(), start_at=T0 + SLOT)
    assert len(out) == 1
    assert out[0].start == T0 + SLOT
    assert out[0].soc_pct == 60
