"""B-46 stage 1: `ControlService` exists as a UNIT — it constructs standalone (no FastAPI /
`create_app`) with mock collaborators + injected callables, and runs a control tick + a full async
cycle end-to-end. This is the proof B-46 asks for: the brain is testable outside the web app.

The exhaustive behaviour of the tick, the car-session lifecycle, the guards and the plan path is
still covered by the app-level suites (test_manual_control / test_car_session / test_car_guard /
test_failsafe_api / test_recovery_wiring / …), which exercise the SAME methods through the aliases
`create_app` sets. Here we only assert the standalone construction + a real tick works.
"""
import asyncio
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from ems.control.mode_controller import ModeController
from ems.control.override import Override
from ems.control.service import ControlContext, ControlService
from ems.domain import BatteryIntent, PhysicalMode
from ems.lifecycle import Lifecycle
from ems.settings import effective_settings
from ems.sources.battery import BatteryWriteUnconfirmed, MockBatteryDriver

NOW = datetime(2026, 6, 28, 12, 0, tzinfo=UTC)


def _controlling_controller(driver=None) -> ModeController:
    """A ModeController already driven to CONTROLLING (SPEC §13.3) so `control_tick` may command."""
    lc = Lifecycle(dry_run=False, startup_grace_seconds=0.0)
    lc.start(NOW)
    lc.mark_sensors_validated()
    lc.mark_probe_ok()
    lc.mark_plan_loaded()
    lc.tick(NOW)  # -> CONTROLLING
    return ModeController(driver or MockBatteryDriver(), lc, dry_run=False)  # starts in AUTO


def _service(
    controller: ModeController, *, audit_store=None, car_charging=None,
) -> tuple[ControlService, ControlContext]:
    """Build a ControlService with mock collaborators + trivial injected callables. `price_source`
    is None so the plan path is a no-op (`current_plan()` returns None) — this test drives the
    tick through an active manual override, not the planner, keeping the unit self-contained.
    `car_charging` overrides the (default: never-charging) car reading for the F2 deferral tests."""
    ctx = ControlContext()
    settings = effective_settings({})
    svc = ControlService(
        ctx=ctx, settings=settings, controller=controller, store=None, audit_store=audit_store,
        price_source=None, solar_forecast=None, site_tz=ZoneInfo("Europe/Amsterdam"), dry_run=False,
        current_soc=lambda now: 50.0,
        current_mode=lambda now: PhysicalMode.AUTO,
        current_towers=lambda now: None,
        data_quality=lambda now: "fresh",
        car_charging=car_charging if car_charging is not None else (lambda now: False),
        load_by=lambda starts: {s: 0.0 for s in starts},
        active_strategy=lambda now: "winter",
        validate_plan_obj=lambda plan, now: (_ for _ in ()).throw(AssertionError("unused")),
        planner_cfg=lambda: None,
        summer_cfg=lambda soc: None,
        adaptive_cfg=lambda: None,
    )
    return svc, ctx


def test_control_service_constructs_and_runs_a_tick_standalone():
    # Constructs with no FastAPI in sight, and a manual "charge now" override drives one real tick.
    controller = _controlling_controller()
    svc, ctx = _service(controller)
    ctx.override_box["ov"] = Override(
        intent=BatteryIntent.GRID_CHARGE_TO_TARGET, expires_at=NOW + timedelta(hours=1))

    records = svc.control_tick(NOW)

    # One audit record for the single per-cycle write, and the mock battery was actually commanded
    # to CHARGE (AUTO -> charge, "command sent").
    assert len(records) == 1
    assert "command sent" in records[0]["summary"]
    assert records[0]["detail"]["desired_mode"] == "charge"
    assert controller.driver.current_mode() is PhysicalMode.CHARGE


def test_control_service_run_cycle_audits_the_write():
    # The async wrapper (run_cycle) serialises on ctx.control_lock, runs the tick off the loop, and
    # writes the tick's records to the injected audit store — proven with a tiny fake store.
    appended: list[tuple[str, str]] = []

    class _FakeAudit:
        async def append(self, ts, kind, summary, detail):
            appended.append((kind, summary))

    controller = _controlling_controller()
    svc, ctx = _service(controller, audit_store=_FakeAudit())
    # run_cycle reads the real wall clock (datetime.now), so the override must be live at real-now,
    # not the fixed NOW the sync tick test uses.
    ctx.override_box["ov"] = Override(
        intent=BatteryIntent.GRID_CHARGE_TO_TARGET,
        expires_at=datetime.now(UTC) + timedelta(hours=1))

    asyncio.run(svc.run_cycle())

    assert len(appended) == 1
    kind, summary = appended[0]
    assert kind == "battery_decision"
    assert "command sent" in summary
    assert controller.driver.current_mode() is PhysicalMode.CHARGE


