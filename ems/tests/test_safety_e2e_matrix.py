"""Hermetic end-to-end checks for the battery safety boundaries.

These tests exercise the lifecycle, mode controller, and shutdown seam together with an in-memory
driver.  No network or real battery adapter is involved.
"""

from datetime import UTC, datetime

from ems.control.mode_controller import ModeController
from ems.domain import BatteryIntent, PhysicalMode
from ems.lifecycle import Lifecycle
from ems.sources.battery import FailingMockBatteryDriver, MockBatteryDriver

NOW = datetime(2026, 7, 29, 12, 0, tzinfo=UTC)


class RecordingDriver(MockBatteryDriver):
    def __init__(self) -> None:
        super().__init__()
        self.writes: list[PhysicalMode] = []

    def apply(self, mode, *, target_soc=None, power_w=None):
        self.writes.append(mode)
        return super().apply(mode, target_soc=target_soc, power_w=power_w)


def _lifecycle(*, dry_run: bool) -> Lifecycle:
    lifecycle = Lifecycle(dry_run=dry_run, startup_grace_seconds=0)
    lifecycle.start(NOW)
    lifecycle.mark_sensors_validated()
    lifecycle.mark_probe_ok()
    lifecycle.mark_plan_loaded()
    lifecycle.tick(NOW)
    return lifecycle


def test_dry_run_end_to_end_never_writes() -> None:
    driver = RecordingDriver()
    controller = ModeController(driver, _lifecycle(dry_run=True), dry_run=True)

    decision = controller.decide(BatteryIntent.GRID_CHARGE_TO_TARGET, NOW)

    assert decision.outcome == "dry_run"
    assert driver.current_mode() is PhysicalMode.AUTO
    assert driver.writes == []


def test_switch_cap_refuses_command_without_touching_driver() -> None:
    driver = RecordingDriver()
    controller = ModeController(
        driver, _lifecycle(dry_run=False), dry_run=False, max_switches_per_day=0
    )

    decision = controller.decide(BatteryIntent.GRID_CHARGE_TO_TARGET, NOW)

    assert decision.outcome == "cap_reached"
    assert decision.applied is False
    assert driver.current_mode() is PhysicalMode.AUTO
    assert driver.writes == []


def test_rejected_write_falls_back_to_auto() -> None:
    driver = FailingMockBatteryDriver(fail_times=1)
    controller = ModeController(driver, _lifecycle(dry_run=False), dry_run=False)

    decision = controller.decide(BatteryIntent.GRID_CHARGE_TO_TARGET, NOW)

    assert decision.outcome == "failed_recovered"
    assert decision.desired_mode is PhysicalMode.AUTO
    assert driver.current_mode() is PhysicalMode.AUTO


def test_shutdown_restoration_places_battery_in_auto() -> None:
    """The service's shutdown seam restores AUTO after an earlier live command."""
    from ems.tests.test_control_service import _controlling_controller, _service

    driver = RecordingDriver()
    controller = _controlling_controller(driver)
    controller.decide(BatteryIntent.GRID_CHARGE_TO_TARGET, NOW)
    assert driver.current_mode() is PhysicalMode.CHARGE

    service, _ = _service(controller)
    assert service.restore_for_shutdown(PhysicalMode.AUTO) is True
    assert driver.current_mode() is PhysicalMode.AUTO
    assert driver.writes[-1] is PhysicalMode.AUTO
