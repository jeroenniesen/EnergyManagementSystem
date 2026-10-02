"""B-105 / #211: EXPORT_FOR_PROFIT mapping + validator guardrails."""
from datetime import UTC, datetime

from ems.domain import BatteryIntent, CapabilityReport, PhysicalMode
from ems.planner.schedule import SLOT, Plan, PlanSlot
from ems.planner.validator import validate_plan
from ems.sources.battery import intent_to_mode

T0 = datetime(2026, 6, 28, 12, 0, tzinfo=UTC)
CAP = CapabilityReport(
    services=("charge", "discharge"), energy_mode_options=(),
    has_standby=True, has_grid_charge_switch=True, p1_paired=True,
    max_charge_w=4000.0, max_discharge_w=4000.0,
)
NO_DISCHARGE = CapabilityReport(
    services=("charge",), energy_mode_options=(),
    has_standby=True, has_grid_charge_switch=True, p1_paired=True,
    max_charge_w=4000.0, max_discharge_w=0.0,
)


def _export(i: int = 0, *, floor=10.0, power=4000.0, kwh=1.0) -> PlanSlot:
    return PlanSlot(
        T0 + i * SLOT, BatteryIntent.EXPORT_FOR_PROFIT, "export for profit",
        floor_soc=floor, power_w=power, target_kwh=kwh,
    )


def test_export_maps_to_discharge_only_when_armed():
    assert intent_to_mode(BatteryIntent.EXPORT_FOR_PROFIT) is PhysicalMode.AUTO
    assert (
        intent_to_mode(BatteryIntent.EXPORT_FOR_PROFIT, allow_export_discharge=True)
        is PhysicalMode.DISCHARGE
    )


def test_dfl_never_armed_by_allow_export_discharge():
    """allow_export_discharge must NOT promote DISCHARGE_FOR_LOAD (narrowed in B-105)."""
    assert intent_to_mode(BatteryIntent.DISCHARGE_FOR_LOAD) is PhysicalMode.AUTO
    assert (
        intent_to_mode(BatteryIntent.DISCHARGE_FOR_LOAD, allow_export_discharge=True)
        is PhysicalMode.AUTO
    )
    # Car session remains the narrow exception.
    assert (
        intent_to_mode(BatteryIntent.DISCHARGE_FOR_LOAD, car_session=True)
        is PhysicalMode.DISCHARGE
    )


def test_intent_to_mode_covers_export():
    assert {intent_to_mode(i) for i in BatteryIntent} <= set(PhysicalMode)
    assert BatteryIntent.EXPORT_FOR_PROFIT in BatteryIntent


def test_export_plan_valid_when_armed_and_capable():
    plan = Plan(created_at=T0, slots=(_export(),), strategy="trading")
    v = validate_plan(
        plan, soc_pct=60.0, data_quality="complete", min_reserve_soc=10.0,
        capability=CAP, allow_export_discharge=True,
    )
    assert v.ok and v.status == "valid"


def test_export_without_capability_is_unsafe():
    plan = Plan(created_at=T0, slots=(_export(),), strategy="trading")
    v = validate_plan(
        plan, soc_pct=60.0, data_quality="complete", min_reserve_soc=10.0,
        capability=NO_DISCHARGE, allow_export_discharge=True,
    )
    assert not v.ok
    assert any(f.code == "export_capability_missing" for f in v.findings)


def test_export_floor_below_reserve_is_unsafe():
    plan = Plan(created_at=T0, slots=(_export(floor=5.0),), strategy="trading")
    v = validate_plan(
        plan, soc_pct=60.0, data_quality="complete", min_reserve_soc=10.0,
        capability=CAP, allow_export_discharge=True,
    )
    assert not v.ok
    assert any(f.code == "export_floor_below_reserve" for f in v.findings)


def test_export_day_cap_exceeded_is_unsafe():
    plan = Plan(
        created_at=T0,
        slots=(_export(0, kwh=2.0), _export(1, kwh=2.0)),
        strategy="trading",
    )
    v = validate_plan(
        plan, soc_pct=60.0, data_quality="complete", min_reserve_soc=10.0,
        capability=CAP, allow_export_discharge=True, max_export_kwh_per_day=3.0,
    )
    assert not v.ok
    assert any(f.code == "export_day_cap_exceeded" for f in v.findings)


def test_export_uncapped_when_max_export_zero():
    plan = Plan(
        created_at=T0,
        slots=tuple(_export(i, kwh=2.0) for i in range(4)),
        strategy="trading",
    )
    v = validate_plan(
        plan, soc_pct=60.0, data_quality="complete", min_reserve_soc=10.0,
        capability=CAP, allow_export_discharge=True, max_export_kwh_per_day=0.0,
    )
    assert v.ok
    assert not any(f.code == "export_day_cap_exceeded" for f in v.findings)


def test_export_not_armed_warns_but_stays_applicable():
    plan = Plan(created_at=T0, slots=(_export(),), strategy="trading")
    v = validate_plan(
        plan, soc_pct=60.0, data_quality="complete", min_reserve_soc=10.0,
        capability=CAP, allow_export_discharge=False,
    )
    assert v.ok and v.status == "warn"
    assert any(f.code == "export_not_armed" for f in v.findings)
