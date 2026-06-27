"""JSON key→value store on the shared SQLite DB (WAL + busy timeout). Backs both the runtime
settings (SPEC §9.4) and other runtime state (e.g. the manual override). Persistence only:
validation/defaults/schema for settings live in `ems.settings`.

The `table` is a fixed, code-supplied identifier (never user input) — guarded with `isidentifier`
so it can be interpolated into DDL/queries without an injection surface.
"""
from __future__ import annotations

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

    @asynccontextmanager
    async def _conn(self):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
            yield db

    async def init(self) -> None:
        async with self._conn() as db:
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
        async with self._conn() as db:
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
        async with self._conn() as db:
            await db.executemany(
                f"DELETE FROM {self.table} WHERE key = ?", [(k,) for k in keys]
            )
            await db.commit()
