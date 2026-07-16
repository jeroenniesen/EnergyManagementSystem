"""Migration runner harness (BACKLOG B-52, second half): PRAGMA user_version ordered migrations,
fresh vs existing DB interplay, idempotence, and loud-failure with version left untouched."""
import asyncio

import aiosqlite
import pytest

from ems.storage.history import LATEST_SCHEMA_VERSION, HistoryStore
from ems.storage.migrations import Migration, has_user_tables, run_migrations


async def _make_v0_db(path: str, rows: list[tuple[str, dict]] | None = None) -> None:
    """Build a pre-runner v0 database: the v0 raw/derived tables + optional rows, user_version=0.
    Simulates an existing production DB created before the migration runner shipped."""
    async with aiosqlite.connect(path) as db:
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute(
            "CREATE TABLE raw_samples "
            "(ts TEXT NOT NULL, grid_power_w REAL NOT NULL, solar_power_w REAL NOT NULL, "
            "battery_power_w REAL NOT NULL, ev_power_w REAL NOT NULL, soc_pct REAL NOT NULL)")
        await db.execute(
            "CREATE TABLE derived_samples "
            "(ts TEXT NOT NULL, house_load_w REAL NOT NULL, non_ev_load_w REAL NOT NULL)")
        for ts, r in rows or []:
            house = r["grid"] + r["solar"] + r["battery"]
            non_ev = house - (r["ev"] if r["ev"] > 200.0 else 0.0)
            await db.execute(
                "INSERT INTO raw_samples VALUES (?,?,?,?,?,?)",
                (ts, r["grid"], r["solar"], r["battery"], r["ev"], r.get("soc", 50.0)))
            await db.execute(
                "INSERT INTO derived_samples VALUES (?,?,?)", (ts, house, non_ev))
        await db.commit()
        cur = await db.execute("PRAGMA user_version")
        assert (await cur.fetchone())[0] == 0  # v0: never stamped


def test_fresh_db_gets_full_schema_stamped_to_latest(tmp_path):
    # A brand-new DB is built entirely by init()'s baseline, then stamped to the latest version;
    # no numbered migration runs. Both new tables exist and are reachable.
    store = HistoryStore(str(tmp_path / "ems.sqlite"))

    async def run():
        await store.init()
        return await store.schema_version(), await store.table_names()

    version, names = asyncio.run(run())
    assert version == LATEST_SCHEMA_VERSION == 3
    assert "observations" in names and "daily_energy" in names
    assert "forecast_ledger" in names


def test_existing_v0_db_migrates_in_order_to_latest(tmp_path):
    # An existing pre-runner DB (raw rows present, user_version=0) applies the pending migrations
    # in order and ends at the latest version, with the new tables created.
    path = str(tmp_path / "ems.sqlite")

    async def run():
        await _make_v0_db(path, [("2026-07-10T10:00:00+00:00",
                                  {"grid": 300, "solar": 0, "battery": 0, "ev": 0})])
        store = HistoryStore(path)
        await store.init()
        return await store.schema_version(), await store.table_names()

    version, names = asyncio.run(run())
    assert version == 3
    assert "observations" in names and "daily_energy" in names
    assert "forecast_ledger" in names


def test_reinit_is_idempotent_applies_nothing(tmp_path):
    # Re-running init() on an already-migrated DB must be a no-op: version unchanged, no error,
    # and no duplicate/altered rows.
    path = str(tmp_path / "ems.sqlite")

    async def run():
        await _make_v0_db(path, [("2026-07-10T10:00:00+00:00",
                                  {"grid": 500, "solar": 1000, "battery": 0, "ev": 0})])
        store = HistoryStore(path)
        await store.init()
        v1 = await store.schema_version()
        obs1 = await store.observations_between("0000", "9999")
        await store.init()  # second boot
        v2 = await store.schema_version()
        obs2 = await store.observations_between("0000", "9999")
        return v1, v2, obs1, obs2

    v1, v2, obs1, obs2 = asyncio.run(run())
    assert v1 == v2 == 3
    assert obs1 == obs2  # backfilled rows unchanged by the second init


def test_failing_migration_raises_and_leaves_version_untouched(tmp_path):
    # A migration that raises mid-body must roll back its OWN partial work (no orphan table) and
    # leave user_version at the last GOOD step — a half-migrated DB must never silently serve.
    path = str(tmp_path / "ems.sqlite")

    async def ok(db):
        await db.execute("CREATE TABLE mig_ok (x TEXT)")

    async def boom(db):
        await db.execute("CREATE TABLE mig_bad (y TEXT)")  # rolled back with the transaction
        raise RuntimeError("deliberate migration failure")

    migrations = [Migration(1, "ok", ok), Migration(2, "boom", boom)]

    async def run():
        await _make_v0_db(path)  # existing (not fresh) so migrations actually run
        async with aiosqlite.connect(path) as db:
            with pytest.raises(RuntimeError, match="deliberate"):
                await run_migrations(db, migrations)
            cur = await db.execute("PRAGMA user_version")
            version = (await cur.fetchone())[0]
            cur = await db.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = {r[0] for r in await cur.fetchall()}
        return version, tables

    version, tables = asyncio.run(run())
    assert version == 1  # v1 committed, v2 failed → stuck at 1 (resumes here next boot)
    assert "mig_ok" in tables  # the successful step persisted
    assert "mig_bad" not in tables  # the failing step's partial DDL rolled back


