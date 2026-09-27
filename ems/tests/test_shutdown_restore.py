"""Graceful-shutdown safe restore (SPEC §6.5 / operator runbook) + epic #111 invariant I5 (#127).

In operational/armed mode, stopping the service must hand the battery back to its safe vendor mode
so an upgrade/reboot/launchd restart can't leave it in a forced charge/hold/discharge. Dry-run
writes nothing. `armed` is a required BatteryDriver port member — `_shutdown_restore` reads
`controller.driver.armed` directly (no getattr default).
"""
import threading
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from ems.control.command_fence import CommandClass
from ems.control.mode_controller import ModeController
from ems.domain import PhysicalMode
from ems.lifecycle import Lifecycle
from ems.sources.mock import MockSource
from ems.sources.ports import BatteryDriver
from ems.sources.prices import MockPriceSource
from ems.web.api import create_app

AMS = ZoneInfo("Europe/Amsterdam")


class _ArmedRecordingDriver:
    """A live-shaped, ARMED driver that records every apply() and starts in a forced mode."""

    def __init__(self, mode=PhysicalMode.CHARGE, *, armed: bool = True):
        self._mode = mode
        self.applied: list[PhysicalMode] = []
        self.writes: list[PhysicalMode] = []
        self._armed = armed

    @property
    def armed(self) -> bool:
        return self._armed

    def current_mode(self):
        return self._mode

    def configure_power_limits(self, *, max_charge_w: float, max_discharge_w: float) -> None:
        # Required BatteryDriver port member (#139); no-op for this recording double.
        return None

    def apply(self, mode, *, target_soc=None, power_w=None):
        self.applied.append(mode)
        self.writes.append(mode)
        self._mode = mode
        return True  # confirmed

    def probe(self):  # called once at startup; raising is fine (lifespan catches it)
        raise RuntimeError("no probe in this test")


def _operational_app(driver, *, dry_run, last_action, original=PhysicalMode.AUTO):
    ctl = ModeController(driver, Lifecycle(dry_run=dry_run), dry_run=dry_run)
    ctl.last_confirmed_action = last_action
    ctl.original_vendor_mode = original
    app = create_app(
        MockSource(), dry_run=dry_run, dev_mode="live", tz=AMS,
        price_source=MockPriceSource(AMS), controller=ctl,
        control_cycle_seconds=3600,  # loop waits before its first tick → won't interfere
    )
    return app, ctl


def test_operational_shutdown_restores_safe_vendor_mode():
    # #127 / I5: armed + operational graceful stop → AUTO via lifespan `_shutdown_restore`
    # (which calls `ControlService.restore_for_shutdown`).
    driver = _ArmedRecordingDriver(mode=PhysicalMode.CHARGE)
    assert isinstance(driver, BatteryDriver)
    assert driver.armed is True
    app, ctl = _operational_app(driver, dry_run=False, last_action=PhysicalMode.CHARGE)
    with TestClient(app):
        pass  # enter + exit the lifespan (graceful shutdown)
    assert driver.writes[-1] is PhysicalMode.AUTO
    assert driver.applied and driver.applied[-1] is PhysicalMode.AUTO
    # Fix 6: the REAL lifespan-shutdown restore also un-wedges the refuse-when-busy restart gate via
    # note_confirmed_auto() — the NEXT process must boot with a confirmed-AUTO, not-unconfirmed
    # control state (else the I2 restart gate would sit at 409 forever after this deploy/restart,
    # since an AUTO-desired planner cycle is idempotent and never rewrites last_confirmed_action).
    # Exercised through the ACTUAL lifespan shutdown, not a direct note_confirmed_auto() call.
    assert ctl.last_confirmed_action is PhysicalMode.AUTO
    assert ctl.last_command_unconfirmed is False


