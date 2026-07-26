from __future__ import annotations

import threading
import time

from ems.control.command_fence import BatteryCommandFence, CommandClass


def test_physical_boundary_serializes_tickets_in_generation_order():
    fence = BatteryCommandFence()
    first = fence.admit_routine()
    assert first is not None and fence.enter(first) is True
    second = fence.reserve(CommandClass.OVERRIDE)
    assert second is not None

    entered = threading.Event()

    def wait_for_second() -> None:
        assert fence.enter(second) is True
        entered.set()
        fence.leave(second)
        fence.release(second)

    waiter = threading.Thread(target=wait_for_second)
    waiter.start()
    assert entered.wait(0.05) is False
    fence.leave(first)
    fence.release(first)
    assert entered.wait(1.0) is True
    waiter.join(1.0)
    assert fence.empty()


def test_recovery_supersedes_a_routine_that_has_not_entered():
    fence = BatteryCommandFence()
    stale = fence.admit_routine()
    assert stale is not None
    recovery = fence.begin_recovery(stale)
    assert recovery is not None

    assert fence.enter(stale) is False
    fence.release(stale)
    assert fence.enter(recovery) is True
    fence.leave(recovery)
    fence.release(recovery)
    assert fence.empty()


def test_recovery_waits_behind_an_already_running_routine_and_writes_next():
    fence = BatteryCommandFence()
    stale = fence.admit_routine()
    assert stale is not None and fence.enter(stale) is True
    recovery = fence.begin_recovery(stale)
    assert recovery is not None

    entered = threading.Event()
    waiter = threading.Thread(target=lambda: (fence.enter(recovery), entered.set()))
    waiter.start()
    assert entered.wait(0.05) is False
    fence.leave(stale)
    fence.release(stale)
    assert entered.wait(1.0) is True
    fence.leave(recovery)
    fence.release(recovery)
    waiter.join(1.0)


def test_one_recovery_ticket_per_stale_generation():
    fence = BatteryCommandFence()
    stale = fence.admit_routine()
    assert stale is not None
    first = fence.begin_recovery(stale)
    second = fence.begin_recovery(stale)
    assert first is second


def test_routine_admission_waits_until_stale_and_recovery_release():
    fence = BatteryCommandFence()
    stale = fence.admit_routine()
    assert stale is not None
    recovery = fence.begin_recovery(stale)
    assert recovery is not None
    assert fence.admit_routine() is None
    fence.release(stale)
    assert fence.admit_routine() is None
    fence.release(recovery)
    assert fence.admit_routine() is not None


def test_draining_refuses_normal_admission_but_allows_shutdown():
    fence = BatteryCommandFence()
    fence.mark_draining()
    assert fence.admit_routine() is None
    assert fence.reserve(CommandClass.OVERRIDE) is None
    shutdown = fence.reserve(CommandClass.SHUTDOWN)
    assert shutdown is not None
    fence.clear_draining()
    assert fence.reserve(CommandClass.OVERRIDE) is not None


def test_snapshot_reports_outstanding_active_and_recovery_state():
    fence = BatteryCommandFence()
    stale = fence.admit_routine()
    assert stale is not None and fence.enter(stale) is True
    recovery = fence.begin_recovery(stale)
    snap = fence.snapshot()
    assert snap.outstanding == 2
    assert snap.active_generation == stale.generation
    assert snap.recovery_pending is True
    assert recovery is not None


def test_enter_timeout_does_not_steal_active_command_lane():
    fence = BatteryCommandFence()
    active = fence.reserve(CommandClass.OVERRIDE)
    shutdown = fence.reserve(CommandClass.SHUTDOWN)
    assert active is not None and shutdown is not None
    assert fence.enter(active) is True

    started = time.monotonic()
    assert fence.enter(shutdown, timeout_seconds=0.02) is False
    assert time.monotonic() - started < 0.5
    assert fence.snapshot().active_generation == active.generation

    fence.leave(active)
    fence.release(active)
    fence.release(shutdown)


def test_newer_override_suppresses_stale_timeout_recovery():
    fence = BatteryCommandFence()
    routine = fence.admit_routine()
    override = fence.reserve(CommandClass.OVERRIDE)
    assert routine is not None and override is not None

    assert fence.begin_recovery(routine) is None
    assert fence.enter(routine) is False
    assert fence.enter(override) is True

    fence.leave(override)
    fence.release(routine)
    fence.release(override)
