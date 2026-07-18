"""SQLite time-series store. Raw and derived values live in SEPARATE tables (SPEC §4.3)
so derived values can always be recomputed after a sign/calibration fix."""
from __future__ import annotations

import asyncio
import functools
import json
import logging
import sqlite3
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import aiosqlite

from ems.domain import RawSample
from ems.load_model import DerivedSample
from ems.perf import atimed
from ems.storage.migrations import Migration, has_table, run_migrations

_log = logging.getLogger("ems.storage")

_RAW_COLS = ("ts", "grid_power_w", "solar_power_w", "battery_power_w", "ev_power_w", "soc_pct")
_DERIVED_COLS = ("ts", "house_load_w", "non_ev_load_w")
# Public column order for CSV export (header is stable even when there are no rows yet).
RAW_COLUMNS = _RAW_COLS
DERIVED_COLUMNS = _DERIVED_COLS
_BUSY_TIMEOUT_MS = 3000
_CAR_SOC_ANCHOR_KEY = "anchor"  # single-row key for the manual car-SoC anchor (see set/get below)

# --- Compact long-horizon observation store (design §4.1) + daily kWh rollups (B-13) ----------
_SLOT_SECONDS = 900  # 15-min observation slot
_SLOT_HOURS = _SLOT_SECONDS / 3600.0  # energy = mean power × slot hours
_DEFAULT_CADENCE_S = 300.0  # recorder default (config cycle_seconds); used for backfill coverage
_LOW_COVERAGE = 0.8  # coverage below this ⇒ the v1 "low_coverage" flag (strict <)
# 400 days of 15-min observations ≈ 35k rows — retained INDEPENDENTLY of raw retention_days so a
# full year of seasonal evidence survives the (much shorter) raw purge. daily_energy is never
# purged at all (see purge notes below).
OBSERVATION_RETENTION_DAYS = 400

_OBSERVATIONS_DDL = (
    "CREATE TABLE IF NOT EXISTS observations "
    "(slot_start TEXT PRIMARY KEY, mean_load_w REAL, mean_non_ev_load_w REAL, mean_solar_w REAL, "
    "samples INTEGER, coverage REAL, flags TEXT NOT NULL DEFAULT '[]')"
)
_DAILY_ENERGY_DDL = (
    "CREATE TABLE IF NOT EXISTS daily_energy "
    "(date TEXT PRIMARY KEY, solar_kwh REAL, load_kwh REAL, non_ev_load_kwh REAL, ev_kwh REAL, "
    "grid_import_kwh REAL, grid_export_kwh REAL, battery_charge_kwh REAL, "
    "battery_discharge_kwh REAL, coverage REAL)"
)

# --- Prediction ledger (design §4.2 / §4.3) -----------------------------------------------------
# Every forecast is persisted BEFORE its outcome is known, with its EXACT `issued_at` — this is the
# single out-of-sample scoring source (recomputing a historical forecast with later knowledge is
# not valid evaluation). `canonical=1` marks the anti-leakage day-ahead snapshot (the 18:00
# next-day forecast the scorer grades); throttled nowcasts land as `canonical=0` and remain for
# lead-time diagnostics. First-write-wins per (issued_at, kind, target_start). Retained for the
# same 400-day horizon as observations (purged by `target_start` — you can only score a target
# against an observation you still have).
_FORECAST_LEDGER_DDL = (
    "CREATE TABLE IF NOT EXISTS forecast_ledger "
    "(issued_at TEXT NOT NULL, kind TEXT NOT NULL, target_start TEXT NOT NULL, "
    "low_w REAL, expected_w REAL, high_w REAL, source TEXT, model_version TEXT, "
    "quality TEXT, canonical INTEGER NOT NULL DEFAULT 0, "
    "PRIMARY KEY (issued_at, kind, target_start))"
)
# Serves the scorer's hot path: "canonical rows for this kind over this target window".
_FORECAST_LEDGER_INDEX_DDL = (
    "CREATE INDEX IF NOT EXISTS idx_forecast_ledger_canonical "
    "ON forecast_ledger(kind, canonical, target_start)"
)
# Fixed tuple order for ledger_append() rows (documented so callers can't misplace a field).
_LEDGER_COLS = (
    "issued_at", "kind", "target_start", "low_w", "expected_w", "high_w",
    "source", "model_version", "quality", "canonical",
)


