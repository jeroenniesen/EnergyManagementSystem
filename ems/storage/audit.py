"""Append-only audit log. Records every change the EMS makes to the battery's operating mode /
plan, every configuration change, and AI validations — each with a human-readable summary and a
JSON detail blob. Lives in the shared SQLite DB. Read-only from the UI; never holds secrets."""
from __future__ import annotations

import json
from contextlib import asynccontextmanager

import aiosqlite

_BUSY_TIMEOUT_MS = 3000


class AuditStore:
    """`ts` is an ISO-8601 string; ordering uses the autoincrement id (insertion order). WAL +
    busy timeout let it write concurrently with the recorder and API reads."""

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
                "CREATE TABLE IF NOT EXISTS audit_log "
                "(id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, category TEXT NOT NULL, "
                "summary TEXT NOT NULL, detail TEXT NOT NULL)"
            )
            await db.commit()

    async def append(
        self, ts: str, category: str, summary: str, detail: dict | None = None
    ) -> None:
        async with self._conn() as db:
            await db.execute(
                "INSERT INTO audit_log (ts, category, summary, detail) VALUES (?, ?, ?, ?)",
                (ts, category, summary, json.dumps(detail or {})),
            )
            await db.commit()

    async def recent(self, limit: int = 100, category: str | None = None) -> list[dict]:
        """Newest-first audit entries, optionally filtered by category. `detail` is decoded to a
        dict (empty on any decode error — never raises)."""
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

    async def last_decision_mode(self) -> str | None:
        """The desired_mode of the most recent battery_decision entry — used to dedupe so we only
        log a decision when the mode actually changes (≤ a handful a day)."""
        rows = await self.recent(1, category="battery_decision")
        return rows[0]["detail"].get("desired_mode") if rows else None
