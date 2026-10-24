"""Cross-store SQLite writer gate (audit F-45 / #311).

WAL still allows only one writer. History, audit, settings and auth each keep their own
connection; cache and control_state open a short connection per call. A sibling write or
``wal_checkpoint(TRUNCATE)`` / backup ``VACUUM`` can therefore outlast ``busy_timeout`` and
raise ``database is locked``.

Non-critical writers (plan history, gas, carbon, finance rollup, audit, cache, samples)
share one gate per database file, retry a lock error 3 times, and give up inside ~2 s.
That give-up must not steer the battery. ``control_state`` uses the same gate with
priority, its own connection, and the same 3 attempts; on exhaustion it records an
incident (alert + audit) and does not raise into the control tick.
"""
from __future__ import annotations

import asyncio
import logging
import sqlite3
import threading
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial

_log = logging.getLogger("ems.storage")

# 3 attempts. Sleeps between them sum to 1.2 s; each attempt also uses a short busy
# timeout (see SHORT_BUSY_MS) so the whole budget stays under ~2 s.
LOCK_ATTEMPTS = 3
BACKOFF_S: tuple[float, ...] = (0.4, 0.8)
SHORT_BUSY_MS = 200

# Stores surfaced on /api/diagnostics even before the first incident.
STORE_NAMES = ("history", "settings", "audit", "auth", "cache", "control_state")

_short_busy: ContextVar[bool] = ContextVar("ems_sqlite_short_busy", default=False)
_writer_name: ContextVar[str] = ContextVar("ems_sqlite_writer", default="store")

def is_sqlite_lock_error(exc: BaseException) -> bool:
    """True for SQLite's transient writer contention (not a dead connection)."""
    if not isinstance(exc, sqlite3.OperationalError):
        return False
    msg = str(exc).lower()
    return "locked" in msg or "busy" in msg


def connect_timeout_ms(default_ms: int) -> int:
    """Busy timeout for a connection opened inside a guarded attempt."""
    return SHORT_BUSY_MS if _short_busy.get() else default_ms


def current_writer() -> str:
    return _writer_name.get()


@asynccontextmanager
async def short_busy_connection(db, *, default_ms: int) -> AsyncIterator[None]:
    """While a guarded attempt is running, fail a contended statement quickly.

    Restoring the caller's busy timeout is best-effort: a dead connection must not
    mask the original lock error.
    """
    if not _short_busy.get():
        yield
        return
    await db.execute(f"PRAGMA busy_timeout={SHORT_BUSY_MS}")
    try:
        yield
    finally:
        try:
            await db.execute(f"PRAGMA busy_timeout={int(default_ms)}")
        except Exception:
            _log.debug("could not restore busy_timeout", exc_info=True)


class WriterGate:
    """One writer at a time per database file. Critical waiters run ahead of the rest."""

    def __init__(self) -> None:
        self._cv = threading.Condition()
        self._held = False
        self._holder: str | None = None
        self._critical_waiting = 0

    def acquire(self, writer: str, *, critical: bool = False) -> None:
        started = time.perf_counter()
        with self._cv:
            blocked_by = self._holder
            if critical:
                self._critical_waiting += 1
            try:
                while self._held or (not critical and self._critical_waiting > 0):
                    self._cv.wait()
                self._held = True
                self._holder = writer
            finally:
                if critical:
                    self._critical_waiting -= 1
        waited_ms = (time.perf_counter() - started) * 1000.0
        if waited_ms >= 50.0:
            # Diagnosis: who waited, for how long, and who held the gate. The SQLite
            # error text is intentionally absent so a clean wait is not a lock incident.
            _log.info(
                "sqlite lock wait writer=%s duration_ms=%.0f blocked_by=%s",
                writer, waited_ms, blocked_by or "-",
            )

    def release(self) -> None:
        with self._cv:
            self._held = False
            self._holder = None
            self._cv.notify_all()


_gates: dict[str, WriterGate] = {}
_gates_guard = threading.Lock()


def gate_for(db_path: str) -> WriterGate:
    with _gates_guard:
        gate = _gates.get(db_path)
        if gate is None:
            gate = WriterGate()
            _gates[db_path] = gate
        return gate


class _AsyncWriter:
    def __init__(self, db_path: str, writer: str, *, critical: bool) -> None:
        self._gate = gate_for(db_path)
        self._writer = writer
        self._critical = critical

    async def __aenter__(self) -> None:
        acquire = asyncio.create_task(asyncio.to_thread(
            partial(self._gate.acquire, self._writer, critical=self._critical)))
        try:
            await asyncio.shield(acquire)
        except asyncio.CancelledError:
            # to_thread cannot cancel a blocked Condition.wait(). Once that worker
            # acquires the gate, release it before propagating cancellation.
            await asyncio.shield(acquire)
            self._gate.release()
            raise

    async def __aexit__(self, *exc: object) -> None:
        self._gate.release()


