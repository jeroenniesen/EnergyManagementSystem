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
