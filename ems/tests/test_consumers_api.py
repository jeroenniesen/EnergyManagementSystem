"""#224 — GET /api/consumers read-only insight contract."""
import asyncio
import json
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import FastAPI
from fastapi.testclient import TestClient

from ems.consumers.mock import MockConsumerSource
from ems.consumers.selection import MOCK_DEVICE_IDS_KEY
from ems.consumers.start_advice import ACCEPTED_WINDOWS_KEY
from ems.sources.mock import MockSource
from ems.storage.history import HistoryStore
from ems.storage.settings import SettingsStore
from ems.web.api import create_app
from ems.web.routes.consumers import build_router, gather_consumers

# Optional create_app kwargs for tests in this module. Cleared by the test that sets it.
_APP_EXTRA: dict = {}


def _app(tmp_path, *, sources=None, dry_run=True):
    db = str(tmp_path / "ems.sqlite")
    kwargs = {}
    if sources is not None:
        kwargs["consumer_sources"] = sources
    kwargs.update(_APP_EXTRA)
    return create_app(
        MockSource(),
        dry_run=dry_run,
        config_dry_run=False,
        dev_mode="mock",
        store=HistoryStore(db),
        settings_store=SettingsStore(db),
        **kwargs,
    )


def test_consumers_empty_when_nothing_opted_in(tmp_path):
    with TestClient(_app(tmp_path)) as c:
        r = c.get("/api/consumers")
        assert r.status_code == 200
        body = r.json()
    assert body["read_only"] is True
    assert body["title"] == "Appliances"
    assert body["devices"] == []
    assert "selected" in body["summary"].lower()


def test_consumers_lists_opted_in_mock_devices(tmp_path):
    with TestClient(_app(tmp_path)) as c:
        assert c.post(
            "/api/settings",
            json={MOCK_DEVICE_IDS_KEY: '["mock-washer-1"]'},
        ).status_code == 200
        body = c.get("/api/consumers").json()
    assert len(body["devices"]) == 1
    d = body["devices"][0]
    assert d["id"] == "mock-washer-1"
    assert d["name"] == "Mock washer"
    assert d["kind"] == "washer"
    assert d["opted_in"] is True
    assert d["status"]["state"] == "idle"
    assert "Idle" in d["status"]["state_label"]
    assert d["status"]["power_w"] == 0.0
    # Tracebacks must never appear in homeowner copy.
    assert "Traceback" not in body["summary"]
    assert "Traceback" not in (d["status"].get("detail") or "")


def test_consumers_no_sources_honest_summary(tmp_path):
    with TestClient(_app(tmp_path, sources=[])) as c:
        body = c.get("/api/consumers").json()
    assert body["devices"] == []
    assert "connected" in body["summary"].lower()


def test_gather_consumers_offline_when_status_missing():
    class _Silent:
        integration = "mock"

        def list_devices(self):
            from ems.consumers.models import ConsumerCapabilities, ConsumerDevice, ConsumerKind
            return [
                ConsumerDevice(
                    id="x", name="X", kind=ConsumerKind.OTHER, integration="mock",
                    capabilities=ConsumerCapabilities(), opted_in=True,
                ),
            ]

        def get_status(self, device_id: str):
            return None

        def set_settings(self, settings):
            return None

    # gather_consumers re-applies apply_opt_in from settings — pre-marked opted_in alone
    # is not enough; the device must be in the settings selection list.
    body = gather_consumers(
        [_Silent()],  # type: ignore[list-item]
        {MOCK_DEVICE_IDS_KEY: '["x"]'},
    )
    assert len(body["devices"]) == 1
    assert body["devices"][0]["status"]["state"] == "offline"
    label = body["devices"][0]["status"]["state_label"].lower()
    assert "can’t reach" in label or "cant reach" in label
    assert body["devices"][0]["status"]["detail"] == (
        "No status from the integration right now."
    )


def test_gather_consumers_ignores_self_marked_opt_in_without_settings():
    """Adapter that sets opted_in=True must not bypass the settings selection list."""
    class _SelfMarked:
        integration = "mock"

        def list_devices(self):
            from ems.consumers.models import ConsumerCapabilities, ConsumerDevice, ConsumerKind
            return [
                ConsumerDevice(
                    id="sneaky", name="Sneaky", kind=ConsumerKind.OTHER, integration="mock",
                    capabilities=ConsumerCapabilities(), opted_in=True,
                ),
            ]

        def get_status(self, device_id: str):
            return None

        def set_settings(self, settings):
            return None

    body = gather_consumers([_SelfMarked()], {})  # type: ignore[list-item]
    assert body["devices"] == []
    assert "selected" in body["summary"].lower()


