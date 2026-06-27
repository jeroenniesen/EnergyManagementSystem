"""SQLite time-series store. Raw and derived values live in SEPARATE tables (SPEC §4.3)
so derived values can always be recomputed after a sign/calibration fix."""
from __future__ import annotations

import aiosqlite

from ems.domain import RawSample
from ems.load_model import DerivedSample

_SCHEMA = """
CREATE TABLE IF NOT EXISTS raw_samples (
    ts TEXT NOT NULL,
    grid_power_w REAL NOT NULL,
    solar_power_w REAL NOT NULL,
    battery_power_w REAL NOT NULL,
    ev_power_w REAL NOT NULL,
    soc_pct REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS derived_samples (
    ts TEXT NOT NULL,
    house_load_w REAL NOT NULL,
    non_ev_load_w REAL NOT NULL
);
"""

_RAW_COLS = ("ts", "grid_power_w", "solar_power_w", "battery_power_w", "ev_power_w", "soc_pct")
_DERIVED_COLS = ("ts", "house_load_w", "non_ev_load_w")


class HistoryStore:
    """Append-only history. `ts` is an ISO-8601 string; ordering uses rowid (insertion order)
    so it is correct regardless of timezone offset / DST in the `ts` text."""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    async def init(self) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.executescript(_SCHEMA)
            await db.commit()

    async def record(self, ts: str, raw: RawSample, derived: DerivedSample) -> None:
        async with aiosqlite.connect(self.db_path) as db:
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
            await db.commit()

    async def recent_raw(self, limit: int = 100) -> list[dict]:
        return await self._recent("raw_samples", _RAW_COLS, limit)

    async def recent_derived(self, limit: int = 100) -> list[dict]:
        return await self._recent("derived_samples", _DERIVED_COLS, limit)

    async def _recent(self, table: str, cols: tuple[str, ...], limit: int) -> list[dict]:
        # table/cols are module constants (never user input) — no injection surface.
        query = f"SELECT {', '.join(cols)} FROM {table} ORDER BY rowid DESC LIMIT ?"
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(query, (limit,))
            rows = await cur.fetchall()
            return [dict(r) for r in rows]

    async def table_names(self) -> set[str]:
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute("SELECT name FROM sqlite_master WHERE type='table'")
            return {r[0] for r in await cur.fetchall()}
