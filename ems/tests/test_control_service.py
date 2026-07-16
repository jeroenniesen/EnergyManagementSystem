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
from ems.sources.battery import MockBatteryDriver

NOW = datetime(2026, 6, 28, 12, 0, tzinfo=UTC)


def _controlling_controller() -> ModeController:
    """A ModeController already driven to CONTROLLING (SPEC §13.3) so `control_tick` may command."""
    lc = Lifecycle(dry_run=False, startup_grace_seconds=0.0)
    lc.start(NOW)
    lc.mark_sensors_validated()
    lc.mark_probe_ok()
    lc.mark_plan_loaded()
    lc.tick(NOW)  # -> CONTROLLING
    return ModeController(MockBatteryDriver(), lc, dry_run=False)  # starts in AUTO


def _service(
    controller: ModeController, *, audit_store=None,
) -> tuple[ControlService, ControlContext]:
    """Build a ControlService with mock collaborators + trivial injected callables. `price_source`
    is None so the plan path is a no-op (`current_plan()` returns None) — this test drives the
    tick through an active manual override, not the planner, keeping the unit self-contained."""
    ctx = ControlContext()
    settings = effective_settings({})
    svc = ControlService(
        ctx=ctx, settings=settings, controller=controller, store=None, audit_store=audit_store,
        price_source=None, solar_forecast=None, site_tz=ZoneInfo("Europe/Amsterdam"), dry_run=False,
        current_soc=lambda now: 50.0,
        current_mode=lambda now: PhysicalMode.AUTO,
        current_towers=lambda now: None,
        data_quality=lambda now: "fresh",
        car_charging=lambda now: False,
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
