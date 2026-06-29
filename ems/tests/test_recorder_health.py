"""Recorder health tracking (long-running review P1): a persistently failing store (full disk, DB
lock) must NOT kill the loop, and the failure must be visible via consecutive_failures/last_error
rather than only inferable from stale data."""
import asyncio

from ems.freshness import FreshnessTracker
from ems.sense import SIGNALS, Recorder
from ems.sources.mock import MockSource
from ems.storage.history import HistoryStore


def _tracker():
    fr = FreshnessTracker()
    fr.register(*SIGNALS)
    return fr


def test_successful_cycle_records_health(tmp_path):
    store = HistoryStore(str(tmp_path / "ems.sqlite"))
    rec = Recorder(MockSource(), store, _tracker(), cycle_seconds=999)

    async def go():
        await store.init()
        await rec.record_now()

    asyncio.run(go())
    assert rec.consecutive_failures == 0
    assert rec.last_success_at is not None and rec.last_error is None


def test_failing_store_keeps_loop_alive_and_is_visible(tmp_path):
    class _FailingStore(HistoryStore):
        async def record(self, *a, **k):
            raise RuntimeError("disk full")

    store = _FailingStore(str(tmp_path / "ems.sqlite"))
    rec = Recorder(MockSource(), store, _tracker(), cycle_seconds=0.01)

    async def go():
        await store.init()
        stop = asyncio.Event()
        task = asyncio.create_task(rec.run(stop))
        await asyncio.sleep(0.06)  # let it tick a few times
        stop.set()
        await task  # the loop must exit cleanly on stop, not have crashed

    asyncio.run(go())
    assert rec.consecutive_failures >= 1  # failures are counted...
    assert "disk full" in (rec.last_error or "")  # ...and the cause is recorded
    assert rec.last_success_at is None
    assert rec.health()["consecutive_failures"] == rec.consecutive_failures