def _parse_utc(ts: object) -> datetime | None:
    """Parse a stored ISO `ts` to an aware UTC datetime (naive ⇒ assumed UTC). None on garbage."""
    if not isinstance(ts, str):
        return None
    try:
        dt = datetime.fromisoformat(ts)
    except ValueError:
        return None
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def _floor15(dt: datetime) -> datetime:
    """Floor an aware UTC datetime to its 15-min slot start."""
    return dt.replace(minute=(dt.minute // 15) * 15, second=0, microsecond=0)


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


_CADENCE_MIN_SAMPLES = 10  # below this, too little evidence to infer — trust the passed cadence
_CADENCE_CLAMP = (15.0, 900.0)  # plausible recorder cadence bounds (sub-slot; ≥ the API's floor)


def _infer_cadence_seconds(raw_rows: list[dict], fallback: float) -> float:
    """Infer the recording cadence of `raw_rows` from the data itself (F4): the MEDIAN positive
    inter-sample gap over the window, clamped to [15, 900]s. This is materialized-window-local so a
    day recorded at one cadence is scored at THAT cadence even if the recorder's live setting has
    since changed (the coverage denominator would otherwise be wrong). Falls back to the passed
    `fallback` cadence below `_CADENCE_MIN_SAMPLES` samples — too few to infer honestly."""
    times = sorted(dt for r in raw_rows if (dt := _parse_utc(r.get("ts"))) is not None)
    if len(times) < _CADENCE_MIN_SAMPLES:
        return fallback
    gaps = sorted(
        g for a, b in zip(times, times[1:], strict=False)
        if (g := (b - a).total_seconds()) > 0
    )
    if not gaps:
        return fallback
    n = len(gaps)
    median = gaps[n // 2] if n % 2 else (gaps[n // 2 - 1] + gaps[n // 2]) / 2.0
    return min(_CADENCE_CLAMP[1], max(_CADENCE_CLAMP[0], median))


def aggregate_observations(
    raw_rows: list[dict], derived_rows: list[dict], cadence_seconds: float = _DEFAULT_CADENCE_S
) -> list[dict]:
    """Pure: group raw+derived samples into 15-min UTC observation rows. `mean_non_ev_load_w`
    comes straight from the recorded derived `non_ev_load_w`, which ALREADY excludes EV charging
    per the load_model threshold rule (`reconstruct`), so EV exclusion happens once, at ingest.

    `coverage` is cadence-aware: samples / (900s / cadence), capped at 1.0. `flags` in v1 is a JSON
    array carrying ONLY "low_coverage" (coverage < 0.8). Deliberately NOT yet emitted (documented
    honestly for a later iteration): clamped/implausible input, manual-override, and
    calibration/setup-activity exclusions."""
    expected = max(1.0, _SLOT_SECONDS / max(float(cadence_seconds), 1.0))
    solar_by: dict[datetime, list[float]] = defaultdict(list)
    load_by: dict[datetime, list[float]] = defaultdict(list)
    nonev_by: dict[datetime, list[float]] = defaultdict(list)
    count_by: dict[datetime, int] = defaultdict(int)
    for r in raw_rows:
        dt = _parse_utc(r.get("ts"))
        if dt is None:
            continue
        slot = _floor15(dt)
        solar_by[slot].append(max(0.0, float(r.get("solar_power_w", 0.0))))  # production ≥ 0 (§4.7)
        count_by[slot] += 1
    for r in derived_rows:
        dt = _parse_utc(r.get("ts"))
        if dt is None:
            continue
        slot = _floor15(dt)
        load_by[slot].append(float(r.get("house_load_w", 0.0)))
        nonev_by[slot].append(float(r.get("non_ev_load_w", 0.0)))
    out: list[dict] = []
    for slot in sorted(count_by):
        samples = count_by[slot]
        coverage = min(1.0, samples / expected)
        flags = ["low_coverage"] if coverage < _LOW_COVERAGE else []
        out.append({
            "slot_start": slot.isoformat(),
            "mean_load_w": _mean(load_by[slot]),
            "mean_non_ev_load_w": _mean(nonev_by[slot]),
            "mean_solar_w": _mean(solar_by[slot]),
            "samples": samples,
            "coverage": coverage,
            "flags": flags,
        })
    return out


def aggregate_daily_energy(
    obs_rows: list[dict], raw_rows: list[dict], day_start: datetime, day_end: datetime,
    cadence_seconds: float = _DEFAULT_CADENCE_S,
) -> dict:
    """Pure: integrate one LOCAL day's observations + raw grid/battery into kWh node totals.
    `day_start`/`day_end` are tz-aware local-midnight bounds — their span is DST-correct (23h/25h
    on transition days), which the coverage denominator and slot count both inherit for free.

    Solar/load/non-EV come from `obs_rows` (already EV-excluded); grid/battery are aggregated from
    `raw_rows` with the fixed sign conventions (reporting.py / energy_flow.py): grid + = import /
    − = export; battery + = discharge / − = charge. Node totals only — no source→sink attribution
    (that's energy_flow.build_flows' job for the live Sankey; here we just need year-over-year kWh).
    """
    dh = _SLOT_HOURS
    solar_kwh = sum(float(o.get("mean_solar_w") or 0.0) for o in obs_rows) * dh / 1000.0
    load_kwh = sum(float(o.get("mean_load_w") or 0.0) for o in obs_rows) * dh / 1000.0
    non_ev_kwh = sum(float(o.get("mean_non_ev_load_w") or 0.0) for o in obs_rows) * dh / 1000.0
    ev_kwh = max(0.0, load_kwh - non_ev_kwh)
    samples = sum(int(o.get("samples") or 0) for o in obs_rows)

    grid_by: dict[datetime, list[float]] = defaultdict(list)
    batt_by: dict[datetime, list[float]] = defaultdict(list)
    for r in raw_rows:
        dt = _parse_utc(r.get("ts"))
        if dt is None:
            continue
        slot = _floor15(dt)
        grid_by[slot].append(float(r.get("grid_power_w", 0.0)))
        batt_by[slot].append(float(r.get("battery_power_w", 0.0)))
    grid_import = grid_export = batt_charge = batt_discharge = 0.0
    for xs in grid_by.values():
        g = _mean(xs)
        grid_import += max(0.0, g) * dh / 1000.0
        grid_export += max(0.0, -g) * dh / 1000.0
    for xs in batt_by.values():
        b = _mean(xs)
        batt_charge += max(0.0, -b) * dh / 1000.0
        batt_discharge += max(0.0, b) * dh / 1000.0

    # Convert to UTC before differencing: subtracting two datetimes that share the SAME tzinfo
    # object does NAIVE wall-clock arithmetic (always 24 h) and would silently drop the DST hour.
    # Via UTC the span is the true 23 h / 24 h / 25 h, which the coverage denominator inherits.
    span_s = (day_end.astimezone(UTC) - day_start.astimezone(UTC)).total_seconds()
    expected = max(1.0, span_s / max(float(cadence_seconds), 1.0))
    coverage = min(1.0, samples / expected)

    def r3(x: float) -> float:
        return round(x, 3) + 0.0  # +0.0 collapses -0.0

    return {
        "date": day_start.date().isoformat(),
        "solar_kwh": r3(solar_kwh), "load_kwh": r3(load_kwh),
        "non_ev_load_kwh": r3(non_ev_kwh), "ev_kwh": r3(ev_kwh),
        "grid_import_kwh": r3(grid_import), "grid_export_kwh": r3(grid_export),
        "battery_charge_kwh": r3(batt_charge), "battery_discharge_kwh": r3(batt_discharge),
        "coverage": round(coverage, 4),
    }


async def _materialize_observations(
    db: aiosqlite.Connection, start_iso: str, end_iso: str, cadence_seconds: float
) -> int:
    """Aggregate raw_samples+derived_samples in [start, end) into observation rows and upsert.
    Operates on the given connection WITHOUT committing — the caller owns the transaction (a
    migration backfill shares init()'s connection; the store wrapper opens its own + commits)."""
    cur = await db.execute(
        "SELECT ts, solar_power_w FROM raw_samples WHERE ts >= ? AND ts < ?", (start_iso, end_iso))
    raw_rows = [{"ts": t, "solar_power_w": s} for (t, s) in await cur.fetchall()]
    cur = await db.execute(
        "SELECT ts, house_load_w, non_ev_load_w FROM derived_samples WHERE ts >= ? AND ts < ?",
        (start_iso, end_iso))
    der_rows = [{"ts": t, "house_load_w": h, "non_ev_load_w": n} for (t, h, n) in
                await cur.fetchall()]
    # Score coverage at the window's OWN cadence (F4), falling back to the passed one when sparse.
    cadence = _infer_cadence_seconds(raw_rows, cadence_seconds)
    obs = aggregate_observations(raw_rows, der_rows, cadence)
    if not obs:
        return 0
    await db.executemany(
        "INSERT OR REPLACE INTO observations "
        "(slot_start, mean_load_w, mean_non_ev_load_w, mean_solar_w, samples, coverage, flags) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        [(o["slot_start"], o["mean_load_w"], o["mean_non_ev_load_w"], o["mean_solar_w"],
          o["samples"], o["coverage"], json.dumps(o["flags"])) for o in obs],
    )
    return len(obs)


async def _materialize_daily_energy(
    db: aiosqlite.Connection, day_start: datetime, day_end: datetime, cadence_seconds: float
) -> None:
    """Derive one local day's daily_energy row from observations (solar/load/non-EV/EV) + raw
    grid/battery, keyed by the LOCAL date `day_start.date()`. Upsert; no commit (caller owns it)."""
    su = day_start.astimezone(UTC).isoformat()
    eu = day_end.astimezone(UTC).isoformat()
    cur = await db.execute(
        "SELECT slot_start, mean_load_w, mean_non_ev_load_w, mean_solar_w, samples "
        "FROM observations WHERE slot_start >= ? AND slot_start < ?", (su, eu))
    obs_rows = [{"slot_start": a, "mean_load_w": b, "mean_non_ev_load_w": c, "mean_solar_w": d,
                 "samples": e} for (a, b, c, d, e) in await cur.fetchall()]
    cur = await db.execute(
        "SELECT ts, grid_power_w, battery_power_w FROM raw_samples WHERE ts >= ? AND ts < ?",
        (su, eu))
    raw_rows = [{"ts": t, "grid_power_w": g, "battery_power_w": bt} for (t, g, bt) in
                await cur.fetchall()]
    # Match the observation path: infer the day's real cadence for the coverage denominator (F4).
    cadence = _infer_cadence_seconds(raw_rows, cadence_seconds)
    d = aggregate_daily_energy(obs_rows, raw_rows, day_start, day_end, cadence)
    await db.execute(
        "INSERT OR REPLACE INTO daily_energy "
        "(date, solar_kwh, load_kwh, non_ev_load_kwh, ev_kwh, grid_import_kwh, grid_export_kwh, "
        "battery_charge_kwh, battery_discharge_kwh, coverage) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (d["date"], d["solar_kwh"], d["load_kwh"], d["non_ev_load_kwh"], d["ev_kwh"],
         d["grid_import_kwh"], d["grid_export_kwh"], d["battery_charge_kwh"],
         d["battery_discharge_kwh"], d["coverage"]),
    )


