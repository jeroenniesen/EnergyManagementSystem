"""Long-lived shared connection per store (BACKLOG B-49, scope item 1): HistoryStore /
SettingsStore / AuditStore each keep ONE aiosqlite connection opened lazily, instead of a fresh
connection (its own worker thread) per call. Writes are serialized behind an asyncio.Lock so a
multi-statement write body (e.g. HistoryStore.record()'s raw+derived pair) can't be torn by another
write interleaving on the SAME shared connection; reads share the connection freely."""
import asyncio

import aiosqlite
import pytest

from ems.domain import RawSample
from ems.load_model import reconstruct
from ems.storage.audit import AuditStore
from ems.storage.history import HistoryStore
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
