"""Notification outbox (B-20): `Notifier.send()` always stores (dedupe-aware) via HistoryStore,
and additionally pushes to ntfy when `notify.ntfy_url`/`notify.ntfy_topic` are both configured.
No live HTTP — the ntfy client is injected, mirroring test_carbon.py's ElectricityMaps pattern."""
import asyncio

from ems.notify import Notifier
from ems.storage.history import HistoryStore


def _store(tmp_path) -> HistoryStore:
    store = HistoryStore(str(tmp_path / "ems.sqlite"))
    asyncio.run(store.init())
    return store


def test_send_with_no_ntfy_config_stores_only(tmp_path):
    store = _store(tmp_path)
    calls = {"n": 0}

    def boom(url, data, headers):
        calls["n"] += 1
        raise AssertionError("must not be called when ntfy isn't configured")

    notifier = Notifier(store, {"notify.ntfy_url": "", "notify.ntfy_topic": ""}, post=boom)
    row_id = asyncio.run(notifier.send("backup_failed", "Backup failed", "body text"))

    assert row_id is not None
    assert calls["n"] == 0
    rows = asyncio.run(store.notifications_between(
        "2020-01-01T00:00:00+00:00", "2030-01-01T00:00:00+00:00"))
    assert rows[0]["delivered"] == ["in_app"]


def test_send_posts_to_ntfy_and_records_the_channel_on_success(tmp_path):
    store = _store(tmp_path)
    captured = {}

    def fake_post(url, data, headers):
        captured["url"] = url
        captured["data"] = data
        captured["headers"] = headers

    notifier = Notifier(
        store, {"notify.ntfy_url": "https://ntfy.sh", "notify.ntfy_topic": "my-topic"},
        post=fake_post,
    )
    row_id = asyncio.run(notifier.send("backup_failed", "Backup failed", "body text"))

    assert row_id is not None
    assert captured["url"] == "https://ntfy.sh/my-topic"
    assert captured["data"] == b"body text"
    assert captured["headers"]["Title"] == "Backup failed"
    rows = asyncio.run(store.notifications_between(
        "2020-01-01T00:00:00+00:00", "2030-01-01T00:00:00+00:00"))
    assert rows[0]["delivered"] == ["in_app", "ntfy"]


def test_send_ntfy_url_trailing_slash_is_normalised(tmp_path):
    store = _store(tmp_path)
    captured = {}

    def fake_post(url, data, headers):
        captured["url"] = url

    notifier = Notifier(
        store, {"notify.ntfy_url": "https://ntfy.example.com/", "notify.ntfy_topic": "t"},
        post=fake_post,
    )
    asyncio.run(notifier.send("k", "title", "body"))
    assert captured["url"] == "https://ntfy.example.com/t"


def test_send_ntfy_failure_is_logged_not_raised_and_channel_absent(tmp_path):
    store = _store(tmp_path)

    def boom(url, data, headers):
        raise RuntimeError("network down")

    notifier = Notifier(
        store, {"notify.ntfy_url": "https://ntfy.sh", "notify.ntfy_topic": "t"}, post=boom,
    )
    row_id = asyncio.run(notifier.send("backup_failed", "Backup failed", "body"))  # must not raise

    assert row_id is not None
    rows = asyncio.run(store.notifications_between(
        "2020-01-01T00:00:00+00:00", "2030-01-01T00:00:00+00:00"))
    assert rows[0]["delivered"] == ["in_app"]  # ntfy absent — the push failed


def test_send_only_url_or_only_topic_configured_skips_ntfy(tmp_path):
    store = _store(tmp_path)
    calls = {"n": 0}

    def boom(url, data, headers):
        calls["n"] += 1

    notifier = Notifier(store, {"notify.ntfy_url": "https://ntfy.sh", "notify.ntfy_topic": ""},
                        post=boom)
    asyncio.run(notifier.send("k", "title", "body"))
    assert calls["n"] == 0


def test_send_deduped_by_dedupe_key_skips_both_store_and_ntfy(tmp_path):
    store = _store(tmp_path)
    calls = {"n": 0}

    def fake_post(url, data, headers):
        calls["n"] += 1

    notifier = Notifier(
        store, {"notify.ntfy_url": "https://ntfy.sh", "notify.ntfy_topic": "t"}, post=fake_post,
    )
    first = asyncio.run(notifier.send("backup_failed", "t", "b", dedupe_key="backup_failed:day1"))
    second = asyncio.run(notifier.send("backup_failed", "t", "b", dedupe_key="backup_failed:day1"))

    assert first is not None
    assert second is None
    assert calls["n"] == 1  # the deduped second call never touches ntfy either


def test_settings_are_read_live_via_callable(tmp_path):
    # A callable settings source (mirrors the app's live settings_cache) is re-read on every
    # send — a config change applies to the NEXT call without rebuilding the Notifier.
    store = _store(tmp_path)
    cfg = {"notify.ntfy_url": "", "notify.ntfy_topic": ""}
    calls = {"n": 0}

    def fake_post(url, data, headers):
        calls["n"] += 1

    notifier = Notifier(store, lambda: cfg, post=fake_post)
    asyncio.run(notifier.send("k", "t", "b1"))
    assert calls["n"] == 0

    cfg["notify.ntfy_url"] = "https://ntfy.sh"
    cfg["notify.ntfy_topic"] = "topic"
    asyncio.run(notifier.send("k", "t", "b2"))
    assert calls["n"] == 1


def test_send_store_failure_is_swallowed_not_raised(tmp_path):
    class _BoomStore:
        async def add_notification(self, *a, **kw):
            raise OSError("disk full")

    notifier = Notifier(_BoomStore(), {"notify.ntfy_url": "", "notify.ntfy_topic": ""})
    row_id = asyncio.run(notifier.send("k", "t", "b"))  # must not raise
    assert row_id is None


class _DeadStore:
    """A store whose in-app write always fails — the wedged history store B-49 alerts on."""

    async def add_notification(self, *a, **kw):
        import sqlite3
        raise sqlite3.ProgrammingError("Cannot operate on a closed database.")

    async def set_notification_delivered(self, *a, **kw):
        raise AssertionError("must not be called when there is no stored row")


def test_send_pushes_ntfy_even_when_store_write_fails_for_targeted_key():
    # B-49: the `store_unhealthy` alert exists because the store is dead — so with the targeted
    # push_even_if_store_fails flag the ntfy push STILL goes out though the in-app write failed.
    captured = {}

    def fake_post(url, data, headers):
        captured["url"] = url
        captured["data"] = data

    notifier = Notifier(
        _DeadStore(), {"notify.ntfy_url": "https://ntfy.sh", "notify.ntfy_topic": "t"},
        post=fake_post,
    )
    row_id = asyncio.run(notifier.send(
        "store_unhealthy", "title", "body", push_even_if_store_fails=True))

    assert row_id is None  # nothing was stored (no row id)
    assert captured["url"] == "https://ntfy.sh/t"  # but the push still fired
    assert captured["data"] == b"body"


def test_send_without_the_flag_does_not_push_when_store_fails():
    # Default behaviour is unchanged: a failed store write short-circuits the push too.
    calls = {"n": 0}

    def fake_post(url, data, headers):
        calls["n"] += 1

    notifier = Notifier(
        _DeadStore(), {"notify.ntfy_url": "https://ntfy.sh", "notify.ntfy_topic": "t"},
        post=fake_post,
    )
    row_id = asyncio.run(notifier.send("k", "t", "b"))  # no flag
    assert row_id is None
    assert calls["n"] == 0