# ==================================================================================================
# F2 — defer non-safety grid-charge writes while a car discharge session is active
#
# Production audit: ~15 battery-command failures/week, ALL inside ~10 kW car-charging windows — the
# Indevolt's single embedded HTTP server saturates and register writes time out. While a car
# discharge session is active AND the car is still drawing, a non-safety PLANNER grid-charge is
# DEFERRED (no write, no cap/dwell spend, session kept alive) rather than written into the saturated
# device; the B-16 recovery/replan path re-issues it once the session ends. Safety actions (a manual
# override / the car-guard hold / return-to-AUTO / the data fail-safe) are NEVER deferred.
# ==================================================================================================

_CHARGE_INTENT = (BatteryIntent.GRID_CHARGE_TO_TARGET, "cheap window — charge to full",
                  False, 100.0, 4000.0, None, None)


def test_f2_defers_planner_grid_charge_during_active_car_session():
    # A planner grid-charge, an active session and the car still drawing => DEFER: no write, audited
    # with outcome "deferred", the session stays alive, and the cap/dwell clock is untouched.
    controller = _controlling_controller()  # MockBatteryDriver starts AUTO
    svc, ctx = _service(controller, car_charging=lambda now: True)
    ctx.car_session["active"] = True
    svc.effective_intent = lambda now: _CHARGE_INTENT  # pin the plan intent (no price wiring)

    records = svc.control_tick(NOW)

    assert controller.driver.current_mode() is PhysicalMode.AUTO  # untouched — nothing written
    assert len(records) == 1
    assert records[0]["detail"]["outcome"] == "deferred"
    assert "deferring grid-charge command" in records[0]["summary"]
    assert ctx.car_session["active"] is True  # session kept alive across the deferral
    # A deferral must NOT spend the daily switch cap or start the dwell clock.
    assert controller.switches_today == 0
    assert controller.last_switch_at is None


def test_f2_deferral_is_deduped_across_cycles():
    # Explainability-first, but not spammy: a long car+charge window audits the deferral ONCE, not a
    # row every cycle — mirroring how a recurring held/blocked decision is explained once.
    controller = _controlling_controller()
    svc, ctx = _service(controller, car_charging=lambda now: True)
    ctx.car_session["active"] = True
    svc.effective_intent = lambda now: _CHARGE_INTENT
    first = svc.control_tick(NOW)
    again = svc.control_tick(NOW + timedelta(seconds=1))
    assert len(first) == 1 and first[0]["detail"]["outcome"] == "deferred"
    assert again == []  # deduped — same deferral episode


def test_f2_manual_override_grid_charge_is_not_deferred():
    # SAFETY: a manual "charge now" override bypasses the deferral (priority actions bypass
    # everything). The override wins, ends the car session, and the charge is actually sent.
    controller = _controlling_controller()
    svc, ctx = _service(controller, car_charging=lambda now: True)
    ctx.car_session["active"] = True
    ctx.override_box["ov"] = Override(
        intent=BatteryIntent.GRID_CHARGE_TO_TARGET, expires_at=NOW + timedelta(hours=1))

    svc.control_tick(NOW)  # real effective_intent — the active override resolves to GRID_CHARGE

    assert controller.driver.current_mode() is PhysicalMode.CHARGE  # sent, NOT deferred


def test_f2_deferred_charge_is_issued_on_the_first_post_session_tick():
    # B-16 pickup: while the car charges the grid-charge is deferred; the moment the car stops the
    # session ends and the still-wanted charge is issued (end_cycles=1 => the session ends at once).
    controller = _controlling_controller()
    car_on = {"v": True}
    svc, ctx = _service(controller, car_charging=lambda now: car_on["v"])
    ctx.car_session["active"] = True
    svc._settings["control.car_session_end_cycles"] = 1
    svc.effective_intent = lambda now: _CHARGE_INTENT

    # Cycle 1: car still charging => deferred, nothing written.
    svc.control_tick(NOW)
    assert controller.driver.current_mode() is PhysicalMode.AUTO
    assert controller.switches_today == 0

    # Car stops => the session ends and the deferred charge is picked up on this post-session tick.
    car_on["v"] = False
    svc.control_tick(NOW + timedelta(seconds=1))
    assert controller.driver.current_mode() is PhysicalMode.CHARGE


