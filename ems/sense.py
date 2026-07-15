"""The sense loop / recorder (SPEC §5.3 sense step). Each cycle it reads the source,
reconstructs house load (§4), records raw+derived to the store (§4.3), and marks per-signal
freshness (§4.7). Read-only and fail-safe: a bad read never kills the loop."""
from __future__ import annotations

import asyncio
import dataclasses
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from ems.domain import RawSample
from ems.freshness import FreshnessTracker
from ems.load_model import is_soc_jump_implausible, reconstruct, sanitize_sample
from ems.retrospect import _floor
from ems.sources.base import Source
from ems.storage.history import HistoryStore

_log = logging.getLogger("ems.recorder")

# Per-signal names tracked for freshness (SPEC §4.7).
SIGNALS = ("grid", "solar", "ev", "battery", "soc")

# Prediction-ledger throttle (design §4.2): append the CURRENT solar forecast to the ledger with
# its true `issued_at` at most once per this interval (in-instance timestamp) — enough to preserve
# real issue-time provenance for nowcast lead-times without letting a 5-min sense cadence bloat it.
_LEDGER_MIN_INTERVAL = timedelta(minutes=30)


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
        carbon_source=None,  # optional CarbonSource (F3); persisted for Insights CO2 reporting —
        # read-only, never feeds the planner (CLAUDE.md: reporting, not carbon-aware control).
    ) -> None:
        self.source = source
        self.store = store
        self.freshness = freshness
        self.cycle_seconds = cycle_seconds
        self._clock = clock
        self.price_source = price_source
        self.solar_forecast = solar_forecast
        self.carbon_source = carbon_source
        # Settable by create_app (not a constructor param — the plan snapshot closure needs the
        # web layer's live settings/strategy/plan machinery, which doesn't exist until the app is
        # built). None means plan/target history logging is off. See `_persist_plan`.
        self.plan_provider: Callable[[datetime], dict | None] | None = None
        # Health, surfaced on /api/diagnostics so a 24/7 operator can SEE a stuck recorder (full
        # disk, DB lock, permanently failing device) instead of only inferring it from stale data.
        self.last_success_at: datetime | None = None
        self.last_error: str | None = None
        self.consecutive_failures = 0
        # Counts readings clamped by the plausibility guard (defense-in-depth against a future
        # sensor/comms glitch) — surfaced on /api/diagnostics so an operator can SEE it happening.
        self.clamped_samples = 0
        # SoC-jump guard (SPEC §4.7): the last SoC we ACCEPTED and when. A physically impossible
        # jump (>rate) is rejected — held at last-good, not marked fresh — so a comms glitch never
        # reaches the planner as a trusted value. `rejected_soc_samples` is surfaced for visibility.
        self._prev_soc: float | None = None
        self._prev_soc_at: datetime | None = None
        self.rejected_soc_samples = 0
        # In-instance throttle for the prediction-ledger nowcast append (see _LEDGER_MIN_INTERVAL).
        # Not persisted: a restart simply writes one extra ledger row, which is harmless.
        self._last_ledger_write_at: datetime | None = None

    def health(self) -> dict:
        return {
            "last_success_at": self.last_success_at.isoformat() if self.last_success_at else None,
            "consecutive_failures": self.consecutive_failures,
            "last_error": self.last_error,
            "clamped_samples": self.clamped_samples,
            "rejected_soc_samples": self.rejected_soc_samples,
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
        raw, clamped = sanitize_sample(raw)
        if clamped:
            self.clamped_samples += 1
            if self.clamped_samples == 1 or self.clamped_samples % 12 == 0:
                _log.warning("implausible reading clamped (%d so far): %s",
                             self.clamped_samples, ", ".join(clamped))
        # SoC-jump plausibility guard (SPEC §4.7): only when SoC was actually read this cycle (a
        # source that didn't report it leaves "soc" out of `fresh` — never seed the guard from a
        # non-fresh 0.0). An impossible jump is rejected: not marked fresh (so it ages to STALE and
        # the planner fails safe), counted, and held at the last-good value in the recorded sample.
        # The accepted value + its time only advance on a plausible reading, so a genuine-but-large
        # drift is eventually admitted as the allowed window grows with elapsed time.
        if "soc" in fresh:
            elapsed_min = ((now - self._prev_soc_at).total_seconds() / 60.0
                           if self._prev_soc_at is not None else 0.0)
            if is_soc_jump_implausible(self._prev_soc, raw.soc_pct, elapsed_min):
                self.rejected_soc_samples += 1
                if self.rejected_soc_samples == 1 or self.rejected_soc_samples % 12 == 0:
                    _log.warning("implausible SoC jump rejected (%d so far): %.1f%% → %.1f%% in "
                                 "%.1f min — holding last-good", self.rejected_soc_samples,
                                 self._prev_soc if self._prev_soc is not None else float("nan"),
                                 raw.soc_pct, elapsed_min)
                fresh = fresh - {"soc"}
                if self._prev_soc is not None:
                    raw = dataclasses.replace(raw, soc_pct=self._prev_soc)
            else:
                self._prev_soc = raw.soc_pct
                self._prev_soc_at = now
        derived = reconstruct(raw)
        await self.store.record(now.isoformat(), raw, derived)
        for sig in fresh:
            self.freshness.mark(sig, now)
        await self._persist_prices()
        await self._persist_forecast(now)
        await self._persist_plan(now)
        await self._persist_gas(now, raw)
        await self._persist_carbon(now)

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
        """Append the current solar forecast to the exact-provenance prediction ledger (design
        §4.2) with the TRUE `issued_at = now` (canonical=0 nowcast), throttled to
        `_LEDGER_MIN_INTERVAL` — enough to preserve real issue-time provenance for nowcast
        lead-times without letting a 5-min sense cadence bloat the ledger. Best-effort: a
        forecast-source or store failure must never kill the sense cycle.

        RETIRED (reconciliation iteration, design §3.3): this used to ALSO upsert the legacy
        date-keyed `forecast_snapshots` table every cycle. Every solar-accuracy reader now scores
        the ledger's canonical rows exclusively (see `ems.analysis.forecast_error`), so that write
        is gone — `forecast_snapshots` is retained ONLY as a read-only historic/migration-source
        table (`ems.storage.history.HistoryStore.upsert_forecast_snapshot`/`forecasts_between` are
        deprecated, not deleted) and is no longer written by anything."""
        if self.solar_forecast is None:
            return
        try:
            slots = await asyncio.to_thread(self.solar_forecast.slots)
            if (self._last_ledger_write_at is None
                    or (now - self._last_ledger_write_at) >= _LEDGER_MIN_INTERVAL):
                issued_at = now.astimezone(UTC).isoformat()
                source = type(self.solar_forecast).__name__
                await self.store.ledger_append([
                    (issued_at, "solar", s.start.astimezone(UTC).isoformat(),
                     float(s.p10_w), float(s.p50_w), float(s.p90_w), source, None, None, 0)
                    for s in slots
                ])
                self._last_ledger_write_at = now
        except Exception as exc:
            _log.warning("forecast persist failed (non-fatal): %s: %s", type(exc).__name__, exc)

    async def _persist_gas(self, now: datetime, raw: RawSample) -> None:
        """Record this cycle's cumulative gas meter reading (B-02: gas folds into the CO2
        footprint), when the sample carries one — a household with no paired gas meter reports
        None and nothing is written. Best-effort: a store failure must never kill the cycle."""
        if raw.total_gas_m3 is None:
            return
        try:
            await self.store.record_gas(now.astimezone(UTC).isoformat(), float(raw.total_gas_m3))
        except Exception as exc:
            _log.warning("gas persist failed (non-fatal): %s: %s", type(exc).__name__, exc)

    async def _persist_carbon(self, now: datetime) -> None:
        """Roadmap F3: upsert the current grid CO2 intensity into the CURRENT 15-min slot (floor
        of `now`), mirroring `_persist_prices`. Read-only reporting signal — never feeds the
        planner. Best-effort: a missing source, a None reading (fetch failed with no last-good
        yet), or a store failure must never kill the sense cycle."""
        if self.carbon_source is None:
            return
        try:
            value = await self.carbon_source.current_intensity()
            if value is not None:
                slot = _floor(now.astimezone(UTC))
                await self.store.upsert_carbon([(slot.isoformat(), float(value))])
        except Exception as exc:
            _log.warning("carbon persist failed (non-fatal): %s: %s", type(exc).__name__, exc)

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
