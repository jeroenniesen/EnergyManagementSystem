"""Long-lived shared connection per store (BACKLOG B-49, scope item 1): HistoryStore /
SettingsStore / AuditStore each keep ONE aiosqlite connection opened lazily, instead of a fresh
connection (its own worker thread) per call. Writes are serialized behind an asyncio.Lock so a
multi-statement write body (e.g. HistoryStore.record()'s raw+derived pair) can't be torn by another
write interleaving on the SAME shared connection; reads share the connection freely."""
import asyncio
import logging
import sqlite3

import aiosqlite
import pytest

from ems.domain import RawSample
from ems.load_model import reconstruct
from ems.storage.audit import AuditStore
from ems.storage.history import HistoryStore, is_dead_connection_error
from ems.storage.settings import SettingsStore


def _count_connects(monkeypatch) -> dict:
    calls = {"n": 0}
    orig_connect = aiosqlite.connect

    def counting_connect(*a, **kw):
        calls["n"] += 1
        return orig_connect(*a, **kw)

    monkeypatch.setattr(aiosqlite, "connect", counting_connect)
    return calls


def test_history_store_opens_one_connection_for_many_calls(tmp_path, monkeypatch):
    calls = _count_connects(monkeypatch)
    store = HistoryStore(str(tmp_path / "ems.sqlite"))

    async def run():
        await store.init()
        raw = RawSample(grid_power_w=1, solar_power_w=0, battery_power_w=0, ev_power_w=0,
                        soc_pct=50)
        await store.record("2026-07-13T10:00:00+00:00", raw, reconstruct(raw))
        await store.record("2026-07-13T10:05:00+00:00", raw, reconstruct(raw))
        await store.recent_raw(10)
        await store.recent_derived(10)
        await store.purge_older_than("2000-01-01T00:00:00+00:00")
        await store.db_stats()

    asyncio.run(run())
    assert calls["n"] == 1  # init() + 6 more calls, but only ONE physical connection opened


def test_history_store_reconnects_lazily_after_close(tmp_path, monkeypatch):
    calls = _count_connects(monkeypatch)
    store = HistoryStore(str(tmp_path / "ems.sqlite"))

    async def run():
        await store.init()
        await store.close()
        await store.recent_raw(10)  # must transparently reopen

    asyncio.run(run())
    assert calls["n"] == 2


def test_history_store_close_is_safe_when_never_opened(tmp_path):
    store = HistoryStore(str(tmp_path / "ems.sqlite"))
    asyncio.run(store.close())  # must not raise


def test_history_store_write_conn_serializes_concurrent_writers(tmp_path):
    # Deterministic proof of write serialization: a slow writer holding `_write_conn()` blocks a
    # second writer's critical section from starting until the first's has fully exited.
    store = HistoryStore(str(tmp_path / "ems.sqlite"))
    order: list[str] = []

    async def first():
        async with store._write_conn():
            order.append("first-start")
            await asyncio.sleep(0.05)
            order.append("first-end")

    async def second():
        await asyncio.sleep(0.01)  # let `first` acquire the lock first
        async with store._write_conn():
            order.append("second-start")

    async def run():
        await store.init()
        await asyncio.gather(first(), second())

    asyncio.run(run())
    assert order == ["first-start", "first-end", "second-start"]


def test_history_store_reads_do_not_wait_on_the_write_lock(tmp_path):
    # Reads must stay cheap/concurrent even while a write is in flight (only writes serialize).
    store = HistoryStore(str(tmp_path / "ems.sqlite"))
    order: list[str] = []

    async def slow_write():
        async with store._write_conn():
            order.append("write-start")
            await asyncio.sleep(0.05)
            order.append("write-end")

    async def read():
        await asyncio.sleep(0.01)  # start while the write is still in flight
        order.append("read-start")
        await store.recent_raw(1)
        order.append("read-end")

    async def run():
        await store.init()
        await asyncio.gather(slow_write(), read())

    asyncio.run(run())
    # The read starts (and finishes) WHILE the write is still in flight — it never waited on the
    # write lock.
    assert order.index("read-start") < order.index("write-end")


