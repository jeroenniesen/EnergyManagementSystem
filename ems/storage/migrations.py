"""PRAGMA user_version ordered migration runner (BACKLOG B-52, second half).

Why this exists
---------------
The v0 schema was pure ``CREATE TABLE IF NOT EXISTS`` with no versioning, so the first
real ``ALTER``/new-table-with-backfill had nowhere to hang. This is a tiny ordered
migration runner keyed on SQLite's ``PRAGMA user_version``.

Fresh DB vs existing DB — the interplay with HistoryStore.init()'s baseline schema
----------------------------------------------------------------------------------
``HistoryStore.init()`` still owns a ``CREATE TABLE IF NOT EXISTS`` baseline that reflects
the FULL current schema — that baseline IS the v0 schema for a brand-new database. The
runner and the baseline cooperate like this:

* FRESH database (no user tables yet): there is nothing to migrate — the baseline builds
  every current table — so ``init()`` runs the baseline and then stamps
  ``user_version = LATEST_SCHEMA_VERSION`` directly, applying NO numbered migration.
  (Running the backfill migrations on a fresh DB would fail: they read raw-history tables
  that don't exist yet. The stamp is also deliberately done AFTER the baseline so that
  ``PRAGMA auto_vacuum`` can still latch on the empty file — writing ``user_version``
  before the first table silently disables it.)

* EXISTING pre-runner database (``user_version = 0`` with the v0 tables present): every
  pending migration is applied IN ORDER, each inside its OWN transaction, BEFORE the
  idempotent baseline (which is then a no-op for the tables it already has).
  ``user_version`` is bumped only after a migration body succeeds. A migration that raises
  leaves the DB at the last GOOD version — its own partial work is rolled back — and
  re-raises, so a half-migrated DB never silently serves and the next boot resumes cleanly.

* UP-TO-DATE / ahead database (``user_version >= LATEST_SCHEMA_VERSION``): a no-op (never
  downgrades).

Each migration is ``(version, description, apply)`` where ``apply(db)`` performs the schema
change AND any bounded backfill on the SAME connection. It MUST NOT commit — the runner
owns the transaction boundary so a failing backfill rolls back the schema change with it.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import aiosqlite

MigrationFn = Callable[[aiosqlite.Connection], Awaitable[None]]


@dataclass(frozen=True)
class Migration:
    """One ordered schema step. ``apply(db)`` runs the DDL + any backfill without committing."""

    version: int
    description: str
    apply: MigrationFn


async def _user_version(db: aiosqlite.Connection) -> int:
    cur = await db.execute("PRAGMA user_version")
    row = await cur.fetchone()
    return int(row[0]) if row else 0


async def has_table(db: aiosqlite.Connection, name: str) -> bool:
    """True if a table `name` exists. Callers detecting an EXISTING schema key off their OWN root
    table (e.g. HistoryStore keys off `raw_samples`), NOT "any table" — the SQLite file is shared
    by several stores (audit, settings, cache), so an unrelated store's table must not make a
    brand-new history schema look already-migrated (which would run a backfill before its baseline
    tables exist)."""
    cur = await db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1", (name,))
    return await cur.fetchone() is not None


async def has_user_tables(db: aiosqlite.Connection) -> bool:
    """True if the DB already has at least one user table — i.e. it is NOT brand-new. Used only by
    run_migrations' DEFAULT fresh detection; init() passes an explicit `fresh` keyed off its own
    root table instead (see has_table)."""
    cur = await db.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    )
    return int((await cur.fetchone())[0]) > 0


async def run_migrations(
    db: aiosqlite.Connection,
    migrations: list[Migration],
    *,
    fresh: bool | None = None,
) -> int:
    """Apply pending migrations in order and return the resulting user_version.

    See the module docstring for the fresh/existing/up-to-date contract. ``fresh`` overrides
    brand-new detection (tests + ``init()`` which controls stamp timing); by default a DB with
    no user tables is treated as fresh and merely stamped to the latest version.
    """
    latest = max((m.version for m in migrations), default=0)
    current = await _user_version(db)
    if current >= latest:
        return current  # up to date (or ahead) — never downgrade
    if fresh is None:
        fresh = not await has_user_tables(db)
    if current == 0 and fresh:
        # Nothing to migrate: the caller's baseline schema already built everything. Stamp only.
        await db.execute(f"PRAGMA user_version = {int(latest)}")
        await db.commit()
        return latest
    for m in sorted(migrations, key=lambda x: x.version):
        if m.version <= current:
            continue
        # Explicit transaction per step: on Python 3.12 sqlite3 the DDL + backfill live inside
        # this transaction, so a raising body rolls BOTH back and leaves user_version untouched
        # (we only bump it on success). Verified: a failed step leaves no partial table behind.
        await db.execute("BEGIN")
        try:
            await m.apply(db)
            await db.execute(f"PRAGMA user_version = {int(m.version)}")
            await db.commit()
        except Exception:
            await db.rollback()
            raise
        current = m.version
    return current
