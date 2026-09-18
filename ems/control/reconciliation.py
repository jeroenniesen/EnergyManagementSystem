"""Small boundary for command lifecycle reconciliation.

Keeping release/finalisation in one object makes it harder for timeout paths to release a
live writer early.  The operations are intentionally idempotent.
"""
from __future__ import annotations

from ems.control.command_fence import BatteryCommandFence, CommandTicket


class CommandReconciliation:
    def __init__(self, fence: BatteryCommandFence) -> None:
        self.fence = fence

    def release(self, ticket: CommandTicket) -> None:
        self.fence.release(ticket)

    def leave_and_release(self, ticket: CommandTicket, entered: bool) -> None:
        if entered:
            self.fence.leave(ticket)
        self.release(ticket)

