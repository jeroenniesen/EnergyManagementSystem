"""Performance timing primitives and budgets.

Pure module — no I/O, no logging side effects beyond optional WARN emission
on over-budget API/control events (callers do the logging themselves; the
sample just records the `over_budget` flag for the registry).

Public API:
    PERF_BUDGETS    — name → threshold_ms (or bytes for memory.rss.peak)
    REGISTRY        — singleton Registry instance
    Sample, Registry, timed, atimed, build_perf_block, RssSampler
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, field

# Unit budgets, in ms (bytes for memory.rss.peak).
PERF_BUDGETS: dict[str, float] = {
    # API tiers
    "api.hot": 500,
    "api.interactive": 1_000,
    "api.batch": 8_000,
    # Control loop
    "control.cycle": 20_000,
    # Per-phase push points inside control_tick (for phase attribution)
    "control.sense": 5_000,
    "control.decide": 5_000,
    "control.write": 15_000,
    "control.audit": 2_000,
    # Stores
    "store.history.read": 100,
    "store.history.write": 500,
    "store.settings.read": 50,
    "store.settings.write": 200,
    "store.audit.append": 200,
    "store.cache.get": 5,
    "store.cache.set": 5,
    "store.control_state.read": 50,
    "store.control_state.write": 200,
    # Replay / reporting
    "replay.run": 30_000,
    "report.build": 30_000,
    # RSS ceiling (bytes)
    "memory.rss.peak": 350 * 1024 * 1024,
}

# Internal ring-buffer depth per metric name.
_MAX_SAMPLES = 1_000
# Last-overrun ring depth (used by build_perf_block).
_LAST_OVERRUN_KEEP = 5


@dataclass(frozen=True)
class Sample:
    name: str
    duration_ms: float
    ts: float
    over_budget: bool


@dataclass
class _Buffer:
    samples: list[Sample] = field(default_factory=list)

    def push(self, sample: Sample) -> None:
        self.samples.append(sample)
        if len(self.samples) > _MAX_SAMPLES:
            del self.samples[: len(self.samples) - _MAX_SAMPLES]


class Registry:
    """Singleton ring-buffer of timing samples."""

    def __init__(self) -> None:
        self._buffers: dict[str, _Buffer] = {}
        self._overruns: list[dict] = []  # heterogeneous last-overrun ring

    def push(self, name: str, duration_ms: float, ts: float | None = None,
             extra: dict | None = None) -> Sample:
        budget = PERF_BUDGETS.get(name)
        over = bool(budget is not None and duration_ms > budget)
        sample = Sample(
            name=name,
            duration_ms=float(duration_ms),
            ts=ts if ts is not None else time.time(),
            over_budget=over,
        )
        self._buffers.setdefault(name, _Buffer()).push(sample)
        if over:
            entry: dict = {"ts": sample.ts, "name": name, "duration_ms": sample.duration_ms}
            if extra:
                entry.update(extra)
            self._overruns.append(entry)
            if len(self._overruns) > _LAST_OVERRUN_KEEP:
                del self._overruns[: len(self._overruns) - _LAST_OVERRUN_KEEP]
        return sample

    def recent(self, name: str, n: int = 100) -> list[Sample]:
        buf = self._buffers.get(name)
        if not buf:
            return []
        return list(buf.samples[-n:])

    def summarize(self, name: str) -> dict:
        samples = self.recent(name, n=_MAX_SAMPLES)
        if not samples:
            return {
                "p50_ms": 0.0, "p95_ms": 0.0, "p99_ms": 0.0, "max_ms": 0.0,
                "n": 0, "over_budget_count": 0,
            }
        durations = sorted(s.duration_ms for s in samples)
        n = len(durations)
        return {
            "p50_ms": _pct(durations, 0.50),
            "p95_ms": _pct(durations, 0.95),
            "p99_ms": _pct(durations, 0.99),
            "max_ms": durations[-1],
            "n": n,
            "over_budget_count": sum(1 for s in samples if s.over_budget),
        }

    def last_overruns(self) -> list[dict]:
        return list(self._overruns)

    def reset(self) -> None:
        """Wipe all buffers. Tests only."""
        self._buffers.clear()
        self._overruns.clear()


def _pct(sorted_values: list[float], p: float) -> float:
    if not sorted_values:
        return 0.0
    k = max(0, min(len(sorted_values) - 1, int(round(p * (len(sorted_values) - 1)))))
    return sorted_values[k]


REGISTRY = Registry()


@contextmanager
def timed(name: str) -> Iterator[None]:
    """Sync context manager. Use for sync code paths (CacheStore).

    Example:
        with timed("store.cache.get"):
            return self._conn().execute(...)
    """
    t0 = time.perf_counter()
    try:
        yield
    finally:
        REGISTRY.push(name, (time.perf_counter() - t0) * 1000)


@asynccontextmanager
async def atimed(name: str) -> AsyncIterator[None]:
    """Async context manager. Use for async code paths (history store, control tick).

    Example:
        async with atimed("store.history.read"):
            return await self._conn.execute(...)
    """
    t0 = time.perf_counter()
    try:
        yield
    finally:
        REGISTRY.push(name, (time.perf_counter() - t0) * 1000)


def build_perf_block() -> dict:
    """Shape consumed by /api/diagnostics.perf (spec §4.6)."""
    api_hot = REGISTRY.summarize("api.hot")
    api_interactive = REGISTRY.summarize("api.interactive")
    api_batch = REGISTRY.summarize("api.batch")
    return {
        "budgets": dict(PERF_BUDGETS),
        "tiers": {
            "hot": api_hot,
            "interactive": api_interactive,
            "batch": api_batch,
        },
        "control_cycle": {
            **REGISTRY.summarize("control.cycle"),
            "last_overrun_at": (
                REGISTRY.last_overruns()[-1]["ts"]
                if any(o["name"] == "control.cycle" for o in REGISTRY.last_overruns())
                else None
            ),
        },
        "rss_mb": _rss_state(),
        "last_overruns": REGISTRY.last_overruns(),
    }


# ---------------------------------------------------------------------------
# RSS sampler
# ---------------------------------------------------------------------------


def _read_rss_bytes() -> int | None:
    """Return process RSS in bytes, or None on platforms that don't expose it."""
    try:
        import resource  # POSIX
        usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        # macOS reports bytes; Linux reports kilobytes. Detect via platform.
        if usage > 10 * 1024 * 1024:
            return int(usage)  # bytes (macOS)
        return int(usage) * 1024  # kilobytes (Linux)
    except ImportError:
        return None