def test_settings_store_opens_one_connection_for_many_calls(tmp_path, monkeypatch):
    calls = _count_connects(monkeypatch)
    store = SettingsStore(str(tmp_path / "ems.sqlite"))

    async def run():
        await store.init()
        await store.set_many({"a": 1})
        await store.set_many({"b": 2})
        await store.all()
        await store.delete("a")

    asyncio.run(run())
    assert calls["n"] == 1


def test_audit_store_opens_one_connection_for_many_calls(tmp_path, monkeypatch):
    calls = _count_connects(monkeypatch)
    store = AuditStore(str(tmp_path / "ems.sqlite"))

    async def run():
        await store.init()
        await store.append("2026-07-13T10:00:00+00:00", "config_change", "x", {})
        await store.append("2026-07-13T10:05:00+00:00", "config_change", "y", {})
        await store.recent(10)

    asyncio.run(run())
    assert calls["n"] == 1


# --- Self-healing shared connection (B-49 follow-up) -------------------------------------------
# The bug: when the long-lived aiosqlite connection / its worker thread dies, the OLD code kept
# returning the dead handle FOREVER — every persist failed (logged only) for hours until a restart.
# These tests kill the connection under a live store and prove the very next call recovers.


async def _new_store(Store, path):
    store = Store(path)
    await store.init()
    return store


async def _read(store):
    """One representative READ per store (opens/uses the shared connection)."""
    if isinstance(store, HistoryStore):
        return await store.recent_raw(1)
    if isinstance(store, SettingsStore):
        return await store.all()
    return await store.recent(1)  # AuditStore


async def _write(store):
    """One representative WRITE per store."""
    if isinstance(store, HistoryStore):
        raw = RawSample(grid_power_w=1, solar_power_w=0, battery_power_w=0, ev_power_w=0,
                        soc_pct=50)
        await store.record("2026-07-16T10:00:00+00:00", raw, reconstruct(raw))
    elif isinstance(store, SettingsStore):
        await store.set_many({"k": 1})
    else:
        await store.append("2026-07-16T10:00:00+00:00", "config_change", "x", {})


def test_dead_connection_predicate_enumerates_conservatively():
    # Dead → reheal.
    assert is_dead_connection_error(sqlite3.ProgrammingError("Cannot operate on a closed db."))
    assert is_dead_connection_error(ValueError("Connection closed"))
    assert is_dead_connection_error(ValueError("no active connection"))
    assert is_dead_connection_error(sqlite3.OperationalError("unable to open database file"))
    assert is_dead_connection_error(sqlite3.OperationalError("disk I/O error"))
    # NOT dead → must NOT reheal (transient contention busy_timeout handles, or unrelated errors).
    assert not is_dead_connection_error(sqlite3.OperationalError("database is locked"))
    assert not is_dead_connection_error(sqlite3.OperationalError("database is busy"))
    assert not is_dead_connection_error(sqlite3.IntegrityError("UNIQUE constraint failed"))
    assert not is_dead_connection_error(ValueError("Expecting value: line 1 column 1"))
    assert not is_dead_connection_error(RuntimeError("boom"))


@pytest.mark.parametrize("Store", [HistoryStore, SettingsStore, AuditStore])
def test_store_self_heals_read_path_after_worker_dies(tmp_path, Store, caplog):
    # Kill mode: stop the aiosqlite worker (its .close() sets _running=False / _connection=None) —
    # the proactive liveness check in _connection() should reopen on the very next READ.
    async def run():
        store = await _new_store(Store, str(tmp_path / "ems.sqlite"))
        await _read(store)  # prime the shared connection
        await store._db.close()  # underlying connection dies
        return await _read(store)  # must transparently re-heal + succeed

    with caplog.at_level(logging.WARNING, logger="ems.storage"):
        result = asyncio.run(run())
    assert result is not None  # the read succeeded on the reopened connection


@pytest.mark.parametrize("Store", [HistoryStore, SettingsStore, AuditStore])
def test_store_self_heals_write_path_after_worker_dies(tmp_path, Store):
    async def run():
        store = await _new_store(Store, str(tmp_path / "ems.sqlite"))
        await _write(store)  # prime
        await store._db.close()  # underlying connection dies
        await _write(store)  # must transparently re-heal + succeed (no raise)
        return store

    store = asyncio.run(run())
    assert store.reheal_stats()["last_reheal_iso"] is not None  # a reheal was recorded


