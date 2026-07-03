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


class _BoomStore:
    async def record(self, *_a, **_k):
        raise RuntimeError("disk full")


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


def test_run_survives_store_error():
    # Fail-safe: a store write failure (disk full, locked DB) must not crash the loop either.
    fresh = FreshnessTracker()
    fresh.register(*SIGNALS)
    rec = Recorder(MockSource(), _BoomStore(), fresh, cycle_seconds=0.01)

    async def run():
        stop = asyncio.Event()
        task = asyncio.create_task(rec.run(stop))
        await asyncio.sleep(0.04)
        stop.set()
        await task  # must not raise

    asyncio.run(run())  # completes cleanly == loop survived store errors


class _StubPrices:
    """Minimal price source: .slots() → objects with .start / .eur_per_kwh."""

    def __init__(self, slots):
        self._slots = slots

    def slots(self):
        return self._slots


class _BoomPrices:
    def slots(self):
        raise RuntimeError("tibber down")


def test_sense_once_persists_price_slots(tmp_path):
    # Spec 2026-07-03: each cycle upserts the current price curve so past slots keep their price.
    from ems.sources.prices import PriceSlot

    store = HistoryStore(str(tmp_path / "ems.sqlite"))
    fresh = FreshnessTracker()
    fresh.register(*SIGNALS)
    slots = [PriceSlot(NOW, 0.20), PriceSlot(NOW.replace(minute=15), 0.25)]
    rec = Recorder(MockSource(), store, fresh, price_source=_StubPrices(slots))

    async def run():
        await store.init()
        await rec.sense_once(NOW)
        await rec.sense_once(NOW)  # idempotent — same slots again
        return await store.prices_between("2020-01-01T00:00:00+00:00",
                                          "2030-01-01T00:00:00+00:00")

    rows = asyncio.run(run())
    assert [(r["start_ts"], r["eur_per_kwh"]) for r in rows] == [
        (NOW.isoformat(), 0.20), (NOW.replace(minute=15).isoformat(), 0.25)]


def test_price_persist_failure_never_kills_the_cycle(tmp_path):
    store = HistoryStore(str(tmp_path / "ems.sqlite"))
    fresh = FreshnessTracker()
    fresh.register(*SIGNALS)
    rec = Recorder(MockSource(), store, fresh, price_source=_BoomPrices())

    async def run():
        await store.init()
        await rec.sense_once(NOW)  # must not raise
        return await store.recent_raw(10)

    rows = asyncio.run(run())
    assert len(rows) == 1  # the sample was still recorded
