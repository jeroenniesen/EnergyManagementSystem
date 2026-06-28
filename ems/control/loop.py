"""The live control loop (SPEC §5.3 act step) — runs ONLY in operational mode.

Each cycle it calls a `tick(now)` callback that advances the ownership lifecycle and, once
CONTROLLING, asks the ModeController to apply the current intent (the single battery write).
Fail-safe: a tick error is logged and never kills the loop. In dry-run this loop is not started at
all — the dashboard still previews via the read-only endpoints.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime

_log = logging.getLogger("ems.control.loop")


def _utcnow() -> datetime:
    return datetime.now(UTC)


class ControlLoop:
    def __init__(
        self,
        tick: Callable[[datetime], None],
        cycle_seconds: float = 300.0,
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        self._tick = tick
        self.cycle_seconds = cycle_seconds
        self._clock = clock

    async def run(self, stop: asyncio.Event) -> None:
        while True:
            try:
                await asyncio.wait_for(stop.wait(), timeout=self.cycle_seconds)
                return  # stop requested
            except TimeoutError:
                pass  # cycle elapsed
            try:
                # tick may do blocking network I/O (read-back, SetData) — keep it off the loop.
                await asyncio.to_thread(self._tick, self._clock())
            except Exception:
                _log.exception("control tick failed; will retry next cycle (fail-safe)")
