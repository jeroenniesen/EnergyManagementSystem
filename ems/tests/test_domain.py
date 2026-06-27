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