async def materialize_observations(
    store: HistoryStore, start: datetime, end: datetime, *,
    cadence_seconds: float = _DEFAULT_CADENCE_S,
) -> int:
    """Public wrapper: materialize observations for [start, end) (tz-aware datetimes) on the
    store's shared connection (write-serialized), committed. Idempotent upsert — safe to re-run
    for the same window."""
    async with store._write_conn() as db:
        n = await _materialize_observations(
            db, start.astimezone(UTC).isoformat(), end.astimezone(UTC).isoformat(), cadence_seconds)
        await db.commit()
        return n


async def materialize_daily_energy(
    store: HistoryStore, day_start: datetime, day_end: datetime, *,
    cadence_seconds: float = _DEFAULT_CADENCE_S,
) -> None:
    """Public wrapper: materialize the daily_energy row for the local day [day_start, day_end)
    (tz-aware local-midnight bounds) on the store's shared connection (write-serialized),
    committed. Idempotent upsert."""
    async with store._write_conn() as db:
        await _materialize_daily_energy(db, day_start, day_end, cadence_seconds)
        await db.commit()


async def _migrate_v1_observations(db: aiosqlite.Connection) -> None:
    """v1: create the observation store and backfill it from ALL existing raw history — on an
    upgrade, production's weeks of samples count as seasonal evidence from day one (bounded: a few
    weeks of 15-min slots is only ~thousands of rows). Backfill coverage assumes the default
    recorder cadence; the daily maintenance recomputes recent days at the true cadence."""
    await db.execute(_OBSERVATIONS_DDL)
    # Reset-clobber guard (F8): on an ABNORMAL user_version=0 DB whose observations table is
    # already populated (e.g. a stamp reset after the tables were built), the INSERT-OR-REPLACE
    # backfill would overwrite good rows with coarse UTC/default-cadence recomputes. Skip the
    # backfill when the table already holds data — the DDL above still ensures it EXISTS, and the
    # runner still stamps the version.
    cur = await db.execute("SELECT 1 FROM observations LIMIT 1")
    if await cur.fetchone() is not None:
        _log.info("observations already populated — skipping backfill")
        return
    await _materialize_observations(db, "0000", "9999", _DEFAULT_CADENCE_S)


async def _migrate_v2_daily_energy(db: aiosqlite.Connection) -> None:
    """v2: create the never-purged daily kWh rollup and backfill one row per UTC day that has
    observations. The storage layer has no site timezone at migration time, so DEEP backfill uses
    UTC day boundaries; the daily maintenance re-materializes recent days (yesterday) with the real
    site tz, so recent history is local-accurate and old history is UTC-approximate — the ≤2h
    midnight offset is immaterial for year-over-year kWh trends."""
    await db.execute(_DAILY_ENERGY_DDL)
    # Reset-clobber guard (F8): as in v1, skip the backfill when daily_energy already holds rows so
    # an abnormal user_version=0 DB with good data isn't overwritten by coarse UTC-day recomputes.
    cur = await db.execute("SELECT 1 FROM daily_energy LIMIT 1")
    if await cur.fetchone() is not None:
        _log.info("daily_energy already populated — skipping backfill")
        return
    cur = await db.execute("SELECT MIN(slot_start), MAX(slot_start) FROM observations")
    row = await cur.fetchone()
    if not row or row[0] is None:
        return
    first, last = _parse_utc(row[0]), _parse_utc(row[1])
    if first is None or last is None:
        return
    day = datetime(first.year, first.month, first.day, tzinfo=UTC)
    while day <= last:
        nxt = day + timedelta(days=1)
        await _materialize_daily_energy(db, day, nxt, _DEFAULT_CADENCE_S)
        day = nxt


async def _migrate_v3_forecast_ledger(db: aiosqlite.Connection) -> None:
    """v3: exact-`issued_at` prediction ledger (design §4.2/§4.3). Create the table + index, then
    copy the legacy date-keyed `forecast_snapshots` in as their nearest equivalent. Those were
    first-write-wins day-ahead SOLAR snapshots by design, so we map each to:

    * ``issued_at = issued_date || 'T00:00:00+00:00'`` — a DOCUMENTED approximation: the legacy
      rows carry only a DATE, never a real issue time. Downstream must treat these as approximate
      legacy provenance, never as a canonical 18:00 snapshot (design §4.5).
    * ``kind='solar'``; ``low/expected/high = p10/p50/p90``; ``source='legacy_snapshot'``;
      ``canonical=1`` (they were the day-ahead first-write-wins record — the nearest legacy
      equivalent of the new canonical snapshot).

    `forecast_snapshots` is left INTACT here (this migration only ADDS the ledger, never deletes
    the source table). The reconciliation iteration (design §3.3) separately retires the
    recorder's WRITE to that table (see `ems.sense.Recorder._persist_forecast`) now that every
    solar-accuracy reader scores the ledger's canonical rows instead — `forecast_snapshots` remains
    only as a read-only historic/migration-source table. Guarded by `has_table` so a truly ancient
    v0 DB without the snapshot table — and the migration-runner test harness, which builds only
    raw/derived — still migrates cleanly (INSERT..SELECT with no source table would raise)."""
    await db.execute(_FORECAST_LEDGER_DDL)
    await db.execute(_FORECAST_LEDGER_INDEX_DDL)
    if await has_table(db, "forecast_snapshots"):
        await db.execute(
            "INSERT OR IGNORE INTO forecast_ledger "
            "(issued_at, kind, target_start, low_w, expected_w, high_w, source, model_version, "
            "quality, canonical) "
            "SELECT issued_date || 'T00:00:00+00:00', 'solar', start, p10_w, p50_w, p90_w, "
            "'legacy_snapshot', NULL, NULL, 1 FROM forecast_snapshots"
        )


