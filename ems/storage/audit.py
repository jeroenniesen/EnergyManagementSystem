"""Append-only audit log. Records every change the EMS makes to the battery's operating mode /
plan, every configuration change, and AI validations — each with a human-readable summary and a
JSON detail blob. Lives in the shared SQLite DB. Read-only from the UI; never holds secrets."""
from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import aiosqlite

from ems.perf import atimed
from ems.storage.history import _connection_is_dead, _log, self_healing

_BUSY_TIMEOUT_MS = 5000  # see ems/storage/history.py for the WAL/synchronous/timeout rationale


@self_healing
class AuditStore:
    """`ts` is an ISO-8601 string; ordering uses the autoincrement id (insertion order). WAL +
    busy timeout let it write concurrently with the recorder and API reads."""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        # Long-lived connection (perf: BACKLOG B-49), mirroring HistoryStore — see its `__init__`
        # docstring for the full rationale (lazy-open, write-lock-serialized, reads share freely).
        self._db: aiosqlite.Connection | None = None
        self._connect_lock = asyncio.Lock()
        self._write_lock = asyncio.Lock()
        self._last_reheal_at: datetime | None = None  # see HistoryStore for the self-heal rationale

    async def _connection(self) -> aiosqlite.Connection:
        db = self._db
        if db is not None and _connection_is_dead(db):  # proactive self-heal (see HistoryStore)
            await self._discard_connection(db, reason="worker thread stopped / connection gone")
        if self._db is None:
            async with self._connect_lock:
                if self._db is None:
                    conn = aiosqlite.connect(self.db_path)
                    conn._thread.daemon = True  # see HistoryStore._connection() for rationale
                    db = await conn
                    await db.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
                    await db.execute("PRAGMA synchronous=NORMAL")  # WAL-safe; see HistoryStore
                    self._db = db
        return self._db

    async def _note_dead_connection(self, exc: BaseException, dead_conn) -> None:
        # Called by the `self_healing` retry wrapper — see HistoryStore for the full rationale
        # (including why the discard targets the connection the failing call was using).
        await self._discard_connection(dead_conn, reason=f"{type(exc).__name__}: {exc}")

    async def reset_connection(self) -> None:
        """Force the shared connection to be discarded + reopened on the next call (best-effort)."""
        await self._discard_connection(self._db, reason="watchdog reset")

    async def _discard_connection(self, dead_conn, *, reason: str) -> None:
        # See HistoryStore._discard_connection for the sibling-race rationale (a stale caller must
        # not close a connection a sibling already healed).
        async with self._connect_lock:
            db = self._db
            if db is None:
                return
            if dead_conn is not None and db is not dead_conn:
                _log.debug("%s: shared connection already healed by a sibling (%s) — skipping "
                           "discard", type(self).__name__, reason)
                return
            self._db = None
            self._last_reheal_at = datetime.now(UTC)
            _log.warning("%s: shared connection unusable (%s) — discarding; reopening on next call",
                         type(self).__name__, reason)
            try:
                await db.close()
            except Exception:
                pass

    def reheal_stats(self) -> dict:
        at = self._last_reheal_at
        return {"last_reheal_iso": at.isoformat() if at else None}

    @asynccontextmanager
    async def _conn(self):
        yield await self._connection()

    @asynccontextmanager
    async def _write_conn(self):
        async with self._write_lock:
            yield await self._connection()

    async def close(self) -> None:
        async with self._connect_lock:
            if self._db is not None:
                await self._db.close()
                self._db = None

    def __del__(self) -> None:
        # Synchronous cleanup for a discarded-without-close() store — see HistoryStore.__del__
        # for the full rationale (avoids an indeterminate deferred file-close on aiosqlite's
        # background worker thread).
        db = self._db
        if db is None:
            return
        conn = getattr(db, "_connection", None)
        if conn is not None:
            db._connection = None
            try:
                conn.close()
            except Exception:
                pass

    async def init(self) -> None:
        async with self._write_conn() as db:
            await db.execute("PRAGMA journal_mode=WAL")
            await db.execute(
                "CREATE TABLE IF NOT EXISTS audit_log "
                "(id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, category TEXT NOT NULL, "
                "summary TEXT NOT NULL, detail TEXT NOT NULL)"
            )
            await db.commit()

    async def append(
        self, ts: str, category: str, summary: str, detail: dict | None = None
    ) -> None:
        async with atimed("store.audit.append"):
            async with self._write_conn() as db:
                await db.execute(
                    "INSERT INTO audit_log (ts, category, summary, detail) VALUES (?, ?, ?, ?)",
                    (ts, category, summary, json.dumps(detail or {})),
                )
                await db.commit()

    async def recent(self, limit: int = 100, category: str | None = None) -> list[dict]:
        """Newest-first audit entries, optionally filtered by category. `detail` is decoded to a
        dict (empty on any decode error — never raises)."""
        return await self._recent_rows(limit, category)

    async def _recent_rows(self, limit: int, category: str | None) -> list[dict]:
        """Private, UNWRAPPED query behind `recent()`. `last_decision_mode` (itself self-heal-
        wrapped) calls THIS, not `recent()`, so a dead connection triggers the reheal-retry at ONE
        level, not the nested up-to-4-attempts a wrapped-calls-wrapped path would cause (F7)."""
        where = "WHERE category = ? " if category else ""
        params: tuple = (category, limit) if category else (limit,)
        async with self._conn() as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                f"SELECT id, ts, category, summary, detail FROM audit_log {where}"
                "ORDER BY id DESC LIMIT ?",
                params,
            )
            rows = await cur.fetchall()
        out: list[dict] = []
        for r in rows:
            d = dict(r)
            try:
                d["detail"] = json.loads(d["detail"])
            except (ValueError, TypeError):
                d["detail"] = {}
            out.append(d)
        return out

    async def between(self, start_iso: str, end_iso: str, limit: int = 2000) -> list[dict]:
        """Audit entries with `ts` in [start, end), oldest-first (`ts` is ISO-8601 UTC, so a
        lexicographic comparison is a correct time comparison — same convention as every other
        `_between` reader in `ems/storage/history.py`). For the weekly digest (BACKLOG B-58):
        windowing by time, not `recent()`'s row-count cap, so a busy week is never truncated
        before it's fully counted. `detail` is decoded exactly like `recent()` (empty dict on any
        decode error — never raises)."""
        async with self._conn() as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT id, ts, category, summary, detail FROM audit_log "
                "WHERE ts >= ? AND ts < ? ORDER BY id ASC LIMIT ?",
                (start_iso, end_iso, limit),
            )
            rows = await cur.fetchall()
        out: list[dict] = []
        for r in rows:
            d = dict(r)
            try:
                d["detail"] = json.loads(d["detail"])
            except (ValueError, TypeError):
                d["detail"] = {}
            out.append(d)
        return out

    async def last_decision_mode(self) -> str | None:
        """The desired_mode of the most recent battery_decision entry — used to dedupe so we only
        log a decision when the mode actually changes (≤ a handful a day). Calls the private
        `_recent_rows` (not the self-heal-wrapped `recent`) so the reheal-retry never nests (F7)."""
        rows = await self._recent_rows(1, category="battery_decision")
        return rows[0]["detail"].get("desired_mode") if rows else None