def hold_db_writer(db_path: str, *, writer: str | None = None, critical: bool = False):
    """Async context: exclusive writer gate for ``db_path``."""
    return _AsyncWriter(db_path, writer or current_writer(), critical=critical)


@dataclass(frozen=True)
class CriticalWriteFailure:
    store: str
    writer: str
    attempts: int
    duration_ms: float
    at: str


@dataclass
class _StoreLockStats:
    retries: int = 0
    last_lock_event: dict | None = None


_stats_guard = threading.Lock()
_stats: dict[str, _StoreLockStats] = {name: _StoreLockStats() for name in STORE_NAMES}
_retry_total = 0
_warned: dict[tuple[str, str], float] = {}
_last_warn_at = 0.0
_critical: list[CriticalWriteFailure] = []
# One WARN for a burst of non-critical give-ups (several writers, 3 attempts each).
_WARN_DEDUPE_S = 2.0


def storage_write_retries() -> int:
    with _stats_guard:
        return _retry_total


def diagnostics_locks() -> dict[str, dict]:
    """Per-store ``last_lock_event`` (or null) and ``retries`` (≥ 0)."""
    with _stats_guard:
        out: dict[str, dict] = {}
        for name in STORE_NAMES:
            stats = _stats.get(name) or _StoreLockStats()
            event = stats.last_lock_event
            out[name] = {
                "last_lock_event": dict(event) if event is not None else None,
                "retries": stats.retries,
            }
        return out


def drain_critical_failures() -> list[CriticalWriteFailure]:
    with _stats_guard:
        items = list(_critical)
        _critical.clear()
        return items


def reset_lock_stats() -> None:
    """Test helper: clear counters, incidents and the deduped-warning latch."""
    global _retry_total, _last_warn_at
    with _stats_guard:
        for name in STORE_NAMES:
            _stats[name] = _StoreLockStats()
        _retry_total = 0
        _warned.clear()
        _critical.clear()
        _last_warn_at = 0.0


def _note_lock(store: str, writer: str, duration_ms: float, attempt: int) -> None:
    global _retry_total
    event = {
        "writer": writer,
        "at": datetime.now(UTC).isoformat(),
        "duration_ms": round(duration_ms, 1),
        "attempt": attempt,
    }
    with _stats_guard:
        stats = _stats.get(store)
        if stats is None:
            stats = _StoreLockStats()
            _stats[store] = stats
        stats.retries += 1
        stats.last_lock_event = event
        _retry_total += 1
    _log.info(
        "sqlite lock event store=%s writer=%s duration_ms=%.0f attempt=%s",
        store, writer, duration_ms, attempt,
    )


def _warn_give_up(store: str, writer: str, duration_ms: float) -> None:
    global _last_warn_at
    key = (store, writer)
    with _stats_guard:
        now = time.monotonic()
        if now - _warned.get(key, float("-inf")) < _WARN_DEDUPE_S:
            return
        burst = (now - _last_warn_at) < _WARN_DEDUPE_S
        _warned[key] = now
        if burst:
            return
        _last_warn_at = now
    _log.warning(
        "non-critical sqlite write skipped after %s attempts store=%s writer=%s "
        "duration_ms=%.0f",
        LOCK_ATTEMPTS, store, writer, duration_ms,
    )


def _clear_warn(store: str, writer: str) -> None:
    with _stats_guard:
        _warned.pop((store, writer), None)


def _queue_critical(store: str, writer: str, duration_ms: float) -> None:
    failure = CriticalWriteFailure(
        store=store, writer=writer, attempts=LOCK_ATTEMPTS,
        duration_ms=duration_ms, at=datetime.now(UTC).isoformat(),
    )
    with _stats_guard:
        _critical.append(failure)
    _log.warning(
        "control_state write failed after %s attempts duration_ms=%.0f; "
        "no extra battery command",
        LOCK_ATTEMPTS, duration_ms,
    )


def _backoff(attempt_index: int) -> float:
    if attempt_index < 0 or attempt_index >= len(BACKOFF_S):
        return 0.0
    return BACKOFF_S[attempt_index]


def _finish_lock(
    store: str, writer: str, duration_ms: float, *, critical: bool, swallow: bool,
    last: BaseException | None,
) -> None:
    if critical:
        _queue_critical(store, writer, duration_ms)
        return
    if not swallow:
        if last is not None:
            raise last
        raise sqlite3.OperationalError("sqlite writer busy")
    _warn_give_up(store, writer, duration_ms)


