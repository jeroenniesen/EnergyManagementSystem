"""Synchronous ordering for battery-affecting work.

Async waiters can time out while their worker threads keep running.  This fence therefore ties
ownership to the real blocking worker lifetime and serializes the physical command boundary with a
``threading.Condition``.  The condition is never held while device I/O executes.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from enum import StrEnum


class CommandClass(StrEnum):
    ROUTINE = "routine"
    OVERRIDE = "override"
    RECOVERY = "recovery"
    SHUTDOWN = "shutdown"


@dataclass(frozen=True)
class CommandTicket:
    token: object
    generation: int
    command_class: CommandClass


@dataclass(frozen=True)
class FenceSnapshot:
    outstanding: int
    active_generation: int | None
    recovery_pending: bool


class BatteryCommandFence:
    """Generation fence plus one-at-a-time physical command ownership."""

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._generation = 0
        self._tickets: dict[object, CommandTicket] = {}
        self._superseded: set[int] = set()
        self._active: CommandTicket | None = None
        self._recoveries: dict[int, CommandTicket] = {}
        self._recovery_for_token: dict[object, int] = {}
        self._draining = False

    def _new_ticket(self, command_class: CommandClass) -> CommandTicket:
        self._generation += 1
        ticket = CommandTicket(object(), self._generation, command_class)
        self._tickets[ticket.token] = ticket
        return ticket

    def reserve(
        self, command_class: CommandClass, *, draining: bool = False,
    ) -> CommandTicket | None:
        with self._condition:
            if (self._draining or draining) and command_class is not CommandClass.SHUTDOWN:
                return None
            ticket = self._new_ticket(command_class)
            if command_class is not CommandClass.ROUTINE:
                self._superseded.update(
                    t.generation for t in self._tickets.values()
                    if t.command_class is CommandClass.ROUTINE and t is not ticket
                )
            self._condition.notify_all()
            return ticket

    def admit_routine(self, *, draining: bool = False) -> CommandTicket | None:
        with self._condition:
            if self._draining or draining or self._tickets:
                return None
            return self._new_ticket(CommandClass.ROUTINE)

    def begin_recovery(
        self, stale: CommandTicket, *, draining: bool = False,
    ) -> CommandTicket | None:
        with self._condition:
            existing = self._recoveries.get(stale.generation)
            if existing is not None and existing.token in self._tickets:
                return existing
            if self._draining or draining:
                return None
            # A newer operator/shutdown command already expresses the desired final state. Queuing
            # AUTO behind it would both delay that command and overwrite it with stale recovery.
            if any(
                t.generation > stale.generation and t.command_class is not CommandClass.ROUTINE
                for t in self._tickets.values()
            ):
                self._superseded.add(stale.generation)
                self._condition.notify_all()
                return None
            self._superseded.add(stale.generation)
            recovery = self._new_ticket(CommandClass.RECOVERY)
            self._recoveries[stale.generation] = recovery
            self._recovery_for_token[recovery.token] = stale.generation
            self._condition.notify_all()
            return recovery

    def _is_next(self, ticket: CommandTicket) -> bool:
        eligible = [
            t for t in self._tickets.values() if t.generation not in self._superseded
        ]
        return bool(eligible) and ticket.generation == min(t.generation for t in eligible)

    def enter(self, ticket: CommandTicket, *, timeout_seconds: float | None = None) -> bool:
        with self._condition:
            deadline = None if timeout_seconds is None else time.monotonic() + timeout_seconds
            while ticket.token in self._tickets:
                if ticket.generation in self._superseded and self._active is not ticket:
                    return False
                if self._active is None and self._is_next(ticket):
                    self._active = ticket
                    return True
                if deadline is None:
                    self._condition.wait()
                    continue
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._condition.wait(remaining)
            return False

    def leave(self, ticket: CommandTicket) -> None:
        with self._condition:
            if self._active is ticket:
                self._active = None
                self._condition.notify_all()

    def release(self, ticket: CommandTicket) -> None:
        with self._condition:
            if self._active is ticket:
                self._active = None
            self._tickets.pop(ticket.token, None)
            self._superseded.discard(ticket.generation)
            stale_generation = self._recovery_for_token.pop(ticket.token, None)
            if stale_generation is not None:
                self._recoveries.pop(stale_generation, None)
            self._condition.notify_all()

    def empty(self) -> bool:
        with self._condition:
            return not self._tickets

    def mark_draining(self) -> None:
        with self._condition:
            self._draining = True
            self._superseded.update(
                t.generation for t in self._tickets.values()
                if t.command_class is CommandClass.ROUTINE
            )
            self._condition.notify_all()

    def clear_draining(self) -> None:
        with self._condition:
            self._draining = False
            self._condition.notify_all()

    def snapshot(self) -> FenceSnapshot:
        with self._condition:
            return FenceSnapshot(
                outstanding=len(self._tickets),
                active_generation=(self._active.generation if self._active else None),
                recovery_pending=bool(self._recoveries),
            )
