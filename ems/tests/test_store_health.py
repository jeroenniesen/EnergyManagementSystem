"""Store-health escalation (B-49): `_run_store_health_check` alerts the operator when the recorder
has failed to persist for many consecutive cycles (the history store may be wedged). Module-level +
injected like `_run_backup`, so it's directly unit-testable with a canned recorder + notifier."""
import asyncio
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from ems.notify import Notifier
from ems.storage.history import HistoryStore
from ems.web.api import _run_store_health_check

AMS = ZoneInfo("Europe/Amsterdam")
NOON = datetime(2026, 7, 16, 12, 0, tzinfo=UTC)


class _Recorder:
    def __init__(self, streak):
        self._streak = streak

    def health(self):
        return {"consecutive_failures": self._streak}


def _store(tmp_path):
    store = HistoryStore(str(tmp_path / "ems.sqlite"))
    asyncio.run(store.init())
    return store


def _notifications(store):
    return asyncio.run(store.notifications_between(
        "2020-01-01T00:00:00+00:00", "2030-01-01T00:00:00+00:00"))


def test_no_alert_below_threshold(tmp_path):
    store = _store(tmp_path)
    notifier = Notifier(store, {"notify.ntfy_url": "", "notify.ntfy_topic": ""})
    state = {"alerted_date": None}
    asyncio.run(_run_store_health_check(_Recorder(9), notifier, NOON, AMS, state))
    assert _notifications(store) == []
    assert state["alerted_date"] is None


def test_alerts_once_at_threshold_then_dedupes_same_day(tmp_path):
    store = _store(tmp_path)
    notifier = Notifier(store, {"notify.ntfy_url": "", "notify.ntfy_topic": ""})
    state = {"alerted_date": None}

    asyncio.run(_run_store_health_check(_Recorder(10), notifier, NOON, AMS, state))
    asyncio.run(_run_store_health_check(_Recorder(11), notifier, NOON, AMS, state))  # same day

    rows = [r for r in _notifications(store) if r["key"] == "store_unhealthy"]
    assert len(rows) == 1
    assert state["alerted_date"] == "2026-07-16"


def test_no_recorder_or_no_notifier_is_a_no_op(tmp_path):
    store = _store(tmp_path)
    notifier = Notifier(store, {"notify.ntfy_url": "", "notify.ntfy_topic": ""})
    asyncio.run(_run_store_health_check(None, notifier, NOON, AMS, {"alerted_date": None}))
    asyncio.run(_run_store_health_check(_Recorder(99), None, NOON, AMS, {"alerted_date": None}))
    assert _notifications(store) == []


def test_alert_pushes_ntfy_even_when_the_store_is_dead(tmp_path):
    # The escalation must reach ntfy even though the in-app store write is exactly what's broken.
    import sqlite3

    class _DeadStore:
        async def add_notification(self, *a, **kw):
            raise sqlite3.ProgrammingError("Cannot operate on a closed database.")

    captured = {}

    def fake_post(url, data, headers):
        captured["url"] = url

    notifier = Notifier(
        _DeadStore(), {"notify.ntfy_url": "https://ntfy.sh", "notify.ntfy_topic": "hems"},
        post=fake_post,
    )
    asyncio.run(_run_store_health_check(_Recorder(10), notifier, NOON, AMS, {"alerted_date": None}))
    assert captured["url"] == "https://ntfy.sh/hems"  # pushed despite the dead store