# Ordered migration registry (see storage/migrations.py). Append-only: never renumber or edit a
# shipped migration — add a new one. Each `apply` runs its DDL + bounded backfill in the runner's
# transaction and must not commit.
MIGRATIONS = [
    Migration(1, "compact 15-min observation store (design §4.1)", _migrate_v1_observations),
    Migration(2, "long-horizon daily kWh rollups (B-13)", _migrate_v2_daily_energy),
    Migration(3, "exact-issued_at prediction ledger (design §4.2/§4.3)",
              _migrate_v3_forecast_ledger),
]
LATEST_SCHEMA_VERSION = max(m.version for m in MIGRATIONS)


# --- Self-healing shared connection (BACKLOG B-49 follow-up) -----------------------------------
# B-49 gave each store ONE long-lived aiosqlite connection (perf). The failure this fixes: when the
# underlying connection / its worker thread dies, the OLD code kept returning the dead handle
# forever — every best-effort persist then failed (logged only) for HOURS until an app restart, and
# production silently lost raw samples across a day. These helpers let the store DETECT a dead
# connection and reopen it on the next call, so a transient death costs one cycle, not a whole day.


def _connection_is_dead(db: aiosqlite.Connection) -> bool:
    """Cheap O(1) liveness probe (NO query): aiosqlite sets `_running=False` when its worker thread
    has stopped and clears `_connection` when the sqlite3 handle is gone. Defensive getattr defaults
    (`True`/sentinel) treat an unexpected shape as ALIVE so we never churn a healthy connection."""
    return not getattr(db, "_running", True) or getattr(db, "_connection", True) is None


def is_dead_connection_error(exc: BaseException) -> bool:
    """True when `exc` means the shared aiosqlite connection is DEAD/unusable (→ discard + reopen),
    as opposed to transient contention (which a reheal would NOT fix). Enumerated CONSERVATIVELY
    from aiosqlite's failure modes (see aiosqlite/core.py) — shared by all three stores:

    * ``sqlite3.ProgrammingError`` containing "closed" — the underlying sqlite3 connection was
      closed out from under aiosqlite (our own ``__del__``, or an external close). The worker thread
      then raises "Cannot operate on a closed database." on every op until reopened.
    * ``ValueError`` "Connection closed" / "no active connection" — aiosqlite's OWN signals, raised
      by ``Connection._execute()`` (``not self._running or not self._connection``) and the ``_conn``
      property when its worker thread has stopped / the connection is gone. This is the exact
      "worker dies" case the long-lived connection never recovered from. Matched by MESSAGE so an
      unrelated ValueError (e.g. a JSON decode) can never be mistaken for a dead connection.
    * ``sqlite3.OperationalError`` ONLY for persistent, connection-fatal variants (can't open the db
      file, disk I/O error, malformed image, "not a database"). "database is locked"/"is busy" are
      DELIBERATELY EXCLUDED: those are transient contention that ``busy_timeout`` already handles,
      and churning the connection under load would make them worse, not better.
    """
    if isinstance(exc, sqlite3.ProgrammingError):
        return "closed" in str(exc).lower()
    if isinstance(exc, sqlite3.OperationalError):
        msg = str(exc).lower()
        if "locked" in msg or "busy" in msg:
            return False  # transient contention — busy_timeout's job, not a reheal's
        return any(s in msg for s in (
            "unable to open database file", "disk i/o error",
            "malformed", "not a database",
        ))
    if isinstance(exc, ValueError):
        msg = str(exc).lower()
        return "connection closed" in msg or "no active connection" in msg
    return False


# Public store methods that must NOT be auto-wrapped with the reheal-retry (startup/teardown, the
# reheal itself, and backup_to which manages its OWN short-lived connection).
_REHEAL_SKIP = frozenset({"init", "close", "reset_connection", "backup_to"})


def self_healing(cls):
    """Class decorator: wrap every PUBLIC async operation of a store so that a dead-connection error
    (see `is_dead_connection_error`) discards the shared connection, logs ONCE, and retries the
    operation EXACTLY ONCE — the connection seam reopens lazily on the retry. A second failure
    propagates (best-effort semantics stay; we never loop). Per-call overhead is a single
    try/except — no liveness/ping query. Methods in `_REHEAL_SKIP` and all `_private` methods are
    left untouched. Re-running a write on the retry is safe: a dead-connection error surfaces before
    any statement commits (commit is always last), so the failed attempt left nothing behind."""
    for name, fn in list(vars(cls).items()):
        if name.startswith("_") or name in _REHEAL_SKIP:
            continue
        if not asyncio.iscoroutinefunction(fn):
            continue

        def _make(method):
            @functools.wraps(method)
            async def _wrapped(self, *args, **kwargs):
                # Capture the connection this call is using BEFORE the op runs (B-49 follow-up race
                # fix): if a sibling coroutine failing on the SAME connection heals it first, our
                # discard below must target the connection WE used, not whatever is current — so we
                # never close a sibling's freshly-reopened handle (the thrash).
                conn = self._db
                try:
                    return await method(self, *args, **kwargs)
                except Exception as exc:
                    if not is_dead_connection_error(exc):
                        raise
                    await self._note_dead_connection(exc, conn)
                    return await method(self, *args, **kwargs)  # one retry; 2nd failure propagates

            return _wrapped

        setattr(cls, name, _make(fn))
    return cls


