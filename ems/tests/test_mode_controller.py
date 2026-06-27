from datetime import UTC, datetime, timedelta

from ems.control.mode_controller import ModeController
from ems.domain import BatteryIntent, PhysicalMode
from ems.lifecycle import Lifecycle
from ems.sources.battery import FailingMockBatteryDriver, MockBatteryDriver

T0 = datetime(2026, 6, 27, 10, 0, tzinfo=UTC)


def _controlling_lifecycle():
    lc = Lifecycle(dry_run=False, startup_grace_seconds=120)
    lc.start(T0)
    lc.mark_sensors_validated()
    lc.mark_probe_ok()
    lc.mark_plan_loaded()
    lc.tick(T0 + timedelta(seconds=121))  # -> CONTROLLING
    return lc


def test_dry_run_never_writes():
    d = MockBatteryDriver()
    ctl = ModeController(d, Lifecycle(dry_run=True), dry_run=True)
    dec = ctl.decide(BatteryIntent.GRID_CHARGE_TO_TARGET, T0)
    assert dec.outcome == "dry_run"
    assert dec.applied is False
    assert d.current_mode() is PhysicalMode.AUTO  # untouched


def test_not_controlling_does_not_write():
    d = MockBatteryDriver()
    ctl = ModeController(d, Lifecycle(dry_run=False), dry_run=False)  # never started -> INACTIVE
    dec = ctl.decide(BatteryIntent.GRID_CHARGE_TO_TARGET, T0)
    assert dec.outcome == "not_controlling"
    assert d.current_mode() is PhysicalMode.AUTO


def test_applies_when_controlling_and_mode_differs():
    d = MockBatteryDriver()
    ctl = ModeController(d, _controlling_lifecycle(), dry_run=False)
    now = T0 + timedelta(seconds=200)
    dec = ctl.decide(BatteryIntent.GRID_CHARGE_TO_TARGET, now)
    assert dec.outcome == "applied"
    assert dec.applied is True
    assert d.current_mode() is PhysicalMode.CHARGE


def test_idempotent_when_already_in_mode():
    d = MockBatteryDriver()  # starts AUTO
    ctl = ModeController(d, _controlling_lifecycle(), dry_run=False)
    dec = ctl.decide(BatteryIntent.ALLOW_SELF_CONSUMPTION, T0 + timedelta(seconds=200))
    assert dec.outcome == "idempotent"
    assert dec.applied is False


def test_min_dwell_blocks_rapid_switch():
    d = MockBatteryDriver()
    ctl = ModeController(d, _controlling_lifecycle(), dry_run=False, min_dwell_seconds=600)
    t1 = T0 + timedelta(seconds=200)
    ctl.decide(BatteryIntent.GRID_CHARGE_TO_TARGET, t1)  # applied, last_switch=t1
    dec = ctl.decide(BatteryIntent.HOLD_RESERVE, t1 + timedelta(seconds=60))  # within dwell
    assert dec.outcome == "dwell"
    assert dec.applied is False


def test_daily_switch_cap_holds():
    d = MockBatteryDriver()
    ctl = ModeController(d, _controlling_lifecycle(), dry_run=False, max_switches_per_day=0)
    dec = ctl.decide(BatteryIntent.GRID_CHARGE_TO_TARGET, T0 + timedelta(seconds=200))
    assert dec.outcome == "cap_reached"
    assert dec.applied is False


def test_failed_apply_recovers_to_auto():
    d = FailingMockBatteryDriver(fail_times=1)
    ctl = ModeController(d, _controlling_lifecycle(), dry_run=False)
    dec = ctl.decide(BatteryIntent.GRID_CHARGE_TO_TARGET, T0 + timedelta(seconds=200))
    assert dec.outcome == "failed_recovered"
    assert dec.desired_mode is PhysicalMode.AUTO
    assert d.current_mode() is PhysicalMode.AUTO  # recovered to safe mode
