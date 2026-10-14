"""#236: focused unit tests for CommandExecutionBoundary (write-admission path).

Covers fence enter / supersede / already-entered without a full ControlService tick.
"""
from __future__ import annotations

import pytest

from ems.control.command_fence import BatteryCommandFence, CommandClass
from ems.control.execution import CommandExecutionBoundary
from ems.control.mode_controller import ActionDecision
from ems.control.service import _CommandSuperseded
from ems.domain import BatteryIntent, PhysicalMode


class _Driver:
    def __init__(self) -> None:
        self.modes: list[PhysicalMode] = []

    def apply(self, mode: PhysicalMode) -> bool:
        self.modes.append(mode)
        return mode is not PhysicalMode.IDLE


class _Controller:
    def __init__(self) -> None:
        self.driver = _Driver()
        self.decide_calls = 0

    def decide(self, *args, **kwargs) -> ActionDecision:
        self.decide_calls += 1
        return ActionDecision(
            BatteryIntent.ALLOW_SELF_CONSUMPTION,
            PhysicalMode.AUTO,
            False,
            "dry_run",
            "test",
        )


def test_decide_without_ticket_calls_controller_directly():
    fence = BatteryCommandFence()
    controller = _Controller()
    boundary = CommandExecutionBoundary(controller, fence)

    dec = boundary.decide(BatteryIntent.ALLOW_SELF_CONSUMPTION)
    assert dec.desired_mode is PhysicalMode.AUTO
    assert controller.decide_calls == 1
    assert fence.snapshot().outstanding == 0
    # decide() must not write the battery itself — only ModeController / apply may.
    assert controller.driver.modes == []


def test_decide_enters_fence_once_then_delegates():
    fence = BatteryCommandFence()
    ticket = fence.admit_routine()
    assert ticket is not None
    controller = _Controller()
    boundary = CommandExecutionBoundary(controller, fence)
    boundary.writer_local.ticket = ticket
    boundary.writer_local.entered = False

    boundary.decide()
    assert boundary.writer_local.entered is True
    assert fence.snapshot().active_generation == ticket.generation
    assert controller.decide_calls == 1
    assert controller.driver.modes == []

    # Second decide on the same worker must not re-enter / re-block.
    boundary.decide()
    assert controller.decide_calls == 2
    assert fence.snapshot().active_generation == ticket.generation
    assert controller.driver.modes == []

    fence.leave(ticket)
    fence.release(ticket)


def test_decide_raises_when_ticket_superseded_before_enter():
    fence = BatteryCommandFence()
    stale = fence.admit_routine()
    assert stale is not None
    recovery = fence.begin_recovery(stale)
    assert recovery is not None

    controller = _Controller()
    boundary = CommandExecutionBoundary(controller, fence)
    boundary.writer_local.ticket = stale
    boundary.writer_local.entered = False

    with pytest.raises(_CommandSuperseded):
        boundary.decide()
    assert controller.decide_calls == 0
    assert getattr(boundary.writer_local, "entered", False) is False
    assert controller.driver.modes == []

    fence.release(stale)
    fence.release(recovery)


def test_decide_raises_when_override_supersedes_routine():
    fence = BatteryCommandFence()
    routine = fence.admit_routine()
    override = fence.reserve(CommandClass.OVERRIDE)
    assert routine is not None and override is not None

    controller = _Controller()
    boundary = CommandExecutionBoundary(controller, fence)
    boundary.writer_local.ticket = routine
    boundary.writer_local.entered = False

    with pytest.raises(_CommandSuperseded):
        boundary.decide()
    assert controller.decide_calls == 0
    assert controller.driver.modes == []

    # Override still owns the lane.
    boundary.writer_local.ticket = override
    boundary.writer_local.entered = False
    boundary.decide()
    assert controller.decide_calls == 1
    assert fence.snapshot().active_generation == override.generation
    assert controller.driver.modes == []

    fence.leave(override)
    fence.release(routine)
    fence.release(override)


def test_apply_routes_through_single_driver_writer():
    fence = BatteryCommandFence()
    controller = _Controller()
    boundary = CommandExecutionBoundary(controller, fence)

    assert boundary.apply(PhysicalMode.AUTO) is True
    assert boundary.apply(PhysicalMode.IDLE) is False
    assert controller.driver.modes == [PhysicalMode.AUTO, PhysicalMode.IDLE]