def test_restore_for_shutdown_service_seam_writes_auto_when_armed():
    """#127 Klaar-als 5: restore_for_shutdown writes AUTO when armed/operational."""
    from ems.control.service import ControlContext, ControlService
    from ems.settings import effective_settings

    driver = _ArmedRecordingDriver(mode=PhysicalMode.DISCHARGE)
    ctl = ModeController(driver, Lifecycle(dry_run=False), dry_run=False)
    ctl.last_confirmed_action = PhysicalMode.DISCHARGE
    svc = ControlService(
        ctx=ControlContext(), settings=effective_settings({}), controller=ctl, store=None,
        audit_store=None, price_source=None, solar_forecast=None, site_tz=AMS, dry_run=False,
        current_soc=lambda now: 50.0,
        current_mode=lambda now: driver.current_mode(),
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
    assert svc.restore_for_shutdown(PhysicalMode.AUTO) is True
    assert driver.writes[-1] is PhysicalMode.AUTO
    assert driver.current_mode() is PhysicalMode.AUTO


def test_i5_shutdown_restore_reads_armed_without_getattr():
    """#127 Klaar-als 4 / epic #111 I5: shutdown path uses driver.armed (no getattr default)."""
    import inspect

    from ems.web import api as api_mod

    source = inspect.getsource(api_mod.create_app)
    assert 'getattr(controller.driver, "armed"' not in source
    assert "getattr(controller.driver, 'armed'" not in source
    assert "controller.driver.armed" in source


def test_dry_run_shutdown_never_touches_the_battery():
    # #127 / I5: in dry-run the battery is never written — shutdown must not issue any apply().
    driver = _ArmedRecordingDriver(mode=PhysicalMode.CHARGE)
    app, _ctl = _operational_app(driver, dry_run=True, last_action=PhysicalMode.CHARGE)
    with TestClient(app):
        pass
    assert driver.applied == []
    assert driver.writes == []


def test_no_restore_when_ems_never_forced_a_mode():
    # If EMS only ever ran self-consumption (AUTO), there's nothing to undo — no write on shutdown.
    driver = _ArmedRecordingDriver(mode=PhysicalMode.AUTO)
    app, _ctl = _operational_app(driver, dry_run=False, last_action=PhysicalMode.AUTO)
    with TestClient(app):
        pass
    assert driver.applied == []


def test_restore_falls_back_to_auto_not_a_forced_original():
    # Even if the captured "original" was a forced energy mode, never restore INTO charge/discharge.
    driver = _ArmedRecordingDriver(mode=PhysicalMode.DISCHARGE)
    app, _ctl = _operational_app(driver, dry_run=False, last_action=PhysicalMode.DISCHARGE,
                                 original=PhysicalMode.CHARGE)
    with TestClient(app):
        pass
    assert driver.applied and driver.applied[-1] is PhysicalMode.AUTO


def test_shutdown_auto_waits_for_existing_write_and_is_final():
    class _BlockedDriver(_ArmedRecordingDriver):
        def __init__(self):
            super().__init__(PhysicalMode.AUTO)
            self.charge_entered = threading.Event()
            self.release_charge = threading.Event()

        def apply(self, mode, *, target_soc=None, power_w=None):
            if mode is PhysicalMode.CHARGE:
                self.charge_entered.set()
                assert self.release_charge.wait(3.0)
            return super().apply(mode, target_soc=target_soc, power_w=power_w)

    driver = _BlockedDriver()
    # The blocked write has not confirmed yet, so persisted state still looks safely AUTO.
    app, _ctl = _operational_app(driver, dry_run=False, last_action=PhysicalMode.AUTO)
    svc = app.state.control_service
    stale = svc.reserve_writer(CommandClass.ROUTINE)
    assert stale is not None

    def old_write() -> None:
        assert svc._ctx.command_fence.enter(stale) is True
        try:
            driver.apply(PhysicalMode.CHARGE)
        finally:
            svc._ctx.command_fence.leave(stale)
            svc.release_writer(stale)

    old = threading.Thread(target=old_write, daemon=True)
    old.start()
    assert driver.charge_entered.wait(1.0)

    restored: list[bool] = []
    shutdown = threading.Thread(
        target=lambda: restored.append(svc.restore_for_shutdown(PhysicalMode.AUTO)), daemon=True)
    shutdown.start()
    assert restored == []
    driver.release_charge.set()
    old.join(2.0)
    shutdown.join(2.0)

    assert restored == [True]
    assert driver.applied == [PhysicalMode.CHARGE, PhysicalMode.AUTO]
    assert driver.current_mode() is PhysicalMode.AUTO
