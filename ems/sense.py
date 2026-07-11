"""The sense loop / recorder (SPEC §5.3 sense step). Each cycle it reads the source,
reconstructs house load (§4), records raw+derived to the store (§4.3), and marks per-signal
freshness (§4.7). Read-only and fail-safe: a bad read never kills the loop."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime

from ems.freshness import FreshnessTracker
from ems.load_model import reconstruct
from ems.sources.base import Source
from ems.storage.history import HistoryStore

_log = logging.getLogger("ems.recorder")

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
        price_source=None,  # optional .slots() provider; persisted for finance (spec 2026-07-03)
        solar_forecast=None,  # optional .slots() provider; persisted for forecast-error analysis
    ) -> None:
        self.source = source
        self.store = store
        self.freshness = freshness
        self.cycle_seconds = cycle_seconds
        self._clock = clock
        self.price_source = price_source
        self.solar_forecast = solar_forecast
        # Settable by create_app (not a constructor param — the plan snapshot closure needs the
        # web layer's live settings/strategy/plan machinery, which doesn't exist until the app is
        # built). None means plan/target history logging is off. See `_persist_plan`.
        self.plan_provider: Callable[[datetime], dict | None] | None = None
        # Health, surfaced on /api/diagnostics so a 24/7 operator can SEE a stuck recorder (full
        # disk, DB lock, permanently failing device) instead of only inferring it from stale data.
        self.last_success_at: datetime | None = None
        self.last_error: str | None = None
        self.consecutive_failures = 0

    def health(self) -> dict:
        return {
            "last_success_at": self.last_success_at.isoformat() if self.last_success_at else None,
            "consecutive_failures": self.consecutive_failures,
            "last_error": self.last_error,
        }

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
        await self._persist_prices()
        await self._persist_forecast(now)
        await self._persist_plan(now)

    async def _persist_prices(self) -> None:
        """Upsert the current price curve so PAST slots keep the price that was active then —
        the live feed only carries today/tomorrow (finance + best-price history, spec 2026-07-03).
        Best-effort: prices come from the source's cache, but a failure (feed down, DB busy) must
        never kill the sense cycle; the gap just lowers that day's price_coverage."""
        if self.price_source is None:
            return
        try:
            slots = await asyncio.to_thread(self.price_source.slots)
            await self.store.upsert_price_slots(
                [(p.start.astimezone(UTC).isoformat(), float(p.eur_per_kwh)) for p in slots])
        except Exception as exc:
            _log.warning("price persist failed (non-fatal): %s: %s", type(exc).__name__, exc)

    async def _persist_forecast(self, now: datetime) -> None:
        """Snapshot today's day-ahead solar forecast so it can later be compared against actual
        production (forecast-vs-actual error, observability-data). The store's INSERT OR IGNORE
        keeps the FIRST forecast recorded per (issued_date, slot) — later cycles the same day are
        no-ops. Best-effort: a forecast-source failure must never kill the sense cycle."""
        if self.solar_forecast is None:
            return
        try:
            issued = now.astimezone(UTC).date().isoformat()
            slots = await asyncio.to_thread(self.solar_forecast.slots)
            await self.store.upsert_forecast_snapshot(
                issued,
                [(s.start.astimezone(UTC).isoformat(), float(s.p10_w), float(s.p50_w),
                  float(s.p90_w)) for s in slots],
            )
        except Exception as exc:
            _log.warning("forecast persist failed (non-fatal): %s: %s", type(exc).__name__, exc)

    async def _persist_plan(self, now: datetime) -> None:
        """Snapshot the planner's current target/strategy/intent (observability-data) so a
        reviewer can later compare `target_soc` against the achieved `soc_pct` in raw_samples.
        `plan_provider` (wired by create_app) does synchronous, non-trivial work (rebuilds the
        plan) — offload to a thread like the other source reads. Best-effort: a provider failure
        (or one returning None, e.g. no plan yet) must never kill the sense cycle."""
        if self.plan_provider is None:
            return
        try:
            snap = await asyncio.to_thread(self.plan_provider, now)
            if snap:
                await self.store.record_plan(now.astimezone(UTC).isoformat(), snap)
        except Exception as exc:
            _log.warning("plan persist failed (non-fatal): %s: %s", type(exc).__name__, exc)

    async def record_now(self) -> None:
        await self.sense_once(self._clock())
        self.last_success_at = self._clock()
        self.consecutive_failures = 0
        self.last_error = None

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
            except Exception as exc:
                # Fail-safe: a transient source/store error must not kill the recorder. The
                # affected signal ages into STALE (SPEC §4.6/§4.7) and we retry next cycle — but
                # TRACK the failure so a persistent problem (full disk, DB lock) is visible on
                # /api/diagnostics, and log it (throttled: first failure + every 12th ~hourly).
                self.consecutive_failures += 1
                self.last_error = f"{type(exc).__name__}: {exc}"
                if self.consecutive_failures == 1 or self.consecutive_failures % 12 == 0:
                    _log.warning("recorder cycle failed (%d in a row): %s",
                                 self.consecutive_failures, self.last_error)