def test_f2_master_switch_off_does_not_defer_the_plan():
    # With the car-charging master switch OFF the planner runs untouched — no deferral even with a
    # (stale) active-session flag and the car drawing.
    controller = _controlling_controller()
    svc, ctx = _service(controller, car_charging=lambda now: True)
    ctx.car_session["active"] = True
    svc._settings["control.hold_battery_when_car_charging"] = False
    svc._settings["control.car_session_end_cycles"] = 1
    svc.effective_intent = lambda now: _CHARGE_INTENT

    svc.control_tick(NOW)
    assert controller.driver.current_mode() is PhysicalMode.CHARGE  # plan ran, not deferred


# ==================================================================================================
# B-46 (b) — the tick consults ActionDecision.audit (F3) when recording an unconfirmed incident row
#
# The controller's episode de-dupe sets dec.audit False for repeat "device slow" cycles within one
# stuck episode (and True again after ~60 min). The tick must record ONE incident row per episode,
# not one per cycle — and honour the hourly re-log (which the old held-box latch would suppress).
# ==================================================================================================

class _AlwaysUnconfirmedDriver(MockBatteryDriver):
    """Every write times out (BatteryWriteUnconfirmed) — the stuck 'device slow' episode."""

    def apply(self, mode, *, target_soc=None, power_w=None):
        raise BatteryWriteUnconfirmed("device slow — write timed out")


def test_repeated_unconfirmed_episode_records_one_incident_row_then_relogs_hourly():
    # A manual override forces a write every tick (bypasses dwell) so all cycles hit the unconfirmed
    # path. dec.audit gates the incident row: one for the episode, then one more after >60 min.
    controller = _controlling_controller(_AlwaysUnconfirmedDriver())
    svc, ctx = _service(controller)
    ctx.override_box["ov"] = Override(
        intent=BatteryIntent.GRID_CHARGE_TO_TARGET, expires_at=NOW + timedelta(hours=3))

    rows = []
    for i in range(5):  # five back-to-back cycles within the hour
        rows += svc.control_tick(NOW + timedelta(seconds=i))
    within_hour = [r for r in rows if r["detail"].get("outcome") == "unconfirmed"]
    assert len(within_hour) == 1  # ONE incident for the whole episode, not one per cycle
    assert "unconfirmed" in within_hour[0]["summary"]

    # Still stuck >60 min later → a SECOND incident row (periodic evidence of the ongoing outage).
    later = svc.control_tick(NOW + timedelta(minutes=61))
    relog = [r for r in later if r["detail"].get("outcome") == "unconfirmed"]
    assert len(relog) == 1


# ==================================================================================================
# Intent persistence (anti-flap) — a ROUTINE (idle / non-AUTO) intent change must persist
# control.intent_persistence_cycles consecutive cycles before it commands the battery, so a
# transient auto<->idle flap can never burn the daily switch cap and starve a committed grid-charge
# (the 07-12 incident: 13 routine flaps exhausted the cap by 09:48, a COMMITTED charge then missed
# its deadline by 66 min). EXEMPT — act immediately, exactly as today: a manual override, a
# car-charging safety context, a committed GRID_CHARGE_TO_TARGET, and any return-to-AUTO / fail-safe
# (AUTO is never held, mirroring the controller's _gate). 1 = legacy (no smoothing).
# ==================================================================================================

_HOLD = (BatteryIntent.HOLD_RESERVE, "hold reserve", False, None, None, None, None)
_CHARGE = (BatteryIntent.GRID_CHARGE_TO_TARGET, "cheap window", False, 90.0, 4000.0, None, None)
_ASC = (BatteryIntent.ALLOW_SELF_CONSUMPTION, "self-consumption", False, None, None, None, None)


def test_routine_intent_change_is_observed_before_switching():
    # settings default intent_persistence_cycles=2 => cycle 1 OBSERVES (no write), cycle 2 ACTS.
    controller = _controlling_controller()  # AUTO
    svc, ctx = _service(controller)
    svc.effective_intent = lambda now: _HOLD

    first = svc.control_tick(NOW)
    assert controller.driver.current_mode() is PhysicalMode.AUTO  # nothing written on cycle 1
    assert len(first) == 1 and first[0]["detail"]["outcome"] == "intent_pending"
    assert "confirming" in first[0]["summary"]
    assert controller.switches_today == 0  # no switch spent while observing

    svc.control_tick(NOW + timedelta(seconds=1))  # same intent persists -> act
    assert controller.driver.current_mode() is PhysicalMode.IDLE


