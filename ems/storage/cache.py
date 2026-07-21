"""A tiny TTL'd key→value cache in the shared SQLite DB.

Purpose (SPEC §ops / CLAUDE.md "fail safe, don't overuse APIs"): survive restarts so we don't
re-hit rate-limited external APIs (Tibber, Forecast.Solar) or re-spend LLM tokens re-explaining a
decision that hasn't changed. Deliberately **sync** (plain sqlite3) so both the async web layer
(via ``asyncio.to_thread``) and the sync data sources can share one mechanism.

NEVER store live meter/SoC data here — that must always be read fresh — nor secrets. Values are
opaque strings (callers JSON-encode their own payloads).
"""
from __future__ import annotations

import sqlite3
from collections.abc import Callable

from ems.perf import timed

_BUSY_TIMEOUT_MS = 5000  # see ems/storage/history.py for the WAL/synchronous/timeout rationale


class CacheStore:
    """key → (value, created_at, expires_at). `get` honours the TTL (and lazily drops the row when
    expired); `get_with_age` ignores expiry and hands back the value plus its age, for callers that
    manage their own freshness (warm-start). The clock is injectable for tests."""

    def __init__(self, db_path: str, *, clock: Callable[[], float] | None = None) -> None:
        self.db_path = db_path
        if clock is not None:
            self._clock = clock
        else:
            import time
            self._clock = time.time

    def _conn(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.db_path, timeout=_BUSY_TIMEOUT_MS / 1000)
        con.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
        con.execute("PRAGMA synchronous=NORMAL")  # WAL-safe; see HistoryStore
        return con

    def init(self) -> None:
        con = self._conn()
        try:
            con.execute("PRAGMA journal_mode=WAL")
            con.execute(
                "CREATE TABLE IF NOT EXISTS cache "
                "(key TEXT PRIMARY KEY, value TEXT NOT NULL, "
                "created_at REAL NOT NULL, expires_at REAL NOT NULL)"
            )
            con.commit()
        finally:
            con.close()

    def set(self, key: str, value: str, ttl_seconds: float) -> None:
        with timed("store.cache.set"):
            now = self._clock()
            con = self._conn()
            try:
                con.execute(
                    "INSERT INTO cache (key, value, created_at, expires_at) VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value, "
                    "created_at=excluded.created_at, expires_at=excluded.expires_at",
                    (key, value, now, now + float(ttl_seconds)),
                )
                con.commit()
            finally:
                con.close()

    def get(self, key: str) -> str | None:
        """The value if present and unexpired, else None. Expired rows are dropped on read."""
        with timed("store.cache.get"):
            now = self._clock()
            con = self._conn()
            try:
                row = con.execute(
                    "SELECT value, expires_at FROM cache WHERE key=?", (key,)
                ).fetchone()
                if row is None:
                    return None
                value, expires_at = row
                if expires_at <= now:
                    con.execute("DELETE FROM cache WHERE key=?", (key,))
                    con.commit()
                    return None
                return value
            finally:
                con.close()

    def get_with_age(self, key: str) -> tuple[str, float] | None:
        """(value, age_seconds) regardless of expiry, or None if absent. For warm-start, where the
        caller (a source with its own TTL) decides whether the snapshot is still usable."""
        with timed("store.cache.get"):
            now = self._clock()
            con = self._conn()
            try:
                row = con.execute(
                    "SELECT value, created_at FROM cache WHERE key=?", (key,)
                ).fetchone()
                if row is None:
                    return None
                value, created_at = row
                return value, max(0.0, now - created_at)
            finally:
                con.close()

    def purge_expired(self) -> int:
        """Drop every expired row; returns how many. Cheap housekeeping to bound table growth.
        Not wrapped in timed — housekeeping, not a hot path."""
        now = self._clock()
        con = self._conn()
        try:
            cur = con.execute("DELETE FROM cache WHERE expires_at <= ?", (now,))
            con.commit()
            return cur.rowcount
        finally:
            con.close()

    def count(self) -> int:
        """Total cache row count (live + expired). Diagnostic — not wrapped in timed."""
        con = self._conn()
        try:
            return int(con.execute("SELECT COUNT(*) FROM cache").fetchone()[0])
        finally:
            con.close()

    def breakdown(self) -> dict[str, int]:
        """{'total': N, '<prefix>': count, ...} over the LIVE (unexpired) rows, grouped by the key
        prefix before ':'. For observability — showing how much is reused instead of refetched.
        Diagnostic — not wrapped in timed."""
        now = self._clock()
        con = self._conn()
        try:
            rows = con.execute("SELECT key FROM cache WHERE expires_at > ?", (now,)).fetchall()
        finally:
            con.close()
        out: dict[str, int] = {"total": 0}
        for (k,) in rows:
            out["total"] += 1
            kind = k.split(":", 1)[0] if ":" in k else "other"
            out[kind] = out.get(kind, 0) + 1
        return out
