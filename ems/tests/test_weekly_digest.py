"""Sunday-evening weekly digest delivery (BACKLOG B-58): `_run_weekly_digest` is extracted as a
plain, directly-testable function (mirrors `_run_backup` — see test_backup.py) so the Sunday gate
and the dedupe are testable without running `_notify_loop`."""
import asyncio
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from ems.notify import Notifier
from ems.storage.cache import CacheStore
from ems.storage.history import HistoryStore
from ems.web.api import _run_weekly_digest

AMS = ZoneInfo("Europe/Amsterdam")

DIGEST = {
    "week_label": "Week of 2026-06-29",
    "saved_eur": 12.34,
    "best_day": {"date": "2026-07-02", "saved_eur": 3.0},
    "self_sufficiency_pct": 78.0,
    "solar_kwh": 24.5,
    "co2_avoided_note": "Avoided 62% of a no-solar home's CO₂ (12 kg vs 32 kg).",
    "actions": {"mode_switches": 3, "negative_soaks": 0, "overrides": 0},
    "tweak": "No tweak this week — settings look right.",
    "headline": "You saved €12.34 this week, ran 78% self-sufficient and the panels made "
                "24.5 kWh. Steady week — settings look right.",
    "days_measured": 7,
    "days_total": 7,
}

SUNDAY_EVENING = datetime(2026, 7, 5, 18, 30, tzinfo=AMS).astimezone(UTC)  # 2026-07-05 is a Sunday
SUNDAY_MORNING = datetime(2026, 7, 5, 9, 0, tzinfo=AMS).astimezone(UTC)
WEDNESDAY_EVENING = datetime(2026, 7, 8, 18, 30, tzinfo=AMS).astimezone(UTC)


def _make_store(tmp_path) -> HistoryStore:
    store = HistoryStore(str(tmp_path / "ems.sqlite"))
    asyncio.run(store.init())
    return store


def _make_cache(tmp_path) -> CacheStore:
    cache = CacheStore(str(tmp_path / "ems.sqlite"))
    cache.init()
    return cache


def _notifications(store: HistoryStore) -> list[dict]:
    return asyncio.run(store.notifications_between(
        "2020-01-01T00:00:00+00:00", "2030-01-01T00:00:00+00:00"))


def _counting_gather(calls: list):
    async def gather(monday):
        calls.append(monday)
        return dict(DIGEST)
    return gather


async def _boom_gather(monday):
    raise RuntimeError("gather failed")


def test_fires_on_sunday_evening_and_sends_the_notification(tmp_path):
    store = _make_store(tmp_path)
    cache = _make_cache(tmp_path)
    notifier = Notifier(store, {"notify.ntfy_url": "", "notify.ntfy_topic": ""})
    calls: list = []

    result = asyncio.run(_run_weekly_digest(
        store, cache, notifier, SUNDAY_EVENING, AMS, _counting_gather(calls)))

    assert result == DIGEST
    assert calls == [date(2026, 6, 29)]  # the just-completed week's Monday
    rows = _notifications(store)
    assert len(rows) == 1
    assert rows[0]["title"] == "Your week: saved €12.34"
    assert rows[0]["dedupe_key"] == "digest:Week of 2026-06-29"
    assert "You saved" in rows[0]["body"] and "Steady week" in rows[0]["body"]


def test_skips_before_18_local_on_sunday(tmp_path):
    store = _make_store(tmp_path)
    cache = _make_cache(tmp_path)
    notifier = Notifier(store, {"notify.ntfy_url": "", "notify.ntfy_topic": ""})
    calls: list = []

    result = asyncio.run(_run_weekly_digest(
        store, cache, notifier, SUNDAY_MORNING, AMS, _counting_gather(calls)))

    assert result is None
    assert calls == []
    assert _notifications(store) == []