def test_consumers_path_has_no_write_methods():
    src = MockConsumerSource()
    assert not hasattr(src, "apply")
    assert not hasattr(src, "start")


def test_consumers_live_defaults_to_empty_sources(tmp_path):
    db = str(tmp_path / "ems.sqlite")
    app = create_app(
        MockSource(),
        dry_run=True,
        config_dry_run=False,
        dev_mode="live",
        store=HistoryStore(db),
        settings_store=SettingsStore(db),
    )
    with TestClient(app) as c:
        body = c.get("/api/consumers").json()
    assert body["devices"] == []
    assert "connected" in body["summary"].lower()


def test_gather_list_failure_honest_summary():
    class _Boom:
        integration = "mock"

        def list_devices(self):
            raise RuntimeError("cloud down")

        def get_status(self, device_id: str):
            return None

        def set_settings(self, settings):
            return None

    body = gather_consumers([_Boom()], {})  # type: ignore[list-item]
    assert body["devices"] == []
    summary = body["summary"].lower()
    assert "can’t reach" in summary or "cant reach" in summary
    assert "appliance integration" in summary


# --- #334 accept / cancel ----------------------------------------------------

_WHEN = datetime(2026, 7, 11, 12, 0, tzinfo=UTC)
_SLOT = timedelta(minutes=15)


class _MemCache:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def get(self, key: str):
        return self.values.get(key)

    def set(self, key: str, value: str, ttl_seconds: float) -> None:
        self.values[key] = value
        self.ttl = ttl_seconds

    def _delete_key(self, key: str) -> None:
        self.values.pop(key, None)


class _Audit:
    def __init__(self) -> None:
        self.rows: list[tuple] = []

    async def append(self, ts, category, summary, detail=None):
        self.rows.append((category, summary, detail))


class _Price:
    def __init__(self, start: datetime, eur: float) -> None:
        self.start = start
        self.eur_per_kwh = eur


class _Prices:
    def __init__(self, slots) -> None:
        self._slots = slots

    def slots(self):
        return list(self._slots)


def _quarter_hours(start: datetime, count: int) -> list[_Price]:
    return [_Price(start + i * _SLOT, 0.10) for i in range(count)]


def _asgi(app, method: str, path: str, body: dict | None, token: str | None = None):
    payload = b"" if body is None else json.dumps(body).encode()
    headers = []
    if body is not None:
        headers.append((b"content-type", b"application/json"))
    if token:
        headers.append((b"authorization", f"Bearer {token}".encode()))
    status = {"code": 0}
    chunks: list[bytes] = []

    async def receive():
        return {"type": "http.request", "body": payload, "more_body": False}

    async def send(message):
        if message["type"] == "http.response.start":
            status["code"] = message["status"]
        elif message["type"] == "http.response.body":
            chunks.append(message.get("body") or b"")

    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": headers,
        "client": ("127.0.0.1", 1234),
        "server": ("test", 80),
    }

    async def run():
        await app(scope, receive, send)

    asyncio.run(run())
    raw = b"".join(chunks)
    return status["code"], (json.loads(raw) if raw else {})


def _advice_app(cache, audit):
    prices = _quarter_hours(_WHEN - timedelta(hours=12), 24 * 4)
    ctx = type("Ctx", (), {})()
    ctx.settings_cache = {MOCK_DEVICE_IDS_KEY: '["mock-washer-1"]'}
    ctx.cache_store = cache
    ctx.audit_store = audit
    ctx.price_source = _Prices(prices)
    ctx.site_tz = ZoneInfo("UTC")
    app = FastAPI()
    app.include_router(build_router(
        ctx,
        sources=[MockConsumerSource()],
        now_utc=lambda: _WHEN,
    ))
    return app


def _window(start: datetime, slots: int = 4, declared: int | None = 4) -> dict:
    body = {
        "start": start.isoformat(),
        "end": (start + slots * _SLOT).isoformat(),
    }
    if declared is not None:
        body["slots"] = declared
    return body


