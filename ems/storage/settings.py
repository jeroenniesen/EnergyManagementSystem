"""Runtime settings KV store (SPEC §9.4) — JSON values in a `settings` table on the shared DB.

Persistence only: validation, defaults and the editable schema live in `ems.settings`. Shares the
same SQLite file as the history store (WAL + busy timeout) so reads/writes don't collide.
"""
from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import Any

import aiosqlite

_BUSY_TIMEOUT_MS = 3000


class SettingsStore:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    @asynccontextmanager
    async def _conn(self):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
            yield db

    async def init(self) -> None:
        async with self._conn() as db:
            await db.execute("PRAGMA journal_mode=WAL")
            await db.execute(
                "CREATE TABLE IF NOT EXISTS settings "
                "(key TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )
            await db.commit()

    async def all(self) -> dict[str, Any]:
        """Every stored key → decoded JSON value. A corrupt row is skipped, not fatal."""
        async with self._conn() as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute("SELECT key, value FROM settings")
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
                    "INSERT INTO settings (key, value) VALUES (?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (key, json.dumps(value)),
                )
            await db.commit()