def test_skips_on_a_non_sunday(tmp_path):
    store = _make_store(tmp_path)
    cache = _make_cache(tmp_path)
    notifier = Notifier(store, {"notify.ntfy_url": "", "notify.ntfy_topic": ""})
    calls: list = []

    result = asyncio.run(_run_weekly_digest(
        store, cache, notifier, WEDNESDAY_EVENING, AMS, _counting_gather(calls)))

    assert result is None
    assert calls == []
    assert _notifications(store) == []


def test_dedupes_a_repeat_call_the_same_process_without_recomputing(tmp_path):
    store = _make_store(tmp_path)
    cache = _make_cache(tmp_path)
    notifier = Notifier(store, {"notify.ntfy_url": "", "notify.ntfy_topic": ""})
    calls: list = []
    gather = _counting_gather(calls)

    first = asyncio.run(_run_weekly_digest(store, cache, notifier, SUNDAY_EVENING, AMS, gather))
    second = asyncio.run(_run_weekly_digest(store, cache, notifier, SUNDAY_EVENING, AMS, gather))

    assert first == DIGEST
    assert second is None
    assert len(calls) == 1  # gather was NOT called again for an already-sent week
    assert len(_notifications(store)) == 1


def test_dedupe_is_persisted_and_survives_a_fresh_cache_handle(tmp_path):
    # Simulates a process restart: a brand-new CacheStore instance pointed at the same db file
    # must still see the prior send (unlike an in-memory box such as `_backup_state`).
    store = _make_store(tmp_path)
    cache1 = _make_cache(tmp_path)
    notifier = Notifier(store, {"notify.ntfy_url": "", "notify.ntfy_topic": ""})
    asyncio.run(_run_weekly_digest(
        store, cache1, notifier, SUNDAY_EVENING, AMS, _counting_gather([])))

    cache2 = _make_cache(tmp_path)  # a fresh handle onto the SAME sqlite file
    calls: list = []
    result = asyncio.run(_run_weekly_digest(
        store, cache2, notifier, SUNDAY_EVENING, AMS, _counting_gather(calls)))

    assert result is None
    assert calls == []
    assert len(_notifications(store)) == 1


def test_a_new_week_sends_again_after_a_prior_weeks_dedupe(tmp_path):
    store = _make_store(tmp_path)
    cache = _make_cache(tmp_path)
    notifier = Notifier(store, {"notify.ntfy_url": "", "notify.ntfy_topic": ""})
    asyncio.run(_run_weekly_digest(
        store, cache, notifier, SUNDAY_EVENING, AMS, _counting_gather([])))

    next_sunday = datetime(2026, 7, 12, 18, 30, tzinfo=AMS).astimezone(UTC)

    async def gather_next_week(monday):
        return {**DIGEST, "week_label": "Week of 2026-07-06", "saved_eur": 5.0,
                "headline": "next week's headline", "tweak": DIGEST["tweak"]}

    result = asyncio.run(_run_weekly_digest(
        store, cache, notifier, next_sunday, AMS, gather_next_week))

    assert result is not None
    assert result["week_label"] == "Week of 2026-07-06"
    assert len(_notifications(store)) == 2


def test_gather_failure_is_swallowed_and_never_marks_the_week_sent(tmp_path):
    store = _make_store(tmp_path)
    cache = _make_cache(tmp_path)
    notifier = Notifier(store, {"notify.ntfy_url": "", "notify.ntfy_topic": ""})

    result = asyncio.run(_run_weekly_digest(
        store, cache, notifier, SUNDAY_EVENING, AMS, _boom_gather))

    assert result is None
    assert _notifications(store) == []
    assert cache.get("digest:last_week_sent") is None  # never marked sent — retried next cycle


def test_noop_without_a_store_or_notifier(tmp_path):
    cache = _make_cache(tmp_path)
    calls: list = []
    result = asyncio.run(_run_weekly_digest(
        None, cache, None, SUNDAY_EVENING, AMS, _counting_gather(calls)))
    assert result is None
    assert calls == []
