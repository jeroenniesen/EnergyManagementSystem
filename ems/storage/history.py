"""SQLite time-series store. Raw and derived values live in SEPARATE tables (SPEC §4.3)
so derived values can always be recomputed after a sign/calibration fix."""
from __future__ import annotations

from contextlib import asynccontextmanager

import aiosqlite

from ems.domain import RawSample
from ems.load_model import DerivedSample

_RAW_COLS = ("ts", "grid_power_w", "solar_power_w", "battery_power_w", "ev_power_w", "soc_pct")
_DERIVED_COLS = ("ts", "house_load_w", "non_ev_load_w")
# Public column order for CSV export (header is stable even when there are no rows yet).
RAW_COLUMNS = _RAW_COLS
DERIVED_COLUMNS = _DERIVED_COLS
_BUSY_TIMEOUT_MS = 3000


class HistoryStore:
    """Append-only history. `ts` is an ISO-8601 string; ordering uses rowid (insertion order)
    so it is correct regardless of timezone offset / DST in the `ts` text. WAL mode + a busy
    timeout let the recorder write concurrently with API reads without 'database is locked'."""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    @asynccontextmanager
    async def _conn(self):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
            yield db

    async def init(self) -> None:
        # WAL allows concurrent reads during writes. Create both tables in one transaction
        # (single commit) so they appear atomically — a crash can't leave one without the other.
        async with self._conn() as db:
            await db.execute("PRAGMA journal_mode=WAL")
            await db.execute(
                "CREATE TABLE IF NOT EXISTS raw_samples "
                "(ts TEXT NOT NULL, grid_power_w REAL NOT NULL, solar_power_w REAL NOT NULL, "
                "battery_power_w REAL NOT NULL, ev_power_w REAL NOT NULL, soc_pct REAL NOT NULL)"
            )
            await db.execute(
                "CREATE TABLE IF NOT EXISTS derived_samples "
                "(ts TEXT NOT NULL, house_load_w REAL NOT NULL, non_ev_load_w REAL NOT NULL)"
            )
            await db.commit()

    async def record(self, ts: str, raw: RawSample, derived: DerivedSample) -> None:
        async with self._conn() as db:
            await db.execute(
                "INSERT INTO raw_samples "
                "(ts, grid_power_w, solar_power_w, battery_power_w, ev_power_w, soc_pct) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (ts, raw.grid_power_w, raw.solar_power_w, raw.battery_power_w,
                 raw.ev_power_w, raw.soc_pct),
            )
            await db.execute(
                "INSERT INTO derived_samples (ts, house_load_w, non_ev_load_w) VALUES (?, ?, ?)",
                (ts, derived.house_load_w, derived.non_ev_load_w),
            )
            # Both INSERTs must share THIS single commit (atomic raw+derived pair).
            # Do NOT add an intermediate commit between them — it would let the tables drift.
            await db.commit()

    async def recent_raw(self, limit: int = 100) -> list[dict]:
        return await self._recent("raw_samples", _RAW_COLS, limit)

    async def recent_derived(self, limit: int = 100) -> list[dict]:
        return await self._recent("derived_samples", _DERIVED_COLS, limit)

    async def recent_raw_since(self, cutoff_iso: str, limit: int = 6000) -> list[dict]:
        return await self._since("raw_samples", _RAW_COLS, cutoff_iso, limit)

    async def recent_derived_since(self, cutoff_iso: str, limit: int = 6000) -> list[dict]:
        return await self._since("derived_samples", _DERIVED_COLS, cutoff_iso, limit)

    async def _recent(self, table: str, cols: tuple[str, ...], limit: int) -> list[dict]:
        # table/cols are module constants (never user input) — no injection surface.
        query = f"SELECT {', '.join(cols)} FROM {table} ORDER BY rowid DESC LIMIT ?"
        async with self._conn() as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(query, (limit,))
            rows = await cur.fetchall()
            return [dict(r) for r in rows]

    async def _since(
        self, table: str, cols: tuple[str, ...], cutoff_iso: str, limit: int
    ) -> list[dict]:
        # Rows at/after `cutoff_iso` (newest-first, capped). `ts` is UTC-ISO so a lexicographic
        # comparison is a correct time comparison. table/cols are module constants — no injection.
        query = (f"SELECT {', '.join(cols)} FROM {table} WHERE ts >= ? "
                 f"ORDER BY rowid DESC LIMIT ?")
        async with self._conn() as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(query, (cutoff_iso, limit))
            return [dict(r) for r in await cur.fetchall()]

    async def table_names(self) -> set[str]:
        async with self._conn() as db:
            cur = await db.execute("SELECT name FROM sqlite_master WHERE type='table'")
            return {r[0] for r in await cur.fetchall()}
