"""#245: align reserve / honest-partial floors with capability.min_target_soc."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from ems.battery_limits import (
    DEFAULT_MIN_TARGET_SOC_PCT,
    device_min_target_soc,
    effective_floor_soc,
    soc_control_floor,
)
from ems.domain import BatteryIntent, CapabilityReport
from ems.planner.adaptive import AdaptiveConfig, plan_adaptive
from ems.planner.projection import BatteryModel, ProjectedSlot, project_energy
from ems.planner.recovery import honest_partial_from_projection
from ems.planner.schedule import SLOT, Plan, PlanSlot
from ems.planner.validator import validate_plan
from ems.settings import (
    SETTINGS_BY_KEY,
    effective_settings,
    settings_migration_writes,
    validate_settings,
)
from ems.sources.prices import PriceSlot

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


def test_effective_floor_soc_alias_matches_soc_control_floor():
    assert effective_floor_soc(10.0, min_target_soc=5.0) == 10.0
    assert effective_floor_soc(5.0, min_target_soc=6.0) == 6.0
    assert effective_floor_soc(5.0, capability=None) == DEFAULT_MIN_TARGET_SOC_PCT


def test_effective_floor_6_device_5_setting_propagates_to_plan_and_projection():
    """#310 AC1: device min 6 % with user reserve 5 % → plan + projection floor ≥ 6."""
    cap = CapabilityReport(
        services=CAP.services,
        energy_mode_options=CAP.energy_mode_options,
        has_standby=CAP.has_standby,
        has_grid_charge_switch=CAP.has_grid_charge_switch,
        p1_paired=CAP.p1_paired,
        max_charge_w=CAP.max_charge_w,
        max_discharge_w=CAP.max_discharge_w,
        supports_grid_charge=CAP.supports_grid_charge,
        min_target_soc=6.0,
    )
    floor = effective_floor_soc(5.0, capability=cap)
    assert floor == 6.0

    prices = [PriceSlot(T0 + i * SLOT, 0.20) for i in range(96)]
    load = {p.start: 1500.0 for p in prices}
    cfg = AdaptiveConfig(
        usable_kwh=10.0,
        reserve_soc_pct=floor,
        max_charge_w=4000.0,
    )
    plan = plan_adaptive(prices, [], T0 + 12 * SLOT, soc_pct=50.0, load_w_by=load, cfg=cfg)
    for s in plan.slots:
        if s.floor_soc is not None:
            assert s.floor_soc >= floor - 1e-6

    model = BatteryModel(
        usable_kwh=10.0,
        max_charge_w=4000.0,
        max_discharge_w=4000.0,
        round_trip_efficiency=0.9,
        reserve_soc_pct=floor,
    )
    assert model.reserve_soc_pct >= 6.0

    proj = project_energy(
        plan.slots[:8], start_soc_pct=50.0,
        solar_w_by={s.start: 0.0 for s in plan.slots[:8]},
        load_w_by={s.start: 1500.0 for s in plan.slots[:8]},
        model=model,
    )
    assert all(p.soc_pct >= floor - 1e-6 for p in proj)


def test_effective_floor_10_setting_5_device():
    floor = effective_floor_soc(10.0, min_target_soc=5.0)
    assert floor == 10.0


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
