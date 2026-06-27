import asyncio
from datetime import UTC, datetime

from ems.freshness import Freshness, FreshnessTracker
from ems.sense import SIGNALS, Recorder
from ems.sources.mock import MockSource
from ems.storage.history import HistoryStore

NOW = datetime(2026, 6, 27, 10, 0, tzinfo=UTC)


class _BoomSource:
    def read(self):
        raise RuntimeError("boom")


def test_sense_once_records_and_marks_fresh(tmp_path):
    store = HistoryStore(str(tmp_path / "ems.sqlite"))
    fresh = FreshnessTracker(stale_after_s=600)
    fresh.register(*SIGNALS)
    rec = Recorder(MockSource(), store, fresh)

    async def run():
        await store.init()
        await rec.sense_once(NOW)
        return await store.recent_raw(10)

    rows = asyncio.run(run())
    assert len(rows) == 1
    assert rows[0]["grid_power_w"] == 200
    for sig in SIGNALS:
        assert fresh.state(sig, NOW) is Freshness.FRESH


def test_record_now_writes_a_sample(tmp_path):
    store = HistoryStore(str(tmp_path / "ems.sqlite"))
    fresh = FreshnessTracker()
    fresh.register(*SIGNALS)
    rec = Recorder(MockSource(), store, fresh)

    async def run():
        await store.init()
        await rec.record_now()
        return await store.recent_raw(10)

    assert len(asyncio.run(run())) == 1


def test_run_records_each_cycle_then_stops(tmp_path):
    store = HistoryStore(str(tmp_path / "ems.sqlite"))
    fresh = FreshnessTracker()
    fresh.register(*SIGNALS)
    rec = Recorder(MockSource(), store, fresh, cycle_seconds=0.01)

    async def run():
        await store.init()
        stop = asyncio.Event()
        task = asyncio.create_task(rec.run(stop))
        await asyncio.sleep(0.06)  # ~several 10ms cycles
        stop.set()
        await task
        return await store.recent_raw(20)

    rows = asyncio.run(run())
    assert len(rows) >= 1  # the periodic loop recorded at least once


def test_run_survives_source_error(tmp_path):
    # Fail-safe: a raising source must not crash the recorder loop.
    store = HistoryStore(str(tmp_path / "ems.sqlite"))
    fresh = FreshnessTracker()
    fresh.register(*SIGNALS)
    rec = Recorder(_BoomSource(), store, fresh, cycle_seconds=0.01)

    async def run():
        await store.init()
        stop = asyncio.Event()
        task = asyncio.create_task(rec.run(stop))
        await asyncio.sleep(0.05)
        stop.set()
        await task  # must not raise
        return await store.recent_raw(10)

    rows = asyncio.run(run())
    assert rows == []  # nothing recorded, but the loop survived (task completed cleanly)