def _rss_state() -> dict:
    """Read RSS state from the sampler's shared state."""
    state = _RSS_STATE
    return {
        "current_mb": round(state["current"] / (1024 * 1024), 1) if state["current"] else 0.0,
        "peak_mb": round(state["peak"] / (1024 * 1024), 1) if state["peak"] else 0.0,
        "over_ceiling_count": state["over_count"],
    }


# Shared mutable state for the RSS sampler; intentionally module-global so
# build_perf_block() can read it without holding a reference to the task.
_RSS_STATE: dict = {"current": 0, "peak": 0, "over_count": 0}


class RssSampler:
    """Background task that samples process RSS every `interval_seconds`.

    Usage:
        sampler = RssSampler(interval_seconds=60.0)
        await sampler.start()
        # ...later, in lifespan finally:
        await sampler.stop()
    """

    def __init__(self, interval_seconds: float = 60.0) -> None:
        self._interval = interval_seconds
        self._stop = asyncio.Event()
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="rss-sampler")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._stop.set()
        try:
            await asyncio.wait_for(self._task, timeout=2.0)
        except TimeoutError:
            self._task.cancel()
        self._task = None

    def current_mb(self) -> float:
        return round(_RSS_STATE["current"] / (1024 * 1024), 1)

    def peak_mb(self) -> float:
        return round(_RSS_STATE["peak"] / (1024 * 1024), 1)

    def over_ceiling_count(self) -> int:
        return _RSS_STATE["over_count"]

    async def _run(self) -> None:
        # Take one sample immediately so /api/diagnostics has data on first request.
        self._sample_once()
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._interval)
                return  # stop was set
            except TimeoutError:
                self._sample_once()

    def _sample_once(self) -> None:
        rss = _read_rss_bytes()
        if rss is None:
            return
        _RSS_STATE["current"] = rss
        if rss > _RSS_STATE["peak"]:
            _RSS_STATE["peak"] = rss
        if rss > PERF_BUDGETS["memory.rss.peak"]:
            _RSS_STATE["over_count"] += 1