@pytest.mark.parametrize("Store", [HistoryStore, SettingsStore, AuditStore])
def test_store_self_heals_when_raw_handle_closed_mid_flight(tmp_path, Store, caplog):
    # Kill mode: close ONLY the raw sqlite3 handle on its worker thread, leaving aiosqlite's flags
    # 'alive' — the proactive check can't see it, so the operation raises ProgrammingError and the
    # reactive `self_healing` retry (one retry) must catch it, reopen, and succeed on the SAME call.
    async def run():
        store = await _new_store(Store, str(tmp_path / "ems.sqlite"))
        await _read(store)  # prime
        # Run the raw close on the worker thread (check_same_thread), flags left intact.
        await store._db._execute(store._db._connection.close)
        return await _read(store)  # ProgrammingError → reheal + retry → success

    with caplog.at_level(logging.WARNING, logger="ems.storage"):
        result = asyncio.run(run())
    assert result is not None
    warnings = [r for r in caplog.records if "shared connection unusable" in r.getMessage()]
    assert len(warnings) == 1  # logged loudly, ONCE per incident (not per call)


def test_reheal_stats_none_until_first_reheal(tmp_path):
    async def run():
        store = await _new_store(HistoryStore, str(tmp_path / "ems.sqlite"))
        await _read(store)
        return store.reheal_stats()

    assert asyncio.run(run()) == {"last_reheal_iso": None}


@pytest.mark.parametrize("Store", [HistoryStore, SettingsStore, AuditStore])
def test_reset_connection_forces_reopen(tmp_path, Store, monkeypatch):
    # The watchdog hook: reset_connection() drops the shared connection so the next call reopens.
    calls = _count_connects(monkeypatch)

    async def run():
        store = await _new_store(Store, str(tmp_path / "ems.sqlite"))
        await _read(store)  # opens connection #1
        await store.reset_connection()
        await _read(store)  # opens connection #2

    asyncio.run(run())
    assert calls["n"] == 2


@pytest.mark.parametrize("Store", [HistoryStore, SettingsStore, AuditStore])
def test_lagging_sibling_discard_does_not_thrash_a_healed_connection(tmp_path, Store, caplog):
    # F1 (self-heal race): two coroutines both failed on connection X. Sibling A discards X and its
    # retry reopens Y. A LAGGING sibling B — which was also using X — then calls
    # _discard_connection(X): it must NO-OP (self._db is now the freshly-healed Y, not X) rather
    # than close Y and force a needless reopen (the thrash the old code caused). Exactly one
    # 'unusable' warning is logged for the incident (A's); B's stale discard is a debug no-op.
    async def run():
        store = await _new_store(Store, str(tmp_path / "ems.sqlite"))
        await _read(store)  # prime: open X
        x = store._db
        # Sibling A: X died under it → discard X, then its retry reopens a healthy Y.
        await store._discard_connection(x, reason="sibling A: dead connection")
        y = await store._connection()
        assert y is not x
        # Sibling B (lagging): still holds the stale X, calls discard — must leave the live Y alone.
        await store._discard_connection(x, reason="sibling B: stale handle")
        assert store._db is y  # Y untouched — B did NOT thrash the healed connection
        b_result = await _read(store)  # …and Y still serves B's retry
        return b_result

    with caplog.at_level(logging.WARNING, logger="ems.storage"):
        b_result = asyncio.run(run())
    assert b_result is not None
    warns = [r for r in caplog.records if "shared connection unusable" in r.getMessage()]
    assert len(warns) == 1  # one incident (A); B's stale discard never logged a second warning


@pytest.mark.parametrize("Store", [HistoryStore, AuditStore])
def test_store_close_allows_a_second_store_to_open_fresh(tmp_path, Store):
    # Sanity: closing one store's connection must not disturb a SECOND store instance pointed at
    # the same file (each store owns its own connection lifecycle).
    path = str(tmp_path / "ems.sqlite")
    store_a = Store(path)
    store_b = Store(path)

    async def run():
        await store_a.init()
        await store_a.close()
        await store_b.init()  # must still work fine against the same file

    asyncio.run(run())
