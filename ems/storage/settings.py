"""JSON key→value store on the shared SQLite DB (WAL + busy timeout). Backs both the runtime
settings (SPEC §9.4) and other runtime state (e.g. the manual override). Persistence only:
validation/defaults/schema for settings live in `ems.settings`.

The `table` is a fixed, code-supplied identifier (never user input) — guarded with `isidentifier`
so it can be interpolated into DDL/queries without an injection surface.
"""
from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from typing import Any

import aiosqlite

_BUSY_TIMEOUT_MS = 3000


class SettingsStore:
    def __init__(self, db_path: str, *, table: str = "settings") -> None:
        if not table.isidentifier():
            raise ValueError(f"unsafe table name: {table!r}")
        self.db_path = db_path
        self.table = table
        # Long-lived connection (perf: BACKLOG B-49), mirroring HistoryStore — see its `__init__`
        # docstring for the full rationale (lazy-open, write-lock-serialized, reads share freely).
        self._db: aiosqlite.Connection | None = None
        self._connect_lock = asyncio.Lock()
        self._write_lock = asyncio.Lock()

    async def _connection(self) -> aiosqlite.Connection:
        if self._db is None:
            async with self._connect_lock:
                if self._db is None:
                    conn = aiosqlite.connect(self.db_path)
                    conn._thread.daemon = True  # see HistoryStore._connection() for rationale
                    db = await conn
                    await db.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
                    self._db = db
        return self._db

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
                f"CREATE TABLE IF NOT EXISTS {self.table} "
                "(key TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )
            await db.commit()

    async def all(self) -> dict[str, Any]:
        """Every stored key → decoded JSON value. A corrupt row is skipped, not fatal."""
        async with self._conn() as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(f"SELECT key, value FROM {self.table}")
            rows = await cur.fetchall()
        out: dict[str, Any] = {}
        for r in rows:
            try:
                out[r["key"]] = json.loads(r["value"])
            except (ValueError, TypeError):
                continue
        return out

    async def set_many(self, items: dict[str, Any]) -> None:
        """Upsert the given key→value pairs in a single transaction. No-op for an empty dict."""
        if not items:
            return
        async with self._write_conn() as db:
            for key, value in items.items():
                await db.execute(
                    f"INSERT INTO {self.table} (key, value) VALUES (?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (key, json.dumps(value)),
                )
            await db.commit()

    async def delete(self, *keys: str) -> None:
        """Remove the given keys (used to clear runtime state such as a manual override)."""
        if not keys:
            return
        async with self._write_conn() as db:
            await db.executemany(
                f"DELETE FROM {self.table} WHERE key = ?", [(k,) for k in keys]
            )
            await db.commit()
