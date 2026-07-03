"""SQLite time-series store. Raw and derived values live in SEPARATE tables (SPEC §4.3)
so derived values can always be recomputed after a sign/calibration fix."""
from __future__ import annotations

import json
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
            # INCREMENTAL auto-vacuum lets maintenance reclaim space freed by retention purges
            # without a full table-locking VACUUM. Only takes effect on a fresh DB (set before the
            # first table); harmless no-op on an existing one (retention still bounds row growth).
            await db.execute("PRAGMA auto_vacuum=INCREMENTAL")
            await db.execute(
                "CREATE TABLE IF NOT EXISTS raw_samples "
                "(ts TEXT NOT NULL, grid_power_w REAL NOT NULL, solar_power_w REAL NOT NULL, "
                "battery_power_w REAL NOT NULL, ev_power_w REAL NOT NULL, soc_pct REAL NOT NULL)"
            )
            await db.execute(
                "CREATE TABLE IF NOT EXISTS derived_samples "
                "(ts TEXT NOT NULL, house_load_w REAL NOT NULL, non_ev_load_w REAL NOT NULL)"
            )
            # Timestamp indexes: the story/forecast/distribution paths query by `ts` window, and
            # retention purges by `ts`. Without these, those scans get slower as the DB ages.
            await db.execute("CREATE INDEX IF NOT EXISTS idx_raw_ts ON raw_samples(ts)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_derived_ts ON derived_samples(ts)")
            # Stored price slots (spec 2026-07-03): finance/best-price need the price that was
            # active in a PAST slot, which the live price feed no longer carries. Upserted by the
            # recorder; purged with the samples.
            await db.execute(
                "CREATE TABLE IF NOT EXISTS price_slots "
                "(start_ts TEXT PRIMARY KEY, eur_per_kwh REAL NOT NULL)"
            )
            # Per-day finance rollups (JSON payload so fields can evolve without migrations).
            # Deliberately NOT covered by retention purges — this is the long-horizon financial
            # record (backlog B-13): a year of rows is only ~365 entries.
            await db.execute(
                "CREATE TABLE IF NOT EXISTS daily_finance "
                "(day TEXT PRIMARY KEY, data TEXT NOT NULL)"
            )
            await db.commit()

    async def purge_older_than(self, cutoff_iso: str) -> int:
        """Delete rows older than `cutoff_iso` (UTC-ISO) from BOTH sample tables atomically (one
        commit), so retention can never leave raw/derived out of sync. Returns total rows deleted.
        `ts` is UTC-ISO, so the lexicographic `<` is a correct time comparison."""
        async with self._conn() as db:
            cur = await db.execute("DELETE FROM raw_samples WHERE ts < ?", (cutoff_iso,))
            deleted = cur.rowcount or 0
            cur = await db.execute("DELETE FROM derived_samples WHERE ts < ?", (cutoff_iso,))
            deleted += cur.rowcount or 0
            cur = await db.execute("DELETE FROM price_slots WHERE start_ts < ?", (cutoff_iso,))
            deleted += cur.rowcount or 0
            # daily_finance is intentionally NOT purged (long-horizon record, B-13).
            await db.commit()
            return deleted

    async def maintain(self) -> None:
        """Periodic housekeeping for a 24/7 install: truncate the WAL so it can't grow unbounded,
        and reclaim space freed by purges (incremental_vacuum is a no-op unless auto_vacuum is on).
        Best-effort and non-fatal — a busy DB just retries next cycle."""
        async with self._conn() as db:
            await db.execute("PRAGMA incremental_vacuum")
            await db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            await db.commit()

    async def db_stats(self) -> dict:
        """Cheap size/row diagnostics for the System page (page_count×page_size = DB bytes; the
        two sample row counts; WAL bytes from the -wal sidecar file if present)."""
        import os

        async with self._conn() as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute("PRAGMA page_count")
            pages = (await cur.fetchone())[0]
            cur = await db.execute("PRAGMA page_size")
            page_size = (await cur.fetchone())[0]
            cur = await db.execute("SELECT COUNT(*) FROM raw_samples")
            raw_rows = (await cur.fetchone())[0]
            cur = await db.execute("SELECT COUNT(*) FROM derived_samples")
            derived_rows = (await cur.fetchone())[0]
        wal_bytes = 0
        try:
            wal_bytes = os.path.getsize(f"{self.db_path}-wal")
        except OSError:
            pass
        return {"db_bytes": pages * page_size, "wal_bytes": wal_bytes,
                "raw_rows": raw_rows, "derived_rows": derived_rows}

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

    async def raw_between(self, start_iso: str, end_iso: str, limit: int = 6000) -> list[dict]:
        return await self._between("raw_samples", _RAW_COLS, start_iso, end_iso, limit)

    async def derived_between(self, start_iso: str, end_iso: str, limit: int = 6000) -> list[dict]:
        return await self._between("derived_samples", _DERIVED_COLS, start_iso, end_iso, limit)

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

    async def _between(
        self, table: str, cols: tuple[str, ...], start_iso: str, end_iso: str, limit: int
    ) -> list[dict]:
        # Rows in [start, end) — a bounded calendar-day window (oldest-first, capped). `ts` is
        # UTC-ISO so a lexicographic comparison is a correct time comparison. table/cols are
        # module constants — no injection. Bounded so an old day fetches only that day, not all
        # history since (keeps load minimal).
        query = (f"SELECT {', '.join(cols)} FROM {table} WHERE ts >= ? AND ts < ? "
                 f"ORDER BY rowid ASC LIMIT ?")
        async with self._conn() as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(query, (start_iso, end_iso, limit))
            return [dict(r) for r in await cur.fetchall()]

    async def upsert_price_slots(self, slots: list[tuple[str, float]]) -> None:
        """Idempotently store (start_ts UTC-ISO, €/kWh) slots — re-fetches simply overwrite."""
        if not slots:
            return
        async with self._conn() as db:
            await db.executemany(
                "INSERT OR REPLACE INTO price_slots (start_ts, eur_per_kwh) VALUES (?, ?)", slots)
            await db.commit()

    async def prices_between(self, start_iso: str, end_iso: str) -> list[dict]:
        """Stored price slots in [start, end), oldest-first (UTC-ISO ⇒ lexicographic = time)."""
        async with self._conn() as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT start_ts, eur_per_kwh FROM price_slots "
                "WHERE start_ts >= ? AND start_ts < ? ORDER BY start_ts ASC",
                (start_iso, end_iso))
            return [dict(r) for r in await cur.fetchall()]

    async def upsert_daily_finance(self, day: str, data: dict) -> None:
        """Store/replace one local day's finance rollup (day = YYYY-MM-DD, data = JSON-able)."""
        async with self._conn() as db:
            await db.execute(
                "INSERT OR REPLACE INTO daily_finance (day, data) VALUES (?, ?)",
                (day, json.dumps(data)))
            await db.commit()

    async def daily_finance_between(self, start_day: str, end_day: str) -> list[dict]:
        """Finance rollups for days in [start, end) as {day, data}, oldest-first."""
        async with self._conn() as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT day, data FROM daily_finance WHERE day >= ? AND day < ? "
                "ORDER BY day ASC", (start_day, end_day))
            return [{"day": r["day"], "data": json.loads(r["data"])} for r in await cur.fetchall()]

    async def table_names(self) -> set[str]:
        async with self._conn() as db:
            cur = await db.execute("SELECT name FROM sqlite_master WHERE type='table'")
            return {r[0] for r in await cur.fetchall()}