def test_transient_flap_never_issues_a_switch_or_burns_the_cap():
    # The 07-12 pattern: routine idle<->auto flapping at ~1-cycle period. With persistence=2 the
    # idle side is never confirmed, so ZERO writes happen and the daily switch cap is untouched.
    controller = _controlling_controller()  # AUTO
    svc, ctx = _service(controller)
    state = {"i": 0}

    def flapping(now):
        state["i"] += 1
        return _HOLD if state["i"] % 2 else _ASC  # idle, auto, idle, auto, ...

    svc.effective_intent = flapping
    for k in range(12):
        svc.control_tick(NOW + timedelta(seconds=k))
    assert controller.driver.current_mode() is PhysicalMode.AUTO  # never switched to idle
    assert controller.switches_today == 0                          # daily cap untouched


def test_committed_grid_charge_is_never_held_for_persistence():
    # A deadline-bearing commitment must not lose a cycle to smoothing.
    controller = _controlling_controller()  # AUTO
    svc, ctx = _service(controller)
    svc.effective_intent = lambda now: _CHARGE
    svc.control_tick(NOW)
    assert controller.driver.current_mode() is PhysicalMode.CHARGE  # acted on the FIRST cycle


def test_return_to_auto_acts_immediately_not_held():
    # Return-to-AUTO / fail-safe is exempt: AUTO is never held (mirrors _gate).
    controller = _controlling_controller()
    controller.driver.apply(PhysicalMode.IDLE)  # start in IDLE
    svc, ctx = _service(controller)
    svc._current_mode = lambda now: PhysicalMode.IDLE
    svc.effective_intent = lambda now: _ASC
    svc.control_tick(NOW)
    assert controller.driver.current_mode() is PhysicalMode.AUTO  # immediate on cycle 1


def test_manual_override_bypasses_intent_persistence():
    # A manual override is a deliberate priority command — never smoothed (HOLD_RESERVE -> IDLE).
    controller = _controlling_controller()  # AUTO
    svc, ctx = _service(controller)
    svc.effective_intent = lambda now: (  # override_active=True (4th element)
        BatteryIntent.HOLD_RESERVE, "override", True, None, None, None, None)
    svc.control_tick(NOW)
    assert controller.driver.current_mode() is PhysicalMode.IDLE  # acted at once


def test_car_charging_safety_context_bypasses_persistence():
    # While the car charges the final decide runs with priority=True (a safety context) — a routine
    # hold there must act immediately, never be smoothed (car-guard exemption).
    controller = _controlling_controller()  # AUTO
    svc, ctx = _service(controller, car_charging=lambda now: True)
    svc.effective_intent = lambda now: _HOLD  # car_action None -> falls through to the final decide
    svc.control_tick(NOW)
    assert controller.driver.current_mode() is PhysicalMode.IDLE  # acted at once (priority)


def test_persistence_cycles_one_reproduces_legacy_immediate_switch():
    controller = _controlling_controller()  # AUTO
    svc, ctx = _service(controller)
    svc._settings["control.intent_persistence_cycles"] = 1  # legacy: no smoothing
    svc.effective_intent = lambda now: _HOLD
    svc.control_tick(NOW)
    assert controller.driver.current_mode() is PhysicalMode.IDLE  # acted on cycle 1


def test_persistence_hold_is_audited_once_across_the_hold():
    # A multi-cycle hold (persistence=3) is explained ONCE, deduped — not one log per cycle.
    controller = _controlling_controller()  # AUTO
    svc, ctx = _service(controller)
    svc._settings["control.intent_persistence_cycles"] = 3  # observe two cycles, act on the third
    svc.effective_intent = lambda now: _HOLD
    c1 = svc.control_tick(NOW)
    c2 = svc.control_tick(NOW + timedelta(seconds=1))
    c3 = svc.control_tick(NOW + timedelta(seconds=2))  # noqa: F841 — acts here
    pend1 = [r for r in c1 if r["detail"]["outcome"] == "intent_pending"]
    pend2 = [r for r in c2 if r["detail"]["outcome"] == "intent_pending"]
    assert len(pend1) == 1  # explained once...
    assert pend2 == []      # ...deduped on the second observe cycle
    assert controller.driver.current_mode() is PhysicalMode.IDLE  # acted on the third
