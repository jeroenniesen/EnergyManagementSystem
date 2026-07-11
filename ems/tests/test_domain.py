import dataclasses

import pytest

from ems.domain import BatteryIntent, PlannerMode, RawSample


def test_battery_intent_values():
    assert BatteryIntent.ALLOW_SELF_CONSUMPTION.value == "allow_self_consumption"
    assert {i.value for i in BatteryIntent} == {
        "allow_self_consumption",
        "grid_charge_to_target",
        "hold_reserve",
        "discharge_for_load",
    }


def test_planner_mode_default_is_rule_based():
    assert PlannerMode.RULE_BASED.value == "rule_based"
    assert {m.value for m in PlannerMode} == {"rule_based", "ml", "advisory"}


def test_raw_sample_is_frozen():
    s = RawSample(grid_power_w=200, solar_power_w=0, battery_power_w=800, ev_power_w=0, soc_pct=55)
    assert s.grid_power_w == 200
    with pytest.raises(dataclasses.FrozenInstanceError):
        s.grid_power_w = 1  # type: ignore[misc]


def test_raw_sample_total_gas_m3_defaults_to_none():
    s = RawSample(grid_power_w=200, solar_power_w=0, battery_power_w=800, ev_power_w=0, soc_pct=55)
    assert s.total_gas_m3 is None


def test_raw_sample_positional_construction_still_works():
    # B-02: total_gas_m3 was added as the LAST field with a default, so every existing positional
    # construction across the codebase/tests must keep working unchanged.
    s = RawSample(1600.0, 3500.0, -800.0, 4000.0, 55.0)
    assert s.grid_power_w == 1600.0
    assert s.solar_power_w == 3500.0
    assert s.battery_power_w == -800.0
    assert s.ev_power_w == 4000.0
    assert s.soc_pct == 55.0
    assert s.total_gas_m3 is None


def test_raw_sample_total_gas_m3_round_trips():
    s = RawSample(1600.0, 3500.0, -800.0, 4000.0, 55.0, 1234.5)
    assert s.total_gas_m3 == 1234.5
    s2 = RawSample(grid_power_w=200, solar_power_w=0, battery_power_w=800, ev_power_w=0,
                    soc_pct=55, total_gas_m3=42.0)
    assert s2.total_gas_m3 == 42.0
