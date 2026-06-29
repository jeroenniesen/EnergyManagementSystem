"""Graceful-shutdown safe restore (SPEC §6.5 / operator runbook): in operational mode, stopping
the service must hand the battery back to its safe vendor mode so an upgrade/reboot/launchd restart
can't leave it in a forced charge/hold/discharge. Validated against a mock armed driver."""
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from ems.control.mode_controller import ModeController
from ems.domain import PhysicalMode
from ems.lifecycle import Lifecycle
from ems.sources.mock import MockSource
from ems.sources.prices import MockPriceSource
from ems.web.api import create_app

AMS = ZoneInfo("Europe/Amsterdam")


class _ArmedRecordingDriver:
    """A live-shaped, ARMED driver that records every apply() and starts in a forced mode."""

    def __init__(self, mode=PhysicalMode.CHARGE):
        self._mode = mode
        self.applied: list[PhysicalMode] = []
        self.armed = True

    def current_mode(self):
        return self._mode

    def apply(self, mode, *, target_soc=None, power_w=None):
        self.applied.append(mode)
        self._mode = mode
        return True  # confirmed

    def probe(self):  # called once at startup; raising is fine (lifespan catches it)
        raise RuntimeError("no probe in this test")


def _operational_app(driver, *, dry_run, last_action, original=PhysicalMode.AUTO):
    ctl = ModeController(driver, Lifecycle(dry_run=dry_run), dry_run=dry_run)
    ctl.last_confirmed_action = last_action
    ctl.original_vendor_mode = original
    return create_app(
        MockSource(), dry_run=dry_run, dev_mode="live", tz=AMS,
        price_source=MockPriceSource(AMS), controller=ctl,
        control_cycle_seconds=3600,  # loop waits before its first tick → won't interfere
    )


def test_operational_shutdown_restores_safe_vendor_mode():
    # EMS had forced CHARGE; a graceful stop must restore the pre-EMS vendor mode (AUTO).
    driver = _ArmedRecordingDriver(mode=PhysicalMode.CHARGE)
    app = _operational_app(driver, dry_run=False, last_action=PhysicalMode.CHARGE)
    with TestClient(app):
        pass  # enter + exit the lifespan (graceful shutdown)
    assert driver.applied and driver.applied[-1] is PhysicalMode.AUTO


def test_dry_run_shutdown_never_touches_the_battery():
    # In dry-run the battery is never written — shutdown must not issue any apply().
    driver = _ArmedRecordingDriver(mode=PhysicalMode.AUTO)
    app = _operational_app(driver, dry_run=True, last_action=PhysicalMode.CHARGE)
    with TestClient(app):
        pass
    assert driver.applied == []


def test_no_restore_when_ems_never_forced_a_mode():
    # If EMS only ever ran self-consumption (AUTO), there's nothing to undo — no write on shutdown.
    driver = _ArmedRecordingDriver(mode=PhysicalMode.AUTO)
    app = _operational_app(driver, dry_run=False, last_action=PhysicalMode.AUTO)
    with TestClient(app):
        pass
    assert driver.applied == []


def test_restore_falls_back_to_auto_not_a_forced_original():
    # Even if the captured "original" was a forced energy mode, never restore INTO charge/discharge.
    driver = _ArmedRecordingDriver(mode=PhysicalMode.DISCHARGE)
    app = _operational_app(driver, dry_run=False, last_action=PhysicalMode.DISCHARGE,
                           original=PhysicalMode.CHARGE)
    with TestClient(app):
        pass
    assert driver.applied and driver.applied[-1] is PhysicalMode.AUTO
