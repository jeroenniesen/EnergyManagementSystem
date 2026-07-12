"""BACKLOG B-75 wiring: `_run_detectors(store, notifier, now, **gathered)` runs the four pure
detectors (`ems/detectors.py`) against already-gathered plain data and fires any that trigger
through the real `Notifier` — mirrors `test_backup.py`'s pattern for `_run_backup`. Covers: one
detector raising must not block the others or propagate (fail-safe, CLAUDE.md); dedupe is proven
end-to-end through the real HistoryStore + Notifier (reusing test_notify.py's patterns); and the
store/notifier-absent no-op."""
import asyncio
from datetime import timedelta
from zoneinfo import ZoneInfo

from ems.notify import Notifier
from ems.sources.prices import PriceSlot
from ems.storage.history import HistoryStore
from ems.web import api as api_module
from ems.web.api import _run_detectors

AMS = ZoneInfo("Europe/Amsterdam")


def _store(tmp_path) -> HistoryStore:
    store = HistoryStore(str(tmp_path / "ems.sqlite"))
    asyncio.run(store.init())
    return store


def _notifications(store: HistoryStore) -> list[dict]:
    return asyncio.run(store.notifications_between(
        "2020-01-01T00:00:00+00:00", "2030-01-01T00:00:00+00:00"))


def _evening_now():
    from datetime import datetime
    return datetime(2026, 7, 12, 18, 0, tzinfo=AMS)


def _cheap_tomorrow_slots():
    from datetime import datetime
    tomorrow = datetime(2026, 7, 13, 0, 0, tzinfo=AMS)
    slots = [PriceSlot(start=tomorrow + timedelta(minutes=15 * i), eur_per_kwh=0.20)
             for i in range(96)]
    slots[10] = PriceSlot(start=tomorrow + timedelta(minutes=150), eur_per_kwh=-0.02)
    return slots


def test_run_detectors_skips_when_store_or_notifier_is_absent(tmp_path):
    store = _store(tmp_path)
    notifier = Notifier(store, {})
    now = _evening_now()

    asyncio.run(_run_detectors(None, notifier, now, price_slots_tomorrow=_cheap_tomorrow_slots()))
    asyncio.run(_run_detectors(store, None, now, price_slots_tomorrow=_cheap_tomorrow_slots()))

    assert _notifications(store) == []


def test_run_detectors_one_failing_detector_does_not_block_others_or_raise(tmp_path, monkeypatch):
    store = _store(tmp_path)
    notifier = Notifier(store, {"notify.ntfy_url": "", "notify.ntfy_topic": ""})

    def boom(*a, **kw):
        raise RuntimeError("bad forecast data")

    monkeypatch.setattr(api_module, "low_solar_tomorrow", boom)

    now = _evening_now()
    asyncio.run(_run_detectors(  # must not raise despite low_solar_tomorrow blowing up
        store, notifier, now,
        p50_by_slot_tomorrow={now: 100.0},  # would be routed to the now-boobytrapped detector
        typical_daily_kwh=10.0,
        price_slots_tomorrow=_cheap_tomorrow_slots(),  # a second, healthy detector
    ))

    rows = _notifications(store)
    assert len(rows) == 1
    assert rows[0]["key"] == "price_opportunity"  # the healthy detector still fired


def test_run_detectors_dedupes_across_two_runs_same_day(tmp_path):
    store = _store(tmp_path)
    notifier = Notifier(store, {"notify.ntfy_url": "", "notify.ntfy_topic": ""})
    now = _evening_now()
    slots = _cheap_tomorrow_slots()

    asyncio.run(_run_detectors(store, notifier, now, price_slots_tomorrow=slots))
    asyncio.run(_run_detectors(store, notifier, now, price_slots_tomorrow=slots))  # same day, again

    rows = _notifications(store)
    assert len(rows) == 1  # deduped — the second run is a no-op


def test_run_detectors_fires_multiple_independent_detectors_in_one_pass(tmp_path):
    store = _store(tmp_path)
    notifier = Notifier(store, {"notify.ntfy_url": "", "notify.ntfy_topic": ""})
    now = _evening_now()
    tomorrow = now.replace(hour=0) + timedelta(days=1)

    asyncio.run(_run_detectors(
        store, notifier, now,
        p50_by_slot_tomorrow={tomorrow.replace(hour=12): 400.0},  # tiny vs. typical -> grey day
        typical_daily_kwh=10.0,
        price_slots_tomorrow=_cheap_tomorrow_slots(),
        projected_soc_at_peak=20.0, needed_soc=50.0, confidence_level="high",
    ))

    keys = {r["key"] for r in _notifications(store)}
    assert keys == {"low_solar_tomorrow", "price_opportunity", "peak_risk"}
