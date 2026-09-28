"""#165 — SoC-floor anti-flap: no hold_reserve↔AUTO oscillation burning the switch budget.

Pure helper tests + a control-tick integration that reproduces the overnight idle↔auto storm
(Mac Mini 2026-09-27: SoC already at 5% ≤ 10% reserve) and asserts ≤1 mode write over N cycles.
"""
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from ems.control.mode_controller import ModeController
from ems.control.safety import resolve_floor_anti_flap
from ems.control.service import ControlContext, ControlService
from ems.domain import BatteryIntent, PhysicalMode
from ems.lifecycle import Lifecycle
from ems.planner.validator import PlanValidation
from ems.settings import effective_settings
from ems.sources.battery import MockBatteryDriver

NOW = datetime(2026, 9, 27, 2, 0, tzinfo=UTC)
_HOLD = (BatteryIntent.HOLD_RESERVE, "hold reserve", False, None, None, None, None)
_ASC = (BatteryIntent.ALLOW_SELF_CONSUMPTION, "self-consumption", False, None, None, None, None)
_CHARGE = (BatteryIntent.GRID_CHARGE_TO_TARGET, "cheap window", False, 90.0, 4000.0, None, None)


# --- pure helper ---------------------------------------------------------------------------------

def test_at_floor_stays_in_observed_auto_when_plan_wants_hold():
    intent, reason, holding = resolve_floor_anti_flap(
        BatteryIntent.HOLD_RESERVE, soc_pct=5.0, min_reserve_soc=10.0,
        observed_mode=PhysicalMode.AUTO, holding=False)
    assert intent is BatteryIntent.ALLOW_SELF_CONSUMPTION
    assert holding is True
    assert reason is not None and "floor anti-flap" in reason
    assert "staying in auto" in reason


def test_at_floor_stays_in_observed_idle_when_plan_wants_auto():
    intent, reason, holding = resolve_floor_anti_flap(
        BatteryIntent.ALLOW_SELF_CONSUMPTION, soc_pct=5.0, min_reserve_soc=10.0,
        observed_mode=PhysicalMode.IDLE, holding=False)
    assert intent is BatteryIntent.HOLD_RESERVE
    assert holding is True
    assert reason is not None and "staying in idle" in reason


def test_already_aligned_is_quiet():
    intent, reason, holding = resolve_floor_anti_flap(
        BatteryIntent.ALLOW_SELF_CONSUMPTION, soc_pct=5.0, min_reserve_soc=10.0,
        observed_mode=PhysicalMode.AUTO, holding=True)
    assert intent is BatteryIntent.ALLOW_SELF_CONSUMPTION
    assert reason is None and holding is True


def test_entering_from_charge_prefers_auto():
    intent, reason, holding = resolve_floor_anti_flap(
        BatteryIntent.HOLD_RESERVE, soc_pct=8.0, min_reserve_soc=10.0,
        observed_mode=PhysicalMode.CHARGE, holding=False)
    assert intent is BatteryIntent.ALLOW_SELF_CONSUMPTION
    assert holding is True
    assert reason is not None and "prefer AUTO" in reason


def test_above_floor_unchanged():
    intent, reason, holding = resolve_floor_anti_flap(
        BatteryIntent.HOLD_RESERVE, soc_pct=50.0, min_reserve_soc=10.0,
        observed_mode=PhysicalMode.AUTO, holding=False)
    assert intent is BatteryIntent.HOLD_RESERVE
    assert reason is None and holding is False


def test_hysteresis_keeps_latch_until_resume_band():
    # Entered at 5%; SoC climbs to 12% (< 10+3) — still holding, still remaps.
    intent, reason, holding = resolve_floor_anti_flap(
        BatteryIntent.HOLD_RESERVE, soc_pct=12.0, min_reserve_soc=10.0,
        observed_mode=PhysicalMode.AUTO, holding=True)
    assert intent is BatteryIntent.ALLOW_SELF_CONSUMPTION and holding is True
    # Recovered to 13% — latch clears; plan HOLD is honoured again.
    intent, reason, holding = resolve_floor_anti_flap(
        BatteryIntent.HOLD_RESERVE, soc_pct=13.0, min_reserve_soc=10.0,
        observed_mode=PhysicalMode.AUTO, holding=True)
    assert intent is BatteryIntent.HOLD_RESERVE and reason is None and holding is False


def test_unknown_soc_never_latches():
    intent, reason, holding = resolve_floor_anti_flap(
        BatteryIntent.HOLD_RESERVE, soc_pct=None, min_reserve_soc=10.0,
        observed_mode=PhysicalMode.AUTO, holding=False)
    assert intent is BatteryIntent.HOLD_RESERVE and reason is None and holding is False


