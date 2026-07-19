"""Safety invariant tests (BACKLOG B-84, E-09).

Proves the EMS enforces its 'never worse than no EMS' guarantees under adversarial
conditions. Scenario-based: each test names the invariant it proves, constructs a
ControlService with mock collaborators and injected callables, then drives it through
the scenario. Pattern mirrors test_control_service.py.

Hard rules: all battery writes go through ModeController.decide() → driver.apply().
No test bypasses the controller. Dry-run is respected.
"""
import asyncio
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from ems.control.mode_controller import ModeController
from ems.control.override import Override
from ems.control.service import ControlContext, ControlService
from ems.domain import BatteryIntent, PhysicalMode
from ems.lifecycle import Lifecycle
from ems.planner.validator import Finding, PlanValidation
from ems.settings import effective_settings
from ems.sources.battery import FailingMockBatteryDriver, MockBatteryDriver

NOW = datetime(2026, 6, 28, 12, 0, tzinfo=UTC)


def _controlling_controller(driver=None):
    """ModeController driven to CONTROLLING so control_tick may command."""
    lc = Lifecycle(dry_run=False, startup_grace_seconds=0.0)
    lc.start(NOW)
    lc.mark_sensors_validated()
    lc.mark_probe_ok()
    lc.mark_plan_loaded()
    lc.tick(NOW)
    return ModeController(driver or MockBatteryDriver(), lc, dry_run=False)


def _service(controller, *, audit_store=None, car_charging=None, data_quality="complete",
             validate_plan_callable=None):
    """Build ControlService + context with mock collaborators and injected callables."""
    ctx = ControlContext()
    settings = effective_settings({})

    dq_fn = data_quality if callable(data_quality) else (lambda now: data_quality)
    cc_fn = car_charging if car_charging is not None else (lambda now: False)
    val_fn = validate_plan_callable or (
        lambda plan, now: (_ for _ in ()).throw(AssertionError("validate_plan_obj not wired")))

    svc = ControlService(
        ctx=ctx, settings=settings, controller=controller, store=None, audit_store=audit_store,
        price_source=None, solar_forecast=None, site_tz=ZoneInfo("Europe/Amsterdam"), dry_run=False,
        data_quality=dq_fn, validate_plan_obj=val_fn, car_charging=cc_fn,
        load_by=lambda starts: {s: 0.0 for s in starts}, active_strategy=lambda now: "winter",
        planner_cfg=lambda: None, summer_cfg=lambda soc: None, adaptive_cfg=lambda: None,
        current_soc=lambda now: 50.0,
        current_mode=lambda now: PhysicalMode.AUTO,
        current_towers=lambda now: None,
    )
    return svc, ctx


# ==================================================================================================
# I1 — Reserve floor respected: charge target below reserve rejected by validator
# ==================================================================================================

def test_reserve_floor_blocks_charge_below_reserve():
    """A grid-charge plan with target SoC below the reserve floor is rejected by the validator
    (target_below_reserve) → effective_intent falls back to ALLOW_SELF_CONSUMPTION. No write."""
    from ems.planner.schedule import Plan, PlanSlot
    from ems.planner.validator import validate_plan

    settings = effective_settings({})
    reserve = settings["battery.min_reserve_soc"]  # default 10

    controller = _controlling_controller()
    svc, ctx = _service(controller)

    # Plan with a charge target BELOW the reserve floor (5 < 10).
    plan = Plan(
        created_at=NOW,
        slots=(PlanSlot(
            start=NOW - timedelta(seconds=1), intent=BatteryIntent.GRID_CHARGE_TO_TARGET,
            reason="charge to 5%", target_soc=5.0, floor_soc=reserve),),
    )

    # Wire the real validator through _validate_plan_obj (the attribute effective_intent calls).
    svc._validate_plan_obj = lambda p, n: validate_plan(
        p, soc_pct=50.0, data_quality="complete", min_reserve_soc=reserve)
    # Bypass build_plan_now (needs price_source) — inject the plan directly.
    svc.current_plan = lambda: (NOW, [], plan)

    result = svc.effective_intent(NOW)
    assert result[0] is BatteryIntent.ALLOW_SELF_CONSUMPTION  # validator rejected → ASC
    assert "self-consumption" in result[1]

    # Battery mode untouched — still AUTO.
    assert controller.driver.current_mode() is PhysicalMode.AUTO


# ==================================================================================================
# I2 — Override DISCHARGE_FOR_LOAD maps to AUTO by default (no forced discharge)
# ==================================================================================================

def test_override_discharge_maps_to_auto_by_default():
    """DISCHARGE_FOR_LOAD maps to AUTO by default (allow_export_discharge=False). Even a manual
    override for discharge never commands PhysicalMode.DISCHARGE unless export-discharge is enabled.
    This prevents accidental grid export — the single-writer safety invariant."""
    controller = _controlling_controller()
    assert controller.allow_export_discharge is False

    svc, ctx = _service(controller)
    # Manual override for DISCHARGE_FOR_LOAD.
    ctx.override_box["ov"] = Override(
        intent=BatteryIntent.DISCHARGE_FOR_LOAD, expires_at=NOW + timedelta(hours=1))

    svc.control_tick(NOW)  # tick runs, battery stays AUTO (no forced discharge).
    assert controller.driver.current_mode() is PhysicalMode.AUTO


# ==================================================================================================
# I3 — AUTO fallback on unsafe data quality
# ==================================================================================================