async def run_async_write[T](
    db_path: str,
    store: str,
    writer: str,
    fn: Callable[[], Awaitable[T]],
    *,
    critical: bool = False,
    swallow: bool = True,
) -> T | None:
    """Run ``fn`` up to 3 times.

    The writer gate itself is taken by the store's ``_write_conn`` (same ``db_path``).
    Non-critical exhaustion (``swallow=True``) returns None after one WARN.
    ``critical=True`` (control_state) queues an alert/audit incident and does not raise.
    ``swallow=False`` re-raises the lock error after the budget (settings).
    """
    gate_for(db_path)
    started = time.perf_counter()
    last: BaseException | None = None
    for attempt in range(LOCK_ATTEMPTS):
        busy = _short_busy.set(True)
        named = _writer_name.set(writer)
        try:
            try:
                result = await fn()
            except sqlite3.OperationalError as exc:
                if not is_sqlite_lock_error(exc):
                    raise
                last = exc
            else:
                _clear_warn(store, writer)
                return result
        finally:
            _writer_name.reset(named)
            _short_busy.reset(busy)
        duration_ms = (time.perf_counter() - started) * 1000.0
        _note_lock(store, writer, duration_ms, attempt + 1)
        if attempt + 1 >= LOCK_ATTEMPTS:
            break
        await asyncio.sleep(_backoff(attempt))
    duration_ms = (time.perf_counter() - started) * 1000.0
    _finish_lock(
        store, writer, duration_ms, critical=critical, swallow=swallow, last=last)
    return None


def run_sync_write[T](
    db_path: str,
    store: str,
    writer: str,
    fn: Callable[[], T],
    *,
    critical: bool = False,
    swallow: bool = True,
) -> T | None:
    """Sync twin of :func:`run_async_write` (cache and control_state). Holds the gate."""
    started = time.perf_counter()
    gate = gate_for(db_path)
    last: BaseException | None = None
    for attempt in range(LOCK_ATTEMPTS):
        busy = _short_busy.set(True)
        named = _writer_name.set(writer)
        gate.acquire(writer, critical=critical)
        try:
            try:
                result = fn()
            except sqlite3.OperationalError as exc:
                if not is_sqlite_lock_error(exc):
                    raise
                last = exc
            else:
                _clear_warn(store, writer)
                return result
        finally:
            gate.release()
            _writer_name.reset(named)
            _short_busy.reset(busy)
        duration_ms = (time.perf_counter() - started) * 1000.0
        _note_lock(store, writer, duration_ms, attempt + 1)
        if attempt + 1 >= LOCK_ATTEMPTS:
            break
        time.sleep(_backoff(attempt))
    duration_ms = (time.perf_counter() - started) * 1000.0
    _finish_lock(
        store, writer, duration_ms, critical=critical, swallow=swallow, last=last)
    return None


def wal_checkpoint_truncate(db_path: str) -> None:
    """``PRAGMA wal_checkpoint(TRUNCATE)`` while holding the writer gate.

    The stress test and any in-process checkpoint must use this so the checkpoint
    does not collide with a store write and surface a lock error. The gate is taken
    as a normal writer: checkpoint is maintenance, not a battery write. Sharing
    ``control_state``'s critical priority lets the two tight loops convoy, so
    samples and audit wait out the whole window behind cache, and cache waits
    behind ``wal_checkpoint`` (#379). A busy checkpoint still retries outside the gate.
    """
    def _op() -> None:
        con = sqlite3.connect(db_path, timeout=SHORT_BUSY_MS / 1000)
        try:
            con.execute(f"PRAGMA busy_timeout={SHORT_BUSY_MS}")
            row = con.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
            if row is not None and row[0]:
                raise sqlite3.OperationalError("sqlite checkpoint busy")
        finally:
            con.close()

    # Not control_state: a failed checkpoint raises (it is not a battery command).
    # critical=False: see the docstring. A critical checkpoint starves the other writers.
    started = time.perf_counter()
    gate = gate_for(db_path)
    for attempt in range(LOCK_ATTEMPTS):
        gate.acquire("wal_checkpoint", critical=False)
        try:
            try:
                _op()
            except sqlite3.OperationalError as exc:
                if not is_sqlite_lock_error(exc):
                    raise
            else:
                return
        finally:
            gate.release()
        duration_ms = (time.perf_counter() - started) * 1000.0
        _note_lock("history", "wal_checkpoint", duration_ms, attempt + 1)
        if attempt + 1 >= LOCK_ATTEMPTS:
            break
        time.sleep(_backoff(attempt))
    raise sqlite3.OperationalError("sqlite checkpoint busy")
