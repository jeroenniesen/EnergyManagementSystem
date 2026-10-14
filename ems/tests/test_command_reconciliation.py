"""#236: focused unit tests for CommandReconciliation (release / leave lifecycle).

Idempotent fence finalisation without a ControlService tick — timeout paths must not
release a live writer early, and leave_and_release must be safe when enter never ran.
"""
from __future__ import annotations

from ems.control.command_fence import BatteryCommandFence, CommandClass
from ems.control.reconciliation import CommandReconciliation


def test_release_drops_outstanding_ticket():
    fence = BatteryCommandFence()
    recon = CommandReconciliation(fence)
    ticket = fence.admit_routine()
    assert ticket is not None
    assert fence.snapshot().outstanding == 1

    recon.release(ticket)
    assert fence.empty()
    # Idempotent: second release is a no-op.
    recon.release(ticket)
    assert fence.empty()


def test_leave_and_release_when_entered_clears_active_lane():
    fence = BatteryCommandFence()
    recon = CommandReconciliation(fence)
    ticket = fence.admit_routine()
    assert ticket is not None and fence.enter(ticket) is True
    assert fence.snapshot().active_generation == ticket.generation

    recon.leave_and_release(ticket, entered=True)
    assert fence.empty()
    assert fence.snapshot().active_generation is None


def test_leave_and_release_when_not_entered_only_releases():
    """Timeout before enter: must not call leave on a ticket that never owned the lane."""
    fence = BatteryCommandFence()
    recon = CommandReconciliation(fence)
    first = fence.admit_routine()
    assert first is not None and fence.enter(first) is True

    waiting = fence.reserve(CommandClass.OVERRIDE)
    assert waiting is not None
    # waiting never entered — leave_and_release must not clear first's active lane.
    recon.leave_and_release(waiting, entered=False)
    assert fence.snapshot().active_generation == first.generation
    assert fence.snapshot().outstanding == 1

    recon.leave_and_release(first, entered=True)
    assert fence.empty()


def test_leave_and_release_after_supersede_is_safe():
    fence = BatteryCommandFence()
    recon = CommandReconciliation(fence)
    stale = fence.admit_routine()
    recovery = fence.begin_recovery(stale)
    assert stale is not None and recovery is not None
    assert fence.enter(stale) is False

    recon.leave_and_release(stale, entered=False)
    assert fence.snapshot().outstanding == 1
    assert fence.enter(recovery) is True
    recon.leave_and_release(recovery, entered=True)
    assert fence.empty()


def test_leave_and_release_idempotent_on_active_ticket():
    fence = BatteryCommandFence()
    recon = CommandReconciliation(fence)
    ticket = fence.reserve(CommandClass.OVERRIDE)
    assert ticket is not None and fence.enter(ticket) is True

    recon.leave_and_release(ticket, entered=True)
    recon.leave_and_release(ticket, entered=True)
    recon.release(ticket)
    assert fence.empty()
