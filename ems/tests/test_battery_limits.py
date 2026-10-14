"""#245: align reserve / honest-partial floors with capability.min_target_soc."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from ems.battery_limits import (
    DEFAULT_MIN_TARGET_SOC_PCT,
    device_min_target_soc,
    soc_control_floor,
)
from ems.domain import BatteryIntent, CapabilityReport
from ems.planner.projection import ProjectedSlot
from ems.planner.recovery import honest_partial_from_projection
from ems.planner.schedule import SLOT, Plan, PlanSlot
from ems.planner.validator import validate_plan
from ems.settings import (
    SETTINGS_BY_KEY,
    effective_settings,
    settings_migration_writes,
    validate_settings,
)

T0 = datetime(2026, 1, 15, 0, 0, tzinfo=UTC)
CAP = CapabilityReport(
    services=("charge", "discharge"),
    energy_mode_options=("self_consumption",),
    has_standby=True,
    has_grid_charge_switch=True,
    p1_paired=True,
    max_charge_w=4000.0,
    max_discharge_w=4000.0,
    supports_grid_charge=True,
    min_target_soc=5.0,
)


def test_settings_schema_floors_min_reserve_at_device_min():
    field = SETTINGS_BY_KEY["battery.min_reserve_soc"]
    assert field.min == DEFAULT_MIN_TARGET_SOC_PCT
    clean, errors = validate_settings({"battery.min_reserve_soc": 3.0})
    assert "battery.min_reserve_soc" in errors
    assert "battery.min_reserve_soc" not in clean
    ok, err = validate_settings({"battery.min_reserve_soc": 5.0})
    assert not err and ok["battery.min_reserve_soc"] == 5.0


def test_legacy_sub_floor_reserve_migrates_to_device_min():
    writes = settings_migration_writes({"battery.min_reserve_soc": 2.0})
    assert writes["battery.min_reserve_soc"] == DEFAULT_MIN_TARGET_SOC_PCT
    eff = effective_settings({"battery.min_reserve_soc": 0.0})
    assert eff["battery.min_reserve_soc"] == DEFAULT_MIN_TARGET_SOC_PCT


def test_soc_control_floor_takes_max_of_reserve_and_device():
    assert soc_control_floor(10.0, min_target_soc=5.0) == 10.0
    assert soc_control_floor(0.0, min_target_soc=5.0) == 5.0
    assert soc_control_floor(3.0, capability=CAP) == 5.0
    assert device_min_target_soc(None) == DEFAULT_MIN_TARGET_SOC_PCT


def test_honest_partial_with_low_reserve_does_not_unsafe_under_capability():
    """Regression #245: reserve 0–4% + projection shortfall must not emit target < 5%."""
    deadline = T0 + 6 * SLOT
    slots = tuple(
        PlanSlot(
            T0 + i * SLOT,
            BatteryIntent.GRID_CHARGE_TO_TARGET,
            "charge",
            target_soc=80.0,
            floor_soc=0.0,
            deadline=deadline,
            power_w=4000.0,
        )
        for i in range(8)
    )
    plan = Plan(
        created_at=T0, strategy="winter", slots=slots,
        target_soc=80.0, deadline=deadline, version=1,
    )
    # Projection only reaches ~3% — without the device floor, honest-partial would commit 3%.
    proj = [
        ProjectedSlot(s.start, s.intent, min(3.0, 0.5 + i * 0.4), 0, 0, 0, 0)
        for i, s in enumerate(slots)
    ]
    resized = honest_partial_from_projection(
        plan, proj, min_reserve_soc=0.0, min_target_soc=CAP.min_target_soc,
    )
    assert resized.target_soc is not None
    assert resized.target_soc >= CAP.min_target_soc - 1e-6
    v = validate_plan(
        resized, soc_pct=1.0, data_quality="complete", min_reserve_soc=0.0,
        capability=CAP, projection=proj, validate_projection=True,
        min_dwell=timedelta(seconds=0),
    )
    assert v.ok is True
    assert not any(f.code == "target_below_capability_min" for f in v.findings)