def test_grid_charge_clears_latch():
    intent, reason, holding = resolve_floor_anti_flap(
        BatteryIntent.GRID_CHARGE_TO_TARGET, soc_pct=5.0, min_reserve_soc=10.0,
        observed_mode=PhysicalMode.AUTO, holding=True)
    assert intent is BatteryIntent.GRID_CHARGE_TO_TARGET
    assert reason is None and holding is False


# --- control-tick integration --------------------------------------------------------------------

def _controlling_controller(driver=None) -> ModeController:
    lc = Lifecycle(dry_run=False, startup_grace_seconds=0.0)
    lc.start(NOW)
    lc.mark_sensors_validated()
    lc.mark_probe_ok()
    lc.mark_plan_loaded()
    lc.tick(NOW)
    return ModeController(driver or MockBatteryDriver(), lc, dry_run=False)


def _service(controller: ModeController, *, soc: float = 5.0):
    ctx = ControlContext()
    settings = effective_settings({})
    svc = ControlService(
        ctx=ctx, settings=settings, controller=controller, store=None, audit_store=None,
        price_source=None, solar_forecast=None,
        site_tz=ZoneInfo("Europe/Amsterdam"), dry_run=False, clock=None,
        current_soc=lambda now: soc,
        current_mode=lambda now: controller.driver.current_mode(),
        current_towers=lambda now: None,
        data_quality=lambda now: "fresh",
        car_charging=lambda now: False,
        load_by=lambda starts: {s: 0.0 for s in starts},
        active_strategy=lambda now: "winter",
        validate_plan_obj=lambda plan, now: PlanValidation(status="valid"),
        planner_cfg=lambda: None,
        summer_cfg=lambda s: None,
        adaptive_cfg=lambda: None,
    )
    return svc, ctx


def test_floor_flap_over_n_cycles_at_most_one_mode_write():
    """Synthetic SoC≤reserve over N alternating hold/auto cycles → ≤1 mode write; no storm."""
    controller = _controlling_controller()  # starts AUTO
    svc, ctx = _service(controller, soc=5.0)
    state = {"i": 0}

    def flapping(now):
        state["i"] += 1
        return _HOLD if state["i"] % 2 else _ASC

    svc.effective_intent = flapping
    audits = []
    for k in range(16):
        audits.extend(svc.control_tick(NOW + timedelta(seconds=k)))

    assert controller.driver.current_mode() is PhysicalMode.AUTO
    assert controller.switches_today == 0  # already AUTO — zero writes, budget intact
    floor_rows = [r for r in audits if r["detail"].get("outcome") == "floor_anti_flap"]
    assert len(floor_rows) == 1  # explained once, not every cycle
    assert "floor anti-flap" in floor_rows[0]["detail"]["reason"]
    assert ctx.floor_anti_flap_box["holding"] is True


def test_floor_flap_from_idle_stays_idle_without_writes():
    controller = _controlling_controller()
    controller.driver.apply(PhysicalMode.IDLE)
    svc, ctx = _service(controller, soc=5.0)
    state = {"i": 0}

    def flapping(now):
        state["i"] += 1
        return _ASC if state["i"] % 2 else _HOLD

    svc.effective_intent = flapping
    for k in range(12):
        svc.control_tick(NOW + timedelta(seconds=k))
    assert controller.driver.current_mode() is PhysicalMode.IDLE
    assert controller.switches_today == 0


def test_floor_anti_flap_does_not_block_grid_charge():
    controller = _controlling_controller()
    svc, ctx = _service(controller, soc=5.0)
    ctx.floor_anti_flap_box["holding"] = True  # was latched overnight
    svc.effective_intent = lambda now: _CHARGE
    svc.control_tick(NOW)
    assert controller.driver.current_mode() is PhysicalMode.CHARGE
    assert ctx.floor_anti_flap_box["holding"] is False


def test_above_floor_persistence_still_applies():
    """Regression: above the floor, existing intent-persistence anti-flap is unchanged."""
    controller = _controlling_controller()
    svc, _ctx = _service(controller, soc=50.0)
    svc.effective_intent = lambda now: _HOLD
    first = svc.control_tick(NOW)
    assert controller.driver.current_mode() is PhysicalMode.AUTO
    assert first and first[0]["detail"]["outcome"] == "intent_pending"
    svc.control_tick(NOW + timedelta(seconds=1))
    assert controller.driver.current_mode() is PhysicalMode.IDLE


def test_manual_override_bypasses_floor_anti_flap():
    controller = _controlling_controller()
    svc, _ctx = _service(controller, soc=5.0)
    svc.effective_intent = lambda now: (
        BatteryIntent.HOLD_RESERVE, "override", True, None, None, None, None)
    svc.control_tick(NOW)
    assert controller.driver.current_mode() is PhysicalMode.IDLE