def test_shared_db_with_other_store_tables_is_still_fresh_for_history(tmp_path):
    # Regression: the SQLite file is shared by the audit/settings/cache stores. A file that has
    # THEIR tables but no raw_samples must be treated as a FRESH history schema — the baseline
    # builds raw_samples et al. and the version is stamped, WITHOUT running a backfill migration
    # against tables that don't exist yet (which raised "no such table: raw_samples").
    path = str(tmp_path / "ems.sqlite")

    async def run():
        async with aiosqlite.connect(path) as db:
            await db.execute("CREATE TABLE audit_log (ts TEXT, kind TEXT)")  # another store's table
            await db.commit()
        store = HistoryStore(path)
        await store.init()  # must NOT raise
        return await store.schema_version(), await store.table_names()

    version, names = asyncio.run(run())
    assert version == LATEST_SCHEMA_VERSION
    assert "raw_samples" in names and "observations" in names and "daily_energy" in names
    assert "audit_log" in names  # the pre-existing table is untouched


def test_populated_tables_survive_backfill_on_abnormal_v0_db(tmp_path):
    # F8 (reset clobber): an ABNORMAL DB — user_version=0 but observations/daily_energy already
    # hold good rows (e.g. a stamp reset after populated tables) — must NOT have its data
    # INSERT-OR-REPLACE'd with coarse recomputes. Each backfill checks its target table first and
    # skips when non-empty, while still CREATE-IF-NOT-EXISTS + stamping to latest. The distinctive
    # pre-seeded rows (which no honest backfill of the raw sample below would ever produce) must
    # survive byte-identical.
    import aiosqlite as _aiosqlite  # local alias; module already imports aiosqlite at top

    path = str(tmp_path / "ems.sqlite")
    obs_row = ("2026-07-10T10:00:00+00:00", 7.0, 7.0, 42.0, 1, 0.5, "[]")
    daily_row = ("2026-07-10", 99.0, 88.0, 88.0, 0.0, 1.0, 2.0, 3.0, 4.0, 0.5)

    async def build_abnormal_v0():
        async with _aiosqlite.connect(path) as db:
            await db.execute("PRAGMA journal_mode=WAL")
            await db.execute(
                "CREATE TABLE raw_samples "
                "(ts TEXT NOT NULL, grid_power_w REAL NOT NULL, solar_power_w REAL NOT NULL, "
                "battery_power_w REAL NOT NULL, ev_power_w REAL NOT NULL, soc_pct REAL NOT NULL)")
            await db.execute(
                "CREATE TABLE derived_samples "
                "(ts TEXT NOT NULL, house_load_w REAL NOT NULL, non_ev_load_w REAL NOT NULL)")
            # A raw+derived sample for the SAME 10:00 slot: a backfill would compute mean_solar_w
            # 1000 and REPLACE the seeded 42.0 (proving clobber if the guard is absent).
            await db.execute("INSERT INTO raw_samples VALUES (?,?,?,?,?,?)",
                             ("2026-07-10T10:00:00+00:00", 300.0, 1000.0, 0.0, 0.0, 50.0))
            await db.execute("INSERT INTO derived_samples VALUES (?,?,?)",
                             ("2026-07-10T10:00:00+00:00", 1300.0, 1300.0))
            await db.execute(
                "CREATE TABLE observations "
                "(slot_start TEXT PRIMARY KEY, mean_load_w REAL, mean_non_ev_load_w REAL, "
                "mean_solar_w REAL, samples INTEGER, coverage REAL, flags TEXT NOT NULL "
                "DEFAULT '[]')")
            await db.execute("INSERT INTO observations VALUES (?,?,?,?,?,?,?)", obs_row)
            await db.execute(
                "CREATE TABLE daily_energy "
                "(date TEXT PRIMARY KEY, solar_kwh REAL, load_kwh REAL, non_ev_load_kwh REAL, "
                "ev_kwh REAL, grid_import_kwh REAL, grid_export_kwh REAL, battery_charge_kwh REAL, "
                "battery_discharge_kwh REAL, coverage REAL)")
            await db.execute("INSERT INTO daily_energy VALUES (?,?,?,?,?,?,?,?,?,?)", daily_row)
            await db.commit()
            cur = await db.execute("PRAGMA user_version")
            assert (await cur.fetchone())[0] == 0

    async def run():
        await build_abnormal_v0()
        store = HistoryStore(path)
        await store.init()  # migrations run; backfills must SKIP the already-populated tables
        obs = await store.observations_between("0000", "9999")
        daily = await store.daily_energy_between("0000", "9999")
        return await store.schema_version(), obs, daily

    version, obs, daily = asyncio.run(run())
    assert version == LATEST_SCHEMA_VERSION
    assert len(obs) == 1 and obs[0]["mean_solar_w"] == 42.0  # NOT clobbered to 1000 by a recompute
    assert obs[0]["samples"] == 1 and obs[0]["coverage"] == 0.5
    assert len(daily) == 1 and daily[0]["solar_kwh"] == 99.0  # daily_energy untouched too


def test_run_migrations_is_noop_when_up_to_date(tmp_path):
    # user_version already at/above latest ⇒ nothing runs (never downgrades).
    path = str(tmp_path / "ems.sqlite")

    async def bad(db):
        raise AssertionError("must not run when already up to date")

    async def run():
        async with aiosqlite.connect(path) as db:
            await db.execute("CREATE TABLE t (x)")
            await db.execute("PRAGMA user_version = 5")
            await db.commit()
            return await run_migrations(db, [Migration(1, "bad", bad)])

    assert asyncio.run(run()) == 5


def test_has_user_tables_distinguishes_fresh_from_existing(tmp_path):
    path = str(tmp_path / "ems.sqlite")

    async def run():
        async with aiosqlite.connect(path) as db:
            fresh = await has_user_tables(db)
            await db.execute("CREATE TABLE t (x)")
            await db.commit()
            existing = await has_user_tables(db)
        return fresh, existing

    fresh, existing = asyncio.run(run())
    assert fresh is False and existing is True
