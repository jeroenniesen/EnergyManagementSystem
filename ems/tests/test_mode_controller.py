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


def test_failed_apply_and_failed_recovery_is_flagged():
    d = FailingMockBatteryDriver(fail_times=2)  # both the apply AND the AUTO recovery fail
    ctl = ModeController(d, _controlling_lifecycle(), dry_run=False)
    dec = ctl.decide(BatteryIntent.GRID_CHARGE_TO_TARGET, T0 + timedelta(seconds=200))
    assert dec.outcome == "failed_unrecovered"
    assert dec.applied is False


def test_preview_never_writes_even_when_controlling():
    d = MockBatteryDriver()
    ctl = ModeController(d, _controlling_lifecycle(), dry_run=False)
    dec = ctl.preview(BatteryIntent.GRID_CHARGE_TO_TARGET, T0 + timedelta(seconds=200))
    assert dec.outcome == "would_apply"
    assert dec.applied is False
    assert d.current_mode() is PhysicalMode.AUTO  # NOT written
    assert ctl.switches_today == 0  # NOT mutated


def test_failed_write_starts_dwell_and_counts_so_it_cannot_retry_every_cycle():
    # A write that never confirms (e.g. a half-offline tower) must NOT be re-attempted every
    # control cycle — that is write-amplification into struggling hardware. A failed attempt starts
    # the dwell timer and counts toward the daily cap, exactly like a confirmed switch.
    d = FailingMockBatteryDriver(fail_times=99)  # every apply() fails to confirm
    ctl = ModeController(d, _controlling_lifecycle(), dry_run=False, min_dwell_seconds=600)
    t1 = T0 + timedelta(seconds=200)
    dec = ctl.decide(BatteryIntent.GRID_CHARGE_TO_TARGET, t1)
    assert dec.outcome in ("failed_recovered", "failed_unrecovered")
    assert ctl.switches_today == 1          # the failed attempt counted toward the cap
    assert ctl.last_switch_at == t1         # ...and started the dwell timer
    # The next control cycle (5 min later, inside the 10 min dwell) is BLOCKED, not retried.
    nxt = ctl.decide(BatteryIntent.GRID_CHARGE_TO_TARGET, t1 + timedelta(seconds=300))
    assert nxt.outcome == "dwell"
    assert nxt.applied is False


def test_repeated_failed_writes_are_bounded_by_the_daily_cap():
    # Even spaced past the dwell, a never-confirming write can't run forever — the cap stops it.
    d = FailingMockBatteryDriver(fail_times=99)
    ctl = ModeController(d, _controlling_lifecycle(), dry_run=False,
                         min_dwell_seconds=600, max_switches_per_day=3)
    t = T0 + timedelta(seconds=200)
    for _ in range(3):
        dec = ctl.decide(BatteryIntent.GRID_CHARGE_TO_TARGET, t)
        assert dec.outcome in ("failed_recovered", "failed_unrecovered")
        t += timedelta(seconds=700)  # clear the 600 s dwell each iteration
    assert ctl.switches_today == 3
    capped = ctl.decide(BatteryIntent.GRID_CHARGE_TO_TARGET, t)
    assert capped.outcome == "cap_reached"
    assert capped.applied is False


def test_switch_cap_resets_on_a_new_local_day():
    d = MockBatteryDriver()
    ctl = ModeController(d, _controlling_lifecycle(), dry_run=False, max_switches_per_day=1)
    ctl.decide(BatteryIntent.GRID_CHARGE_TO_TARGET, T0 + timedelta(seconds=200))  # switch 1/1
    blocked = ctl.decide(BatteryIntent.HOLD_RESERVE, T0 + timedelta(seconds=900))  # same day
    assert blocked.outcome == "cap_reached"
    next_day = ctl.decide(BatteryIntent.HOLD_RESERVE, T0 + timedelta(days=1, seconds=200))
    assert next_day.outcome == "applied"  # counter reset at the new local date
