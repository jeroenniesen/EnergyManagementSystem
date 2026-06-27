"""The sense loop / recorder (SPEC §5.3 sense step). Each cycle it reads the source,
reconstructs house load (§4), records raw+derived to the store (§4.3), and marks per-signal
freshness (§4.7). Read-only and fail-safe: a bad read never kills the loop."""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime

from ems.freshness import FreshnessTracker
from ems.load_model import reconstruct
from ems.sources.base import Source
from ems.storage.history import HistoryStore

# Per-signal names tracked for freshness (SPEC §4.7).
SIGNALS = ("grid", "solar", "ev", "battery", "soc")


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Recorder:
    def __init__(
        self,
        source: Source,
        store: HistoryStore,
        freshness: FreshnessTracker,
        cycle_seconds: float = 300.0,
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        self.source = source
        self.store = store
        self.freshness = freshness
        self.cycle_seconds = cycle_seconds
        self._clock = clock

    async def sense_once(self, now: datetime) -> None:
        # Offload the source read to a thread: live sources (HomeWizard/Tibber/Indevolt) do
        # blocking network I/O and must not stall the event loop (SPEC §5.3).
        # A source may implement read_sample() -> (RawSample, fresh signals) to report partial
        # availability; otherwise read() is treated as all-fresh (e.g. the deterministic mock).
        read_sample = getattr(self.source, "read_sample", None)
        if read_sample is not None:
            raw, fresh = await asyncio.to_thread(read_sample)
        else:
            raw = await asyncio.to_thread(self.source.read)
            fresh = set(SIGNALS)
        derived = reconstruct(raw)
        await self.store.record(now.isoformat(), raw, derived)
        for sig in fresh:
            self.freshness.mark(sig, now)

    async def record_now(self) -> None:
        await self.sense_once(self._clock())

    async def run(self, stop: asyncio.Event) -> None:
        """Periodic loop: wait `cycle_seconds` (or until `stop`), then record. The startup
        sample is taken separately (see the app lifespan) so readiness is deterministic."""
        while True:
            try:
                await asyncio.wait_for(stop.wait(), timeout=self.cycle_seconds)
                return  # stop requested
            except TimeoutError:
                pass  # cycle elapsed
            try:
                await self.record_now()
            except Exception:
                # Fail-safe: a transient source/store error must not kill the recorder.
                # The affected signal simply ages into STALE (SPEC §4.6/§4.7); we retry next cycle.
                pass