def test_accept_bumps_exact_watts_and_cancel_audits(tmp_path):
    del tmp_path
    cache = _MemCache()
    audit = _Audit()
    app = _advice_app(cache, audit)
    start = _WHEN + timedelta(hours=1)
    code, body = _asgi(app, "POST", "/api/consumers/advice/accept", {
        "device_id": "mock-washer-1",
        "window": _window(start),
    })
    assert code == 200
    assert body["assumed_kwh"] == 1.0
    assert body["slots"] == 4
    assert body["bump_w"] == 1000.0  # 1.0 kWh / 4 slots / 0.25 h × 1000
    assert audit.rows[0][0] == "consumer_advice_accepted"
    stored = json.loads(cache.values[ACCEPTED_WINDOWS_KEY])
    assert stored["mock-washer-1"]["assumed_kwh"] == 1.0
    assert cache.ttl > 0

    advice_code, advice = _asgi(app, "GET", "/api/consumers/start-advice", None)
    assert advice_code == 200
    tip = advice["advice"][0]
    assert tip["device_id"] == "mock-washer-1"
    assert tip["accepted"] is True

    cancel_code, cancelled = _asgi(app, "DELETE", "/api/consumers/advice/accept", {
        "device_id": "mock-washer-1",
    })
    assert cancel_code == 200
    assert cancelled["ok"] is True
    assert audit.rows[1][0] == "consumer_advice_cancelled"
    assert ACCEPTED_WINDOWS_KEY not in cache.values
    again_code, again = _asgi(app, "GET", "/api/consumers/start-advice", None)
    assert again_code == 200
    assert again["advice"][0]["accepted"] is False


def test_accept_rejects_past_horizon_and_unknown_device():
    cache = _MemCache()
    app = _advice_app(cache, _Audit())
    past = _asgi(app, "POST", "/api/consumers/advice/accept", {
        "device_id": "mock-washer-1",
        "window": _window(_WHEN - timedelta(hours=2)),
    })
    assert past[0] == 422
    assert "past" in past[1]["detail"].lower()

    beyond = _asgi(app, "POST", "/api/consumers/advice/accept", {
        "device_id": "mock-washer-1",
        "window": _window(_WHEN + timedelta(days=5)),
    })
    assert beyond[0] == 422
    assert "horizon" in beyond[1]["detail"].lower()

    unknown = _asgi(app, "POST", "/api/consumers/advice/accept", {
        "device_id": "no-such-appliance",
        "window": _window(_WHEN + timedelta(hours=1)),
    })
    assert unknown[0] == 422
    assert "unknown" in unknown[1]["detail"].lower()

    mismatched = _asgi(app, "POST", "/api/consumers/advice/accept", {
        "device_id": "mock-washer-1",
        "window": _window(_WHEN + timedelta(hours=1), declared=2),
    })
    assert mismatched[0] == 422
    assert cache.values == {}


def test_reader_and_view_token_cannot_accept_or_cancel(tmp_path):
    from ems.authn import hash_password
    from ems.storage.auth import AuthStore

    db = str(tmp_path / "ems.sqlite")
    auth = AuthStore(db)
    _APP_EXTRA.clear()
    _APP_EXTRA["auth_store"] = auth
    try:
        app = _app(tmp_path)
        app.state.users_exist = True

        async def go():
            await auth.init()
            reader = await auth.create_user("rdr", hash_password("pw12345678"), "reader")
            user = await auth.create_user("usr", hash_password("pw12345678"), "user")
            reader_tok = await auth.create_token(reader, "session")
            user_tok = await auth.create_token(user, "session")
            view_tok = await auth.create_token(user, "access", tier="view")
            denied = await _asgi_async(app, "POST", "/api/consumers/advice/accept",
                                       {"device_id": "x"}, reader_tok)
            denied_del = await _asgi_async(app, "DELETE", "/api/consumers/advice/accept",
                                           {"device_id": "x"}, reader_tok)
            view_denied = await _asgi_async(app, "POST", "/api/consumers/advice/accept",
                                            {"device_id": "x"}, view_tok)
            passed = await _asgi_async(app, "POST", "/api/consumers/advice/accept", {}, user_tok)
            return denied, denied_del, view_denied, passed

        denied, denied_del, view_denied, passed = asyncio.run(go())
    finally:
        _APP_EXTRA.clear()
    assert denied[0] == 403
    assert denied_del[0] == 403
    assert view_denied[0] == 403
    assert passed[0] == 422


async def _asgi_async(app, method, path, body, token=None):
    payload = b"" if body is None else json.dumps(body).encode()
    headers = [(b"content-type", b"application/json")]
    if token:
        headers.append((b"authorization", f"Bearer {token}".encode()))
    status = {"code": 0}
    chunks: list[bytes] = []

    async def receive():
        return {"type": "http.request", "body": payload, "more_body": False}

    async def send(message):
        if message["type"] == "http.response.start":
            status["code"] = message["status"]
        elif message["type"] == "http.response.body":
            chunks.append(message.get("body") or b"")

    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": headers,
        "client": ("127.0.0.1", 1234),
        "server": ("test", 80),
    }
    await app(scope, receive, send)
    raw = b"".join(chunks)
    return status["code"], (json.loads(raw) if raw else {})
