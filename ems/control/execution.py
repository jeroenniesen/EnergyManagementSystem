"""Physical command execution boundary for the control loop.

The boundary deliberately contains no planning policy.  It owns only admission to the
single battery writer and delegates the actual decision to the existing controller.
"""
from __future__ import annotations

import threading
from typing import Any

from ems.control.command_fence import BatteryCommandFence, CommandTicket
from ems.domain import PhysicalMode


class CommandExecutionBoundary:
    """Fence-aware adapter around the controller's physical command methods."""

    def __init__(self, controller: Any, fence: BatteryCommandFence) -> None:
        self.controller = controller
        self.fence = fence
        self.writer_local = threading.local()

    def decide(self, *args: Any, **kwargs: Any) -> Any:
        """Enter the generation fence immediately before a physical decision/write."""
        ticket = getattr(self.writer_local, "ticket", None)
        if ticket is not None and not getattr(self.writer_local, "entered", False):
            from ems.control.service import _CommandSuperseded
            if not self.fence.enter(ticket):
                raise _CommandSuperseded
            self.writer_local.entered = True
        return self.controller.decide(*args, **kwargs)

    def apply(self, mode: PhysicalMode) -> bool:
        """Apply an already-admitted command through the single battery writer."""
        return bool(self.controller.driver.apply(mode))