def test_unsafe_data_forces_auto_fallback():
    """When data quality is 'unsafe', even a GRID_CHARGE_TO_TARGET plan intent is forced to
    ALLOW_SELF_CONSUMPTION (AUTO). The fail-safe gate in effective_intent."""
    controller = _controlling_controller()
    svc, ctx = _service(controller, data_quality="unsafe")

    # Active manual override for GRID_CHARGE — should be forced to self-consumption.
    ctx.override_box["ov"] = Override(
        intent=BatteryIntent.GRID_CHARGE_TO_TARGET, expires_at=NOW + timedelta(hours=1))

    result = svc.effective_intent(NOW)
    assert result[0] is BatteryIntent.ALLOW_SELF_CONSUMPTION
    assert "unsafe" in result[1].lower()

    # The tick should NOT command CHARGE — it commands AUTO.
    svc.control_tick(NOW)
    assert controller.driver.current_mode() is PhysicalMode.AUTO


# ==================================================================================================
# I4 — AUTO fallback on write failure (retry → AUTO)
# ==================================================================================================

def test_write_failure_falls_back_to_auto():
    """FailingMockBatteryDriver returns False (unconfirmed) for first N calls, then succeeds.
    The controller retries once on unconfirmed, then falls back to AUTO after the retry fails."""
    driver = FailingMockBatteryDriver(fail_times=2)
    controller = _controlling_controller(driver)

    svc, ctx = _service(controller)
    # Active override forces a write (bypasses dwell/cap).
    ctx.override_box["ov"] = Override(
        intent=BatteryIntent.GRID_CHARGE_TO_TARGET, expires_at=NOW + timedelta(hours=1))

    svc.control_tick(NOW)  # tick runs, driver fails twice → AUTO fallback.
    # After two failures the controller should have fallen back to AUTO.
    mode = controller.driver.current_mode()
    assert mode is PhysicalMode.AUTO, f"Expected AUTO fallback after write failures, got {mode}"


# ==================================================================================================
# I5 — Single-writer: concurrent cycles serialised + idempotency prevents duplicate writes
# ==================================================================================================

def test_concurrent_cycles_serialised_no_duplicate_writes():
    """Two run_cycle() coroutines started simultaneously: control_lock serialises execution.
    The first cycle writes CHARGE; the second sees mode is already CHARGE (via driver.current_mode)
    → idempotent, no second write. The single-writer invariant: only one actual device write per
    serialised pair of cycles."""
    from datetime import UTC as _UTC

    driver = MockBatteryDriver()
    real_now = datetime.now(_UTC)

    lc = Lifecycle(dry_run=False, startup_grace_seconds=0.0)
    lc.start(real_now)
    lc.mark_sensors_validated()
    lc.mark_probe_ok()
    lc.mark_plan_loaded()
    lc.tick(real_now)
    controller = ModeController(driver, lc, dry_run=False)

    svc, ctx = _service(controller)
    # Make current_mode read from the driver (not a fixed callable) so idempotency sees
    # the mode change from cycle 1. This matches production where current_mode derives from
    # the cluster read / driver.current_mode().
    svc._current_mode = lambda now: driver.current_mode()

    # Override expires far in the future (relative to real wall clock that run_cycle uses).
    ctx.override_box["ov"] = Override(
        intent=BatteryIntent.GRID_CHARGE_TO_TARGET, expires_at=real_now + timedelta(hours=2))

    write_count = {"n": 0}
    original_apply = driver.apply

    def counting_apply(mode, *, target_soc=None, power_w=None):
        write_count["n"] += 1
        return original_apply(mode, target_soc=target_soc, power_w=power_w)

    driver.apply = counting_apply

    async def run_both():
        await asyncio.gather(svc.run_cycle(), svc.run_cycle())

    asyncio.run(run_both())

    # control_lock serialises: first cycle writes CHARGE, second sees idempotent (already CHARGE).
    # Only ONE actual device write.
    assert write_count["n"] == 1, f"Expected exactly 1 device write, got {write_count['n']}"
    assert driver.current_mode() is PhysicalMode.CHARGE


# ==================================================================================================
# I6 — No command on failed plan validation
# ==================================================================================================

def test_failed_validation_prevents_command():
    """When validate_plan_obj returns a PlanValidation with status='unsafe', effective_intent
    falls back to ALLOW_SELF_CONSUMPTION and no battery command is issued."""
    from ems.planner.schedule import Plan, PlanSlot

    def rejecting_validator(plan, now):
        return PlanValidation(
            status="unsafe",
            findings=(
                Finding(severity="unsafe", code="projection_below_reserve",
                        message="projected SoC 15% below reserve floor 20%"),
            ),
        )

    controller = _controlling_controller()
    svc, ctx = _service(controller)
    # Wire through the internal attribute effective_intent calls.
    svc._validate_plan_obj = rejecting_validator

    # A plan with a charge slot covering NOW — intent_at() will return it.
    plan = Plan(
        created_at=NOW,
        slots=(PlanSlot(
            start=NOW - timedelta(seconds=1), intent=BatteryIntent.GRID_CHARGE_TO_TARGET,
            reason="cheap window", target_soc=90.0),),
    )

    # Bypass build_plan_now (which needs price_source) — inject the plan directly.
    svc.current_plan = lambda: (NOW, [], plan)

    result = svc.effective_intent(NOW)
    assert result[0] is BatteryIntent.ALLOW_SELF_CONSUMPTION  # validator rejected → ASC
    assert "self-consumption" in result[1]

    # Battery mode untouched.
    assert controller.driver.current_mode() is PhysicalMode.AUTO
