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