@self_healing
class HistoryStore:
    """Append-only history. `ts` is an ISO-8601 string; ordering uses rowid (insertion order)
    so it is correct regardless of timezone offset / DST in the `ts` text. WAL mode + a busy
    timeout let the recorder write concurrently with API reads without 'database is locked'."""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        # Long-lived connection (perf: BACKLOG B-49) — opened lazily on first use and kept for the
        # store's lifetime, instead of a fresh aiosqlite connection (its own worker thread) per
        # call. `_connect_lock` guards the lazy-open race (two first-callers can't each start a
        # connection); `_write_lock` serializes WRITES so a multi-statement write body (e.g.
        # record()'s raw+derived pair, or init()'s DDL) always runs through to its commit before
        # another write's statements can be interleaved on the SAME connection. aiosqlite already
        # serializes every individual statement through the connection's own single worker thread
        # (verified: `Connection` proxies all execute/commit calls onto one background Thread), so
        # this lock is purely an app-level "don't interleave two logical write transactions" guard,
        # not a driver-level race.
        #
        # Reads (`_conn()`) do NOT take the lock — a lone SELECT is trivially atomic on its own,
        # and requiring reads to queue behind writes would reintroduce the very serialization this
        # change is meant to avoid. The accepted trade-off: sharing one connection means a read CAN
        # now (same as any single SQLite connection sees its own uncommitted work) observe a
        # half-written multi-statement invariant if it lands between a write's own statements
        # (e.g. raw inserted, derived not yet). This is a narrow, pre-existing-in-spirit risk — the
        # previous per-call-connection design avoided it via full connection isolation — judged
        # acceptable here given reporting reads are windowed aggregates, not per-row invariants.
        self._db: aiosqlite.Connection | None = None
        self._connect_lock = asyncio.Lock()
        self._write_lock = asyncio.Lock()
        # Last time the shared connection was discarded + reopened (self-heal, B-49 follow-up).
        # None until the first reheal; surfaced on /api/diagnostics so a recovery is VISIBLE.
        self._last_reheal_at: datetime | None = None

    async def _connection(self) -> aiosqlite.Connection:
        """The shared connection, opened + configured on first use (idempotent, reopens lazily
        after `close()` or a self-heal). Proactively discards a connection whose aiosqlite worker
        thread has stopped / whose sqlite3 handle is gone — a cheap O(1) flag check (no query), so
        the common 'worker died' case reopens on the very next call without a failed attempt."""
        db = self._db
        if db is not None and _connection_is_dead(db):
            await self._discard_connection(db, reason="worker thread stopped / connection gone")
        if self._db is None:
            async with self._connect_lock:
                if self._db is None:
                    conn = aiosqlite.connect(self.db_path)
                    # Daemonize the worker thread BEFORE it's started (awaiting `conn` below starts
                    # it) — belt-and-suspenders with `__del__`'s direct close: a store that's never
                    # explicitly close()'d (common in ad-hoc scripts/tests) must never be able to
                    # keep the interpreter alive at exit.
                    conn._thread.daemon = True
                    db = await conn
                    await db.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
                    self._db = db
        return self._db

    async def _note_dead_connection(self, exc: BaseException, dead_conn) -> None:
        """Called by the `self_healing` retry wrapper: discard the dead connection `dead_conn` (the
        one the failing call was using, so the retry reopens) and log the incident once."""
        await self._discard_connection(dead_conn, reason=f"{type(exc).__name__}: {exc}")

    async def reset_connection(self) -> None:
        """Public: force the shared connection to be discarded now + reopened on the next call.
        Used by the recorder watchdog (`ems.sense`) after repeated persist failures. Idempotent
        and best-effort — never raises."""
        await self._discard_connection(self._db, reason="watchdog reset")

    async def _discard_connection(self, dead_conn, *, reason: str) -> None:
        """Drop the shared connection under `_connect_lock`, closing it best-effort. Logs ONCE per
        incident. `dead_conn` is the connection the caller was using: if a SIBLING coroutine that
        failed on the same connection already healed it (so `self._db` is now a DIFFERENT, healthy
        handle), this no-ops with a debug log rather than closing the sibling's fresh connection —
        the caller just retries on the current handle (B-49 follow-up race fix). `dead_conn is None`
        means the caller can't name its connection (a lazy-open first call, or the watchdog reset
        with nothing open yet) — then we fall back to discarding whatever is current."""
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
            except Exception:  # a dead connection may itself fail to close — that's fine
                pass

    def reheal_stats(self) -> dict:
        """Self-heal diagnostics (B-49 follow-up): {'last_reheal_iso': iso|None} — the last time the
        shared connection was discarded + reopened, or None if it never has been."""
        at = self._last_reheal_at
        return {"last_reheal_iso": at.isoformat() if at else None}

    @asynccontextmanager
    async def _conn(self):
        """Read seam: the shared connection, WITHOUT closing it (perf: B-49). Kept as the seam
        name so every existing read call site is unchanged."""
        yield await self._connection()

    @asynccontextmanager
    async def _write_conn(self):
        """Write seam: the same shared connection, serialized behind `_write_lock` (see the
        `__init__` docstring) so a multi-statement write body can't be torn by a concurrent one."""
        async with self._write_lock:
            yield await self._connection()

    async def close(self) -> None:
        """Close the shared connection (app shutdown). Safe to call when never opened, or twice."""
        async with self._connect_lock:
            if self._db is not None:
                await self._db.close()
                self._db = None

    def __del__(self) -> None:
        """Best-effort SYNCHRONOUS cleanup for callers that discard a store without awaiting
        close() (common in tests: create → use → let it fall out of scope). Deferring to
        aiosqlite's own async close() here would push the actual file close onto its background
        worker thread at an INDETERMINATE later time (possibly after this object's owning event
        loop has already closed) — observed to leave the db file's mtime touched well after the
        caller's last awaited call returned, which is surprising for anything that snapshots file
        state around an unrelated operation (e.g. a read-only replay's "never writes" guarantee).
        Closing the underlying sqlite3 connection directly here restores the OLD per-call-
        connection design's guarantee: nothing touches the file once the last awaited call has
        returned. Nulling `_connection` also makes aiosqlite's own `Connection.__del__` a silent
        no-op (no ResourceWarning, no redundant stop()); the worker thread is left idle on its
        queue, harmless since it was daemonized in `_connection()`."""
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
        # WAL allows concurrent reads during writes. Create both tables in one transaction
        # (single commit) so they appear atomically — a crash can't leave one without the other.
        async with self._write_conn() as db:
            await db.execute("PRAGMA journal_mode=WAL")
            # INCREMENTAL auto-vacuum lets maintenance reclaim space freed by retention purges
            # without a full table-locking VACUUM. Only takes effect on a fresh DB (set before the
            # first table AND before any header write); harmless no-op on an existing one (retention
            # still bounds row growth).
            await db.execute("PRAGMA auto_vacuum=INCREMENTAL")
            # "Existing history schema?" is keyed off raw_samples (our v0 ROOT table), NOT "any
            # table" — the SQLite file is shared with the audit/settings/cache stores, so their
            # tables must not make a brand-new history schema look already-migrated. An EXISTING
            # pre-runner DB gets its pending migrations applied HERE, before the idempotent baseline
            # below (see storage/migrations.py for the full fresh/existing contract). A DB WITHOUT
            # raw_samples skips migrations — the baseline builds every table — and is stamped to the
            # latest version AFTER the baseline (below), so a backfill never runs before its tables.
            existing = await has_table(db, "raw_samples")
            if existing:
                await run_migrations(db, MIGRATIONS, fresh=False)
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
            # DEPRECATED (reconciliation iteration, design §3.3): solar forecast snapshots — the
            # day-ahead P10/P50/P90 forecast for each 15-min slot, keyed by the date it was ISSUED.
            # No longer written by the recorder (see `ems.sense.Recorder._persist_forecast`); every
            # solar-accuracy reader now scores the prediction ledger's canonical rows instead (see
            # `forecast_ledger` below). Retained as a read-only historic/migration-source table
            # (migration v3 backfills it into the ledger) — never dropped, still purged with the
            # samples (bounded by slot `start`, like price_slots).
            await db.execute(
                "CREATE TABLE IF NOT EXISTS forecast_snapshots "
                "(issued_date TEXT NOT NULL, start TEXT NOT NULL, p10_w REAL NOT NULL, "
                "p50_w REAL NOT NULL, p90_w REAL NOT NULL, PRIMARY KEY (issued_date, start))"
            )
            # Plan/target history (observability-data): what the planner intended each cycle —
            # strategy, the target SoC it's aiming for + deadline, the resolved intent, and the
            # SoC observed at that moment — so a reviewer can later compare `target_soc` against
            # the achieved `soc_pct` in raw_samples. Recorded by the recorder, purged with samples.
            # `plan_version` + `floor_soc` back the intent-aware follow-through scorer
            # (ems.analysis.plan_execution_error): plan_version identifies the plan EPOCH a row
            # belongs to (so a superseded day-ahead commitment isn't scored against a later rolling
            # plan's SoC), and floor_soc is the reserve floor discharge/hold/self-consumption
            # deadlines are scored against (they intend SoC to fall, not reach the target).
            await db.execute(
                "CREATE TABLE IF NOT EXISTS plan_history "
                "(ts TEXT NOT NULL, strategy TEXT, target_soc REAL, deadline TEXT, "
                "soc_pct REAL, intent TEXT, plan_version TEXT, floor_soc REAL)"
            )
            await db.execute("CREATE INDEX IF NOT EXISTS idx_plan_ts ON plan_history(ts)")
            # Idempotent forward-compat for a plan_history built before those two columns existed:
            # ADD COLUMN only when missing (nullable, no backfill needed — the scorer falls back to
            # legacy grouping for rows that predate them). Cheaper than a versioned migration for a
            # pure nullable-column add, and safe on every boot.
            await self._add_missing_columns(
                db, "plan_history", {"plan_version": "TEXT", "floor_soc": "REAL"})
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
            # Compact 15-min observation store (design §4.1, 400-day horizon) + never-purged daily
            # kWh rollups (B-13). Part of the v0 baseline for a FRESH DB; on an EXISTING DB the
            # migrations above already created (and backfilled) them, so these are no-ops.
            await db.execute(_OBSERVATIONS_DDL)
            await db.execute(_DAILY_ENERGY_DDL)
            # Prediction ledger (design §4.2/§4.3): baseline for a FRESH DB; migration v3 already
            # created + backfilled it on an EXISTING DB, so these are no-ops there.
            await db.execute(_FORECAST_LEDGER_DDL)
            await db.execute(_FORECAST_LEDGER_INDEX_DDL)
            await db.commit()
            # Fresh DB: baseline just built the FULL current schema (with auto_vacuum latched), so
            # stamp straight to the latest version — the numbered migrations would only re-create
            # what exists and find no raw history to backfill.
            if not existing:
                await db.execute(f"PRAGMA user_version = {int(LATEST_SCHEMA_VERSION)}")
                await db.commit()

    @staticmethod
    async def _add_missing_columns(
        db: aiosqlite.Connection, table: str, columns: dict[str, str]
    ) -> None:
        """Idempotently ADD each `name -> sqlite_type` column that `table` doesn't already have —
        for nullable, no-backfill column additions to a table the baseline `CREATE TABLE IF NOT
        EXISTS` can't retro-add on an existing DB. Runs inside the init transaction (no commit);
        a no-op once the columns exist, so it's safe on every boot."""
        cur = await db.execute(f"PRAGMA table_info({table})")
        existing = {row[1] for row in await cur.fetchall()}
        for name, col_type in columns.items():
            if name not in existing:
                await db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {col_type}")

    async def purge_older_than(self, cutoff_iso: str) -> int:
        """Delete rows older than `cutoff_iso` (UTC-ISO) from BOTH sample tables atomically (one
        commit), so retention can never leave raw/derived out of sync. Returns total rows deleted.
        `ts` is UTC-ISO, so the lexicographic `<` is a correct time comparison."""
        async with atimed("store.history.write"):
            async with self._write_conn() as db:
                cur = await db.execute("DELETE FROM raw_samples WHERE ts < ?", (cutoff_iso,))
                deleted = cur.rowcount or 0
                cur = await db.execute("DELETE FROM derived_samples WHERE ts < ?", (cutoff_iso,))
                deleted += cur.rowcount or 0
                cur = await db.execute("DELETE FROM price_slots WHERE start_ts < ?", (cutoff_iso,))
                deleted += cur.rowcount or 0
                cur = await db.execute(
                    "DELETE FROM forecast_snapshots WHERE start < ?", (cutoff_iso,))
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
        async with self._write_conn() as db:
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

        async with atimed("store.history.read"):
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
        async with atimed("store.history.write"):
            async with self._write_conn() as db:
                await db.execute(
                    "INSERT INTO raw_samples "
                    "(ts, grid_power_w, solar_power_w, battery_power_w, ev_power_w, soc_pct) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (ts, raw.grid_power_w, raw.solar_power_w, raw.battery_power_w,
                     raw.ev_power_w, raw.soc_pct),
                )
                await db.execute(
                    "INSERT INTO derived_samples (ts, house_load_w, non_ev_load_w) "
                    "VALUES (?, ?, ?)",
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
        async with atimed("store.history.write"):
            async with self._write_conn() as db:
                await db.executemany(
                    "INSERT OR REPLACE INTO price_slots (start_ts, eur_per_kwh) "
                    "VALUES (?, ?)", slots)
                await db.commit()

    async def prices_between(self, start_iso: str, end_iso: str) -> list[dict]:
        """Stored price slots in [start, end), oldest-first (UTC-ISO ⇒ lexicographic = time)."""
        async with atimed("store.history.read"):
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
        """DEPRECATED (reconciliation iteration, design §3.3): nothing writes this anymore — the
        recorder now appends solar forecasts to the prediction ledger exclusively (see
        `ems.sense.Recorder._persist_forecast` / `ledger_append`). Kept only so the migration v3
        backfill (`_migrate_v3_forecast_ledger`) and direct tests of the legacy table still work;
        do not call this from new code.

        Record the day-ahead solar forecast for each slot, keyed by (issued_date, start).
        INSERT OR IGNORE: the FIRST snapshot recorded for a given (issued_date, slot) sticks — we
        want the day-ahead forecast, not a later-cycle nowcast overwriting it for error analysis."""
        if not slots:
            return
        async with atimed("store.history.write"):
            async with self._write_conn() as db:
                await db.executemany(
                    "INSERT OR IGNORE INTO forecast_snapshots "
                    "(issued_date, start, p10_w, p50_w, p90_w) VALUES (?, ?, ?, ?, ?)",
                    [(issued_date, start, p10, p50, p90) for start, p10, p50, p90 in slots],
                )
                await db.commit()

    async def forecasts_between(self, start_iso: str, end_iso: str) -> list[dict]:
        """DEPRECATED (reconciliation iteration, design §3.3): every live solar-accuracy reader now
        calls `ledger_canonical_between('solar', ...)` instead — this table is no longer written
        (see `upsert_forecast_snapshot`), so this helper only serves historic data recorded before
        the ledger existed and the migration v3 legacy-backfill path. Kept for that read, not for
        new scoring code.

        Stored forecast snapshots with slot `start` in [start, end), ordered by
        (issued_date, start) (UTC-ISO ⇒ lexicographic = time)."""
        async with atimed("store.history.read"):
            async with self._conn() as db:
                db.row_factory = aiosqlite.Row
                cur = await db.execute(
                    "SELECT issued_date, start, p10_w, p50_w, p90_w FROM forecast_snapshots "
                    "WHERE start >= ? AND start < ? ORDER BY issued_date ASC, start ASC",
                    (start_iso, end_iso))
                return [dict(r) for r in await cur.fetchall()]

    async def ledger_append(self, rows: list[tuple]) -> None:
        """Append prediction-ledger rows (design §4.2). Each row is the fixed tuple
        ``(issued_at, kind, target_start, low_w, expected_w, high_w, source, model_version,
        quality, canonical)`` (see `_LEDGER_COLS`). INSERT OR IGNORE ⇒ FIRST write per
        (issued_at, kind, target_start) wins — persist-before-outcome provenance is never mutated,
        and a canonical write can't be clobbered by a nowcast that happened to share an instant."""
        if not rows:
            return
        async with atimed("store.history.write"):
            async with self._write_conn() as db:
                await db.executemany(
                    f"INSERT OR IGNORE INTO forecast_ledger ({', '.join(_LEDGER_COLS)}) "
                    f"VALUES ({', '.join('?' for _ in _LEDGER_COLS)})",
                    rows,
                )
                await db.commit()

    async def ledger_canonical_between(
        self, kind: str, start_iso: str, end_iso: str
    ) -> list[dict]:
        """Canonical (`canonical=1`) ledger rows of `kind` with `target_start` in [start, end),
        oldest-first — the anti-leakage day-ahead snapshot the forecast scorer grades. Uses the
        (kind, canonical, target_start) index."""
        async with atimed("store.history.read"):
            async with self._conn() as db:
                db.row_factory = aiosqlite.Row
                cur = await db.execute(
                    f"SELECT {', '.join(_LEDGER_COLS)} FROM forecast_ledger "
                    "WHERE kind = ? AND canonical = 1 AND target_start >= ? AND target_start < ? "
                    "ORDER BY target_start ASC",
                    (kind, start_iso, end_iso))
                return [dict(r) for r in await cur.fetchall()]

    async def ledger_between(self, kind: str, start_iso: str, end_iso: str) -> list[dict]:
        """ALL ledger rows of `kind` with `target_start` in [start, end) — every issue time, not
        just canonical — ordered by (target_start, issued_at) for lead-time/nowcast diagnostics
        (UTC-ISO ⇒ lexicographic = time)."""
        async with atimed("store.history.read"):
            async with self._conn() as db:
                db.row_factory = aiosqlite.Row
                cur = await db.execute(
                    f"SELECT {', '.join(_LEDGER_COLS)} FROM forecast_ledger "
                    "WHERE kind = ? AND target_start >= ? AND target_start < ? "
                    "ORDER BY target_start ASC, issued_at ASC",
                    (kind, start_iso, end_iso))
                return [dict(r) for r in await cur.fetchall()]

    async def purge_ledger_older_than(
        self, cutoff_iso: str, *, nowcast_cutoff_iso: str | None = None
    ) -> int:
        """Delete ledger rows with `target_start` < `cutoff_iso` (the 400-day horizon), returning
        the row count. Purged by TARGET, symmetric with observations: a forecast whose target slot
        has aged past the observation horizon can no longer be scored, so it need not be kept.

        `nowcast_cutoff_iso` (differentiated retention): canonical=0 nowcast rows dominate the DB
        (~96 nowcasts per slot vs 1 canonical) but only the canonical rows are scored — nowcasts
        exist for lead-time diagnostics, so they get a much shorter horizon (60 days at the call
        site) keeping the ledger ~95% smaller without touching the evidence."""
        async with atimed("store.history.write"):
            async with self._write_conn() as db:
                cur = await db.execute(
                    "DELETE FROM forecast_ledger WHERE target_start < ?", (cutoff_iso,))
                n = cur.rowcount or 0
                if nowcast_cutoff_iso is not None:
                    cur = await db.execute(
                        "DELETE FROM forecast_ledger WHERE canonical = 0 AND target_start < ?",
                        (nowcast_cutoff_iso,))
                    n += cur.rowcount or 0
                await db.commit()
                return n

    async def record_plan(self, ts: str, snapshot: dict) -> None:
        """Append one plan/target history row (observability-data): what the planner intended
        THIS cycle. `snapshot` is the {"strategy","target_soc","deadline","soc_pct","intent"} dict
        assembled by the API's `_plan_snapshot`, OPTIONALLY carrying "plan_version" + "floor_soc"
        (the plan-epoch id + reserve floor the intent-aware follow-through scorer needs — see
        ems.analysis.plan_execution_error). Missing keys default to None (a partial or
        pre-commitment snapshot still records something rather than nothing; the scorer falls back
        to legacy grouping for rows without plan_version/floor_soc)."""
        async with atimed("store.history.write"):
            async with self._write_conn() as db:
                await db.execute(
                    "INSERT INTO plan_history "
                    "(ts, strategy, target_soc, deadline, soc_pct, intent, "
                    "plan_version, floor_soc) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (ts, snapshot.get("strategy"), snapshot.get("target_soc"),
                     snapshot.get("deadline"), snapshot.get("soc_pct"), snapshot.get("intent"),
                     snapshot.get("plan_version"), snapshot.get("floor_soc")),
                )
                await db.commit()

    async def plan_history_between(self, start_iso: str, end_iso: str) -> list[dict]:
        """Plan/target history rows with `ts` in [start, end), oldest-first (UTC-ISO ⇒
        lexicographic = time) — compare `target_soc` against raw_samples.soc_pct over time.
        `plan_version`/`floor_soc` are None on rows written before those columns existed."""
        async with atimed("store.history.read"):
            async with self._conn() as db:
                db.row_factory = aiosqlite.Row
                cur = await db.execute(
                    "SELECT ts, strategy, target_soc, deadline, soc_pct, intent, plan_version, "
                    "floor_soc FROM plan_history WHERE ts >= ? AND ts < ? ORDER BY ts ASC",
                    (start_iso, end_iso))
                return [dict(r) for r in await cur.fetchall()]

    async def record_gas(self, ts: str, total_gas_m3: float) -> None:
        """Upsert one cumulative gas meter reading (B-02). INSERT OR REPLACE: `ts` is the
        recorder's sense timestamp (one row/cycle in practice), so a re-record at the same `ts`
        (e.g. a retried cycle) simply overwrites rather than duplicating."""
        async with atimed("store.history.write"):
            async with self._write_conn() as db:
                await db.execute(
                    "INSERT OR REPLACE INTO gas_readings (ts, total_gas_m3) VALUES (?, ?)",
                    (ts, total_gas_m3))
                await db.commit()

    async def gas_between(self, start_iso: str, end_iso: str) -> list[dict]:
        """Gas meter readings with `ts` in [start, end), oldest-first (UTC-ISO ⇒
        lexicographic = time) — window consumption is the last reading minus the first."""
        async with atimed("store.history.read"):
            async with self._conn() as db:
                db.row_factory = aiosqlite.Row
                cur = await db.execute(
                    "SELECT ts, total_gas_m3 FROM gas_readings WHERE ts >= ? AND ts < ? "
                    "ORDER BY ts ASC",
                    (start_iso, end_iso))
                return [dict(r) for r in await cur.fetchall()]

    async def upsert_daily_finance(self, day: str, data: dict) -> None:
        """Store/replace one local day's finance rollup (day = YYYY-MM-DD, data = JSON-able)."""
        async with atimed("store.history.write"):
            async with self._write_conn() as db:
                await db.execute(
                    "INSERT OR REPLACE INTO daily_finance (day, data) VALUES (?, ?)",
                    (day, json.dumps(data)))
                await db.commit()

    async def daily_finance_between(self, start_day: str, end_day: str) -> list[dict]:
        """Finance rollups for days in [start, end) as {day, data}, oldest-first."""
        async with atimed("store.history.read"):
            async with self._conn() as db:
                db.row_factory = aiosqlite.Row
                cur = await db.execute(
                    "SELECT day, data FROM daily_finance WHERE day >= ? AND day < ? "
                    "ORDER BY day ASC", (start_day, end_day))
                return [
                    {"day": r["day"], "data": json.loads(r["data"])}
                    for r in await cur.fetchall()
                ]

    async def schema_version(self) -> int:
        """The DB's PRAGMA user_version (the applied migration level; see storage/migrations.py)."""
        async with atimed("store.history.read"):
            async with self._conn() as db:
                cur = await db.execute("PRAGMA user_version")
                return int((await cur.fetchone())[0])

    async def observations_between(self, start_iso: str, end_iso: str) -> list[dict]:
        """Compact 15-min observations with slot_start in [start, end), oldest-first (UTC-ISO ⇒
        lexicographic = time). `flags` is decoded from JSON to a list (a corrupt value ⇒ [])."""
        async with atimed("store.history.read"):
            async with self._conn() as db:
                db.row_factory = aiosqlite.Row
                cur = await db.execute(
                    "SELECT slot_start, mean_load_w, mean_non_ev_load_w, mean_solar_w, samples, "
                    "coverage, flags FROM observations WHERE slot_start >= ? AND slot_start < ? "
                    "ORDER BY slot_start ASC", (start_iso, end_iso))
                out = []
                for r in await cur.fetchall():
                    row = dict(r)
                    try:
                        row["flags"] = json.loads(row["flags"]) if row["flags"] else []
                    except (ValueError, TypeError):
                        row["flags"] = []
                    out.append(row)
                return out

    async def purge_observations_older_than(self, cutoff_iso: str) -> int:
        """Delete observations with slot_start < `cutoff_iso` (the 400-day horizon), returning the
        row count. Deliberately SEPARATE from purge_older_than: observations outlive the raw
        samples they were distilled from, and daily_energy is never purged at all."""
        async with atimed("store.history.write"):
            async with self._write_conn() as db:
                cur = await db.execute(
                    "DELETE FROM observations WHERE slot_start < ?", (cutoff_iso,))
                await db.commit()
                return cur.rowcount or 0

    async def daily_energy_between(self, start_date: str, end_date: str) -> list[dict]:
        """Daily kWh rollups (B-13) for local dates in [start, end) as dicts, oldest-first. Never
        purged, so this is the year-over-year record that survives the raw retention purge."""
        cols = ("date", "solar_kwh", "load_kwh", "non_ev_load_kwh", "ev_kwh", "grid_import_kwh",
                "grid_export_kwh", "battery_charge_kwh", "battery_discharge_kwh", "coverage")
        async with atimed("store.history.read"):
            async with self._conn() as db:
                db.row_factory = aiosqlite.Row
                cur = await db.execute(
                    f"SELECT {', '.join(cols)} FROM daily_energy WHERE date >= ? AND date < ? "
                    "ORDER BY date ASC", (start_date, end_date))
                return [dict(r) for r in await cur.fetchall()]

    async def upsert_carbon(self, rows: list[tuple[str, float]]) -> None:
        """Idempotently store (start_ts UTC-ISO, kg CO2/kWh) slots — re-fetches simply overwrite
        (roadmap F3, reporting-only)."""
        if not rows:
            return
        async with atimed("store.history.write"):
            async with self._write_conn() as db:
                await db.executemany(
                    "INSERT OR REPLACE INTO carbon_intensity (start_ts, kg_per_kwh) VALUES (?, ?)",
                    rows)
                await db.commit()

    async def carbon_between(self, start_iso: str, end_iso: str) -> list[dict]:
        """Stored carbon-intensity slots in [start, end), oldest-first (UTC-ISO ⇒
        lexicographic = time) — the Insights CO2 score averages these for a live window factor."""
        async with atimed("store.history.read"):
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
        async with atimed("store.history.write"):
            async with self._write_conn() as db:
                await db.execute(
                    "INSERT OR REPLACE INTO car_soc_anchor (key, value) VALUES (?, ?)",
                    (_CAR_SOC_ANCHOR_KEY, json.dumps({"pct": float(pct), "ts": ts})),
                )
                await db.commit()

    async def get_car_soc_anchor(self) -> tuple[float, str] | None:
        """The stored (pct, ts) anchor, or None if never set / the row is corrupt (never raises)."""
        async with atimed("store.history.read"):
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
        async with atimed("store.history.write"):
            async with self._write_conn() as db:
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
        async with atimed("store.history.write"):
            async with self._write_conn() as db:
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
        async with atimed("store.history.read"):
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
        async with atimed("store.history.read"):
            async with self._conn() as db:
                cur = await db.execute("SELECT COUNT(*) FROM notifications WHERE read = 0")
                return (await cur.fetchone())[0]

    async def mark_notifications_read(
        self, ids: list[int] | None = None, mark_all: bool = False
    ) -> int:
        """Mark notifications read: `mark_all=True` marks every currently-unread row; otherwise
        marks exactly the given `ids` (an id that doesn't exist is silently ignored). Returns the
        number of rows actually changed."""
        async with atimed("store.history.write"):
            async with self._write_conn() as db:
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
        async with atimed("store.history.read"):
            async with self._conn() as db:
                cur = await db.execute("SELECT name FROM sqlite_master WHERE type='table'")
                return {r[0] for r in await cur.fetchall()}
