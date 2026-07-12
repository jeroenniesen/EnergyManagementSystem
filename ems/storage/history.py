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
_CAR_SOC_ANCHOR_KEY = "anchor"  # single-row key for the manual car-SoC anchor (see set/get below)


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
            # Solar forecast snapshots (observability-data): the day-ahead P10/P50/P90 forecast
            # for each 15-min slot, keyed by the date it was ISSUED — so a later same-day cycle
            # can't overwrite it with a nowcast. Needed to measure forecast-vs-actual error
            # (join `start` to raw_samples.solar_power_w). Upserted by the recorder, purged with
            # the samples (bounded by slot `start`, like price_slots).
            await db.execute(
                "CREATE TABLE IF NOT EXISTS forecast_snapshots "
                "(issued_date TEXT NOT NULL, start TEXT NOT NULL, p10_w REAL NOT NULL, "
                "p50_w REAL NOT NULL, p90_w REAL NOT NULL, PRIMARY KEY (issued_date, start))"
            )
            # Plan/target history (observability-data): what the planner intended each cycle —
            # strategy, the target SoC it's aiming for + deadline, the resolved intent, and the
            # SoC observed at that moment — so a reviewer can later compare `target_soc` against
            # the achieved `soc_pct` in raw_samples. Recorded by the recorder, purged with samples.
            await db.execute(
                "CREATE TABLE IF NOT EXISTS plan_history "
                "(ts TEXT NOT NULL, strategy TEXT, target_soc REAL, deadline TEXT, "
                "soc_pct REAL, intent TEXT)"
            )
            await db.execute("CREATE INDEX IF NOT EXISTS idx_plan_ts ON plan_history(ts)")
            # Cumulative gas meter readings (B-02: gas folds into the CO2 footprint). The recorder
            # inserts one row/cycle when a gas meter is paired to the P1; window consumption is a
            # last-minus-first delta over the readings (see reporting.gas_m3_consumed), so we only
            # need the raw cumulative reading, not a derived per-slot volume. Purged with samples.
            await db.execute(
                "CREATE TABLE IF NOT EXISTS gas_readings "
                "(ts TEXT PRIMARY KEY, total_gas_m3 REAL NOT NULL)"
            )
            # Time-varying grid CO2 intensity (roadmap F3, reporting-only): one row per 15-min
            # slot, mirroring price_slots. Upserted by the recorder from an optional carbon
            # source (live ElectricityMaps signal or the flat factor), purged with the samples.
            await db.execute(
                "CREATE TABLE IF NOT EXISTS carbon_intensity "
                "(start_ts TEXT PRIMARY KEY, kg_per_kwh REAL NOT NULL)"
            )
            # Manual car-SoC anchor (feat/ev-charging): the car has no API, so the user occasionally
            # sets a (percent, timestamp) anchor and EV SoC is ESTIMATED from measured charging
            # energy since it (see ems.ev_session.estimate_soc). One small piece of CURRENT state,
            # so it reuses the settings/control_state key→value + JSON-blob idiom (single row under
            # a fixed key) rather than a bespoke schema. NOT time-series → deliberately NOT purged.
            await db.execute(
                "CREATE TABLE IF NOT EXISTS car_soc_anchor "
                "(key TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )
            # Notification outbox (B-20): one row per notification, in-app-first (the row itself IS
            # the in-app delivery) with an optional ntfy push recorded in `delivered`. `dedupe_key`
            # is precomputed by the CALLER with the local day baked in (e.g. "backup_failed:
            # 2026-07-13") so `add_notification` only needs a plain equality check — a new day is
            # naturally a new key. Purged with the samples (bounded by `ts`).
            await db.execute(
                "CREATE TABLE IF NOT EXISTS notifications "
                "(id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, key TEXT NOT NULL, "
                "title TEXT NOT NULL, body TEXT NOT NULL, confidence TEXT, "
                "read INTEGER NOT NULL DEFAULT 0, delivered TEXT NOT NULL DEFAULT '[]', "
                "dedupe_key TEXT)"
            )
            await db.execute("CREATE INDEX IF NOT EXISTS idx_notifications_ts ON notifications(ts)")
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_notifications_dedupe "
                "ON notifications(dedupe_key)"
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
            cur = await db.execute("DELETE FROM forecast_snapshots WHERE start < ?", (cutoff_iso,))
            deleted += cur.rowcount or 0
            cur = await db.execute("DELETE FROM plan_history WHERE ts < ?", (cutoff_iso,))
            deleted += cur.rowcount or 0
            cur = await db.execute("DELETE FROM gas_readings WHERE ts < ?", (cutoff_iso,))
            deleted += cur.rowcount or 0
            cur = await db.execute(
                "DELETE FROM carbon_intensity WHERE start_ts < ?", (cutoff_iso,))
            deleted += cur.rowcount or 0
            cur = await db.execute("DELETE FROM notifications WHERE ts < ?", (cutoff_iso,))
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

    async def backup_to(self, dest_path: str) -> int:
        """Online backup of the whole DB to `dest_path` via `VACUUM INTO` (SPEC §11 durability).
        VACUUM INTO reads a consistent snapshot and is SAFE on a live WAL database — unlike a raw
        file copy, which can catch a torn write mid-checkpoint — and it produces a compact,
        single-file, independent SQLite DB. Creates the parent directory. Returns the resulting
        file size in bytes. Raises on ANY error — the caller (maintenance loop) decides whether a
        failure is fatal (it treats it as best-effort and logs loudly)."""
        import os

        os.makedirs(os.path.dirname(dest_path) or ".", exist_ok=True)
        # Own connection (not _conn): VACUUM requires NO other statement in progress, so the
        # busy_timeout pragma must be fully fetched/finalised first. Parameterised INTO filename
        # (SQLite >= 3.27); no manual transaction — VACUUM cannot run inside one.
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
            await cur.fetchall()
            await db.execute("VACUUM INTO ?", (dest_path,))
        return os.path.getsize(dest_path)

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

    async def upsert_forecast_snapshot(
        self, issued_date: str, slots: list[tuple[str, float, float, float]]
    ) -> None:
        """Record the day-ahead solar forecast for each slot, keyed by (issued_date, start).
        INSERT OR IGNORE: the FIRST snapshot recorded for a given (issued_date, slot) sticks — we
        want the day-ahead forecast, not a later-cycle nowcast overwriting it for error analysis."""
        if not slots:
            return
        async with self._conn() as db:
            await db.executemany(
                "INSERT OR IGNORE INTO forecast_snapshots "
                "(issued_date, start, p10_w, p50_w, p90_w) VALUES (?, ?, ?, ?, ?)",
                [(issued_date, start, p10, p50, p90) for start, p10, p50, p90 in slots],
            )
            await db.commit()

    async def forecasts_between(self, start_iso: str, end_iso: str) -> list[dict]:
        """Stored forecast snapshots with slot `start` in [start, end), ordered by
        (issued_date, start) (UTC-ISO ⇒ lexicographic = time)."""
        async with self._conn() as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT issued_date, start, p10_w, p50_w, p90_w FROM forecast_snapshots "
                "WHERE start >= ? AND start < ? ORDER BY issued_date ASC, start ASC",
                (start_iso, end_iso))
            return [dict(r) for r in await cur.fetchall()]

    async def record_plan(self, ts: str, snapshot: dict) -> None:
        """Append one plan/target history row (observability-data): what the planner intended
        THIS cycle. `snapshot` is the {"strategy","target_soc","deadline","soc_pct","intent"} dict
        assembled by the API's `_plan_snapshot` — missing keys default to None (a partial snapshot
        still records something rather than nothing)."""
        async with self._conn() as db:
            await db.execute(
                "INSERT INTO plan_history (ts, strategy, target_soc, deadline, soc_pct, intent) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (ts, snapshot.get("strategy"), snapshot.get("target_soc"),
                 snapshot.get("deadline"), snapshot.get("soc_pct"), snapshot.get("intent")),
            )
            await db.commit()

    async def plan_history_between(self, start_iso: str, end_iso: str) -> list[dict]:
        """Plan/target history rows with `ts` in [start, end), oldest-first (UTC-ISO ⇒
        lexicographic = time) — compare `target_soc` against raw_samples.soc_pct over time."""
        async with self._conn() as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT ts, strategy, target_soc, deadline, soc_pct, intent FROM plan_history "
                "WHERE ts >= ? AND ts < ? ORDER BY ts ASC",
                (start_iso, end_iso))
            return [dict(r) for r in await cur.fetchall()]

    async def record_gas(self, ts: str, total_gas_m3: float) -> None:
        """Upsert one cumulative gas meter reading (B-02). INSERT OR REPLACE: `ts` is the
        recorder's sense timestamp (one row/cycle in practice), so a re-record at the same `ts`
        (e.g. a retried cycle) simply overwrites rather than duplicating."""
        async with self._conn() as db:
            await db.execute(
                "INSERT OR REPLACE INTO gas_readings (ts, total_gas_m3) VALUES (?, ?)",
                (ts, total_gas_m3))
            await db.commit()

    async def gas_between(self, start_iso: str, end_iso: str) -> list[dict]:
        """Gas meter readings with `ts` in [start, end), oldest-first (UTC-ISO ⇒
        lexicographic = time) — window consumption is the last reading minus the first."""
        async with self._conn() as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT ts, total_gas_m3 FROM gas_readings WHERE ts >= ? AND ts < ? "
                "ORDER BY ts ASC",
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

    async def upsert_carbon(self, rows: list[tuple[str, float]]) -> None:
        """Idempotently store (start_ts UTC-ISO, kg CO2/kWh) slots — re-fetches simply overwrite
        (roadmap F3, reporting-only)."""
        if not rows:
            return
        async with self._conn() as db:
            await db.executemany(
                "INSERT OR REPLACE INTO carbon_intensity (start_ts, kg_per_kwh) VALUES (?, ?)",
                rows)
            await db.commit()

    async def carbon_between(self, start_iso: str, end_iso: str) -> list[dict]:
        """Stored carbon-intensity slots in [start, end), oldest-first (UTC-ISO ⇒
        lexicographic = time) — the Insights CO2 score averages these for a live window factor."""
        async with self._conn() as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT start_ts, kg_per_kwh FROM carbon_intensity "
                "WHERE start_ts >= ? AND start_ts < ? ORDER BY start_ts ASC",
                (start_iso, end_iso))
            return [dict(r) for r in await cur.fetchall()]

    async def set_car_soc_anchor(self, pct: float, ts: str) -> None:
        """Store/replace the manual car-SoC anchor: a percent at an ISO timestamp, from which EV
        SoC is estimated. Single-row upsert — a new anchor overwrites the previous one (there is
        only ever one 'last known' anchor)."""
        async with self._conn() as db:
            await db.execute(
                "INSERT OR REPLACE INTO car_soc_anchor (key, value) VALUES (?, ?)",
                (_CAR_SOC_ANCHOR_KEY, json.dumps({"pct": float(pct), "ts": ts})),
            )
            await db.commit()

    async def get_car_soc_anchor(self) -> tuple[float, str] | None:
        """The stored (pct, ts) anchor, or None if never set / the row is corrupt (never raises)."""
        async with self._conn() as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT value FROM car_soc_anchor WHERE key = ?", (_CAR_SOC_ANCHOR_KEY,))
            row = await cur.fetchone()
        if row is None:
            return None
        try:
            data = json.loads(row["value"])
            return float(data["pct"]), str(data["ts"])
        except (ValueError, TypeError, KeyError):
            return None

    async def add_notification(
        self, ts: str, key: str, title: str, body: str, *,
        confidence: str | None = None, dedupe_key: str | None = None,
    ) -> int | None:
        """Append one row to the notification outbox, or return None WITHOUT inserting if
        `dedupe_key` already matches an existing row (sparse by construction — B-20). The caller
        precomputes `dedupe_key` with the local calendar day baked in (e.g.
        "backup_failed:2026-07-13"), so this is a plain equality check: a repeat the SAME day is
        suppressed, a NEW day is simply a different key and always gets through. `delivered`
        starts as `["in_app"]` — storing the row IS the in-app delivery; a channel like ntfy is
        added afterwards via `set_notification_delivered` once (if) it actually succeeds."""
        async with self._conn() as db:
            if dedupe_key is not None:
                cur = await db.execute(
                    "SELECT 1 FROM notifications WHERE dedupe_key = ? LIMIT 1", (dedupe_key,))
                if await cur.fetchone() is not None:
                    return None
            cur = await db.execute(
                "INSERT INTO notifications "
                "(ts, key, title, body, confidence, read, delivered, dedupe_key) "
                "VALUES (?, ?, ?, ?, ?, 0, ?, ?)",
                (ts, key, title, body, confidence, json.dumps(["in_app"]), dedupe_key),
            )
            await db.commit()
            return cur.lastrowid

    async def set_notification_delivered(self, notification_id: int, delivered: list[str]) -> None:
        """Overwrite the delivered-channel list for one notification (Notifier calls this after a
        successful ntfy push has actually gone out)."""
        async with self._conn() as db:
            await db.execute(
                "UPDATE notifications SET delivered = ? WHERE id = ?",
                (json.dumps(delivered), notification_id),
            )
            await db.commit()

    async def notifications_between(
        self, start_iso: str, end_iso: str, limit: int = 500
    ) -> list[dict]:
        """Outbox rows with `ts` in [start, end), oldest-first (UTC-ISO ⇒ lexicographic = time) —
        mirrors the other `_between` helpers. Notifications are sparse by construction (dedupe_key
        collapses repeats), so a generous default limit comfortably covers a recency feed. `read`
        is decoded to a bool and `delivered` to a list (a corrupt/empty value degrades to [])."""
        async with self._conn() as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT id, ts, key, title, body, confidence, read, delivered, dedupe_key "
                "FROM notifications WHERE ts >= ? AND ts < ? ORDER BY ts ASC LIMIT ?",
                (start_iso, end_iso, limit))
            out = []
            for r in await cur.fetchall():
                row = dict(r)
                row["read"] = bool(row["read"])
                try:
                    row["delivered"] = json.loads(row["delivered"]) if row["delivered"] else []
                except (ValueError, TypeError):
                    row["delivered"] = []
                out.append(row)
            return out

    async def unread_count(self) -> int:
        """Count of unread notifications (the bell's dot count)."""
        async with self._conn() as db:
            cur = await db.execute("SELECT COUNT(*) FROM notifications WHERE read = 0")
            return (await cur.fetchone())[0]

    async def mark_notifications_read(
        self, ids: list[int] | None = None, mark_all: bool = False
    ) -> int:
        """Mark notifications read: `mark_all=True` marks every currently-unread row; otherwise
        marks exactly the given `ids` (an id that doesn't exist is silently ignored). Returns the
        number of rows actually changed."""
        async with self._conn() as db:
            if mark_all:
                cur = await db.execute("UPDATE notifications SET read = 1 WHERE read = 0")
            elif ids:
                placeholders = ", ".join("?" for _ in ids)
                cur = await db.execute(
                    f"UPDATE notifications SET read = 1 WHERE id IN ({placeholders})", ids)
            else:
                return 0
            await db.commit()
            return cur.rowcount or 0

    async def table_names(self) -> set[str]:
        async with self._conn() as db:
            cur = await db.execute("SELECT name FROM sqlite_master WHERE type='table'")
            return {r[0] for r in await cur.fetchall()}
