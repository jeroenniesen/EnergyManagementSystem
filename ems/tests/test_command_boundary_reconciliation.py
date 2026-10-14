"""Focused unit tests for CommandExecutionBoundary and CommandReconciliation."""

from __future__ import annotations

import threading

import pytest

from ems.control.command_fence import BatteryCommandFence, CommandClass
from ems.control.execution import CommandExecutionBoundary
from ems.control.mode_controller import ActionDecision
from ems.control.reconciliation import CommandReconciliation
from ems.control.service import _CommandSuperseded
from ems.domain import BatteryIntent, PhysicalMode


class _RecordingDriver:
    def __init__(self) -> None:
        self.modes: list[PhysicalMode] = []

    def apply(self, mode: PhysicalMode) -> bool:
        self.modes.append(mode)
        return mode is PhysicalMode.CHARGE


class _StubController:
    driver = _RecordingDriver()
    decide_calls = 0

    def decide(self, *args, **kwargs) -> ActionDecision:
        type(self).decide_calls += 1
        return ActionDecision(
            BatteryIntent.ALLOW_SELF_CONSUMPTION,
            PhysicalMode.AUTO,
            False,
            "dry_run",
            "test",
        )


@pytest.fixture
def fence() -> BatteryCommandFence:
    return BatteryCommandFence()


@pytest.fixture
def controller() -> _StubController:
    _StubController.decide_calls = 0
    return _StubController()


def test_boundary_apply_delegates_to_single_writer(
    controller: _StubController, fence: BatteryCommandFence,
) -> None:
    boundary = CommandExecutionBoundary(controller, fence)
    assert boundary.apply(PhysicalMode.CHARGE) is True
    assert boundary.apply(PhysicalMode.AUTO) is False
    assert controller.driver.modes == [PhysicalMode.CHARGE, PhysicalMode.AUTO]


def test_boundary_decide_without_ticket_skips_fence(
    controller: _StubController, fence: BatteryCommandFence,
) -> None:
    boundary = CommandExecutionBoundary(controller, fence)
    boundary.writer_local = threading.local()
    decision = boundary.decide()
    assert decision.reason == "test"
    assert controller.decide_calls == 1
    assert getattr(boundary.writer_local, "entered", False) is False


def test_boundary_decide_enters_fence_before_controller(
    controller: _StubController, fence: BatteryCommandFence,
) -> None:
    ticket = fence.admit_routine()
    assert ticket is not None
    boundary = CommandExecutionBoundary(controller, fence)
    boundary.writer_local = threading.local()
    boundary.writer_local.ticket = ticket
    boundary.writer_local.entered = False

    boundary.decide()

    assert controller.decide_calls == 1
    assert boundary.writer_local.entered is True
    assert fence.snapshot().active_generation == ticket.generation
    fence.leave(ticket)
    fence.release(ticket)


def test_boundary_decide_raises_superseded_when_generation_lost(
    controller: _StubController, fence: BatteryCommandFence,
) -> None:
    stale = fence.admit_routine()
    assert stale is not None
    recovery = fence.begin_recovery(stale)
    assert recovery is not None

    boundary = CommandExecutionBoundary(controller, fence)
    boundary.writer_local = threading.local()
    boundary.writer_local.ticket = stale
    boundary.writer_local.entered = False

    with pytest.raises(_CommandSuperseded):
        boundary.decide()
    assert controller.decide_calls == 0

    fence.release(stale)
    fence.enter(recovery)
    fence.leave(recovery)
    fence.release(recovery)


def test_boundary_decide_does_not_reenter_when_already_inside(
    controller: _StubController, fence: BatteryCommandFence,
) -> None:
    ticket = fence.admit_routine()
    assert ticket is not None and fence.enter(ticket) is True

    boundary = CommandExecutionBoundary(controller, fence)
    boundary.writer_local = threading.local()
    boundary.writer_local.ticket = ticket
    boundary.writer_local.entered = True

    boundary.decide()
    assert controller.decide_calls == 1
    assert fence.snapshot().active_generation == ticket.generation

    fence.leave(ticket)
    fence.release(ticket)


def test_reconciliation_release_clears_outstanding_ticket(fence: BatteryCommandFence) -> None:
    ticket = fence.reserve(CommandClass.OVERRIDE)
    assert ticket is not None
    reconciliation = CommandReconciliation(fence)

    reconciliation.release(ticket)
    assert fence.empty()


def test_reconciliation_leave_and_release_when_entered(fence: BatteryCommandFence) -> None:
    ticket = fence.reserve(CommandClass.OVERRIDE)
    assert ticket is not None and fence.enter(ticket) is True
    reconciliation = CommandReconciliation(fence)

    reconciliation.leave_and_release(ticket, entered=True)

    assert fence.empty()
    assert fence.snapshot().active_generation is None


def test_reconciliation_leave_and_release_without_enter_skips_leave(
    fence: BatteryCommandFence,
) -> None:
    stale = fence.admit_routine()
    assert stale is not None
    recovery = fence.begin_recovery(stale)
    assert recovery is not None
    assert fence.enter(stale) is False

    reconciliation = CommandReconciliation(fence)
    reconciliation.leave_and_release(stale, entered=False)

    assert fence.enter(recovery) is True
    fence.leave(recovery)
    fence.release(recovery)
    fence.release(stale)
