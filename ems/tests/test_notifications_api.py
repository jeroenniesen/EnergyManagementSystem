"""B-20 notification API: GET /api/notifications (feed + unread count) and POST
/api/notifications/read (mark ids or all read). Auth-gated the same way as /api/override /
/api/settings — see test_auth.py for the shared middleware behaviour."""
import asyncio
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from ems.sources.mock import MockSource
from ems.storage.history import HistoryStore
from ems.web.api import create_app


def _app(tmp_path, **kw):
    return create_app(
        MockSource(), dry_run=True, dev_mode="mock",
        store=HistoryStore(str(tmp_path / "ems.sqlite")),
        **kw,
    )


def _ago(minutes: int) -> str:
    # The endpoint windows on [now - 30d, now) — seed relative to the REAL clock, not a fixed
    # calendar date, so the fixture stays inside that window regardless of when the suite runs.
    return (datetime.now(UTC) - timedelta(minutes=minutes)).isoformat()


def _seed(tmp_path, rows: list[tuple[int, str, str, str]]) -> None:
    """Write rows (minutes_ago, key, title, body) straight through a second HistoryStore handle
    onto the SAME sqlite file — the app's own store isn't reachable from the test process; this
    mirrors how a real notification source (e.g. the backup step) would have already written them
    before the dashboard polls."""

    async def run():
        store = HistoryStore(str(tmp_path / "ems.sqlite"))
        await store.init()
        for minutes_ago, key, title, body in rows:
            await store.add_notification(_ago(minutes_ago), key, title, body)

    asyncio.run(run())


def test_notifications_empty_by_default(tmp_path):
    with TestClient(_app(tmp_path)) as c:
        b = c.get("/api/notifications").json()
    assert b == {"items": [], "unread": 0}


def test_notifications_without_a_store_degrades_to_empty(tmp_path):
    with TestClient(create_app(MockSource(), dry_run=True, dev_mode="mock")) as c:
        b = c.get("/api/notifications").json()
    assert b == {"items": [], "unread": 0}


def test_notifications_feed_shows_newest_first_and_unread_count(tmp_path):
    app = _app(tmp_path)
    _seed(tmp_path, [
        (10, "backup_failed", "Backup failed", "first"),
        (5, "backup_failed", "Backup failed", "second"),
    ])
    with TestClient(app) as c:
        b = c.get("/api/notifications").json()
    assert b["unread"] == 2
    assert [i["body"] for i in b["items"]] == ["second", "first"]  # newest first


def test_notifications_limit_query_param(tmp_path):
    app = _app(tmp_path)
    _seed(tmp_path, [(60 - i, "k", "t", f"n{i}") for i in range(5)])
    with TestClient(app) as c:
        b = c.get("/api/notifications", params={"limit": 2}).json()
    assert len(b["items"]) == 2
    assert b["unread"] == 5


def test_mark_notifications_read_by_ids(tmp_path):
    app = _app(tmp_path)
    _seed(tmp_path, [
        (10, "k", "t", "a"),
        (5, "k", "t", "b"),
    ])
    with TestClient(app) as c:
        items = c.get("/api/notifications").json()["items"]
        first_id = items[-1]["id"]  # the older one ("a")
        r = c.post("/api/notifications/read", json={"ids": [first_id]})
        assert r.status_code == 200
        assert r.json()["unread"] == 1
        items_after = c.get("/api/notifications").json()["items"]
    read_flags = {i["id"]: i["read"] for i in items_after}
    assert read_flags[first_id] is True


def test_mark_notifications_read_all(tmp_path):
    app = _app(tmp_path)
    _seed(tmp_path, [
        (10, "k", "t", "a"),
        (5, "k", "t", "b"),
    ])
    with TestClient(app) as c:
        r = c.post("/api/notifications/read", json={"all": True})
        assert r.status_code == 200
        assert r.json()["unread"] == 0
        assert c.get("/api/notifications").json()["unread"] == 0


def test_mark_notifications_read_requires_token_when_configured(tmp_path):
    with TestClient(_app(tmp_path, web_auth_token="s3cret")) as c:
        assert c.post("/api/notifications/read", json={"all": True}).status_code == 401
        ok = c.post("/api/notifications/read", json={"all": True},
                    headers={"Authorization": "Bearer s3cret"})
        assert ok.status_code == 200


def test_get_notifications_is_open_even_with_token(tmp_path):
    with TestClient(_app(tmp_path, web_auth_token="s3cret")) as c:
        assert c.get("/api/notifications").status_code == 200
