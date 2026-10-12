"""#263 — Home Assistant connection wizard (probe + disconnect, mocked HTTP)."""
from __future__ import annotations

import json

from fastapi.testclient import TestClient

from ems.consumers.ha_probe import probe_ha_connection
from ems.consumers.selection import HA_DEVICE_IDS_KEY
from ems.settings import normalize_ha_url
from ems.sources.mock import MockSource
from ems.storage.history import HistoryStore
from ems.storage.settings import SettingsStore
from ems.web.api import create_app

WASHER = {
    "entity_id": "switch.bijkeuken_washer",
    "state": "off",
    "attributes": {"friendly_name": "Bijkeuken wasmachine"},
}
TV = {
    "entity_id": "switch.living_tv",
    "state": "on",
    "attributes": {"friendly_name": "Living TV"},
}


def test_normalize_ha_url_strips_slash_and_rejects_path():
    assert normalize_ha_url("https://ha.local:8123/") == "https://ha.local:8123"
    try:
        normalize_ha_url("http://ha.local/api")
        raise AssertionError("expected ValueError")
    except ValueError as exc:
        assert "host only" in str(exc)


def test_probe_returns_laundry_candidates():
    def fake_get(url, headers):
        assert "Bearer secret" in headers["Authorization"]
        assert url.endswith("/api/states")
        return [WASHER, TV]

    out = probe_ha_connection(
        "http://homeassistant.local:8123/", "  secret  ", http_get=fake_get,
    )
    assert out["ok"] is True
    assert out["url"] == "http://homeassistant.local:8123"
    assert "token" not in out
    assert len(out["devices"]) == 1
    assert out["devices"][0]["id"] == "switch.bijkeuken_washer"
    assert out["devices"][0]["kind"] == "washer"


def test_probe_invalid_token_401():
    class _HTTPStatusError(Exception):
        def __init__(self):
            self.response = type("R", (), {"status_code": 401})()

    def boom(url, headers):
        raise _HTTPStatusError()

    out = probe_ha_connection("http://ha.local:8123", "bad", http_get=boom)
    assert out["ok"] is False
    assert out["error"] == "invalid_token"
    assert "token" not in json.dumps(out).lower() or "rejected the token" in out["message"]


def test_probe_timeout():
    class TimeoutException(Exception):
        pass

    def boom(url, headers):
        raise TimeoutException("read timeout")

    out = probe_ha_connection("http://ha.local:8123", "tok", http_get=boom)
    assert out["ok"] is False
    assert out["error"] == "timeout"


def test_probe_tls_error():
    class SSLError(Exception):
        pass

    def boom(url, headers):
        raise SSLError("certificate verify failed")

    out = probe_ha_connection("https://ha.local:8123", "tok", http_get=boom)
    assert out["ok"] is False
    assert out["error"] == "tls_error"


def test_probe_not_ha_payload():
    out = probe_ha_connection(
        "http://ha.local:8123", "tok",
        http_get=lambda u, h: {"message": "HTML page"},
    )
    assert out["ok"] is False
    assert out["error"] == "not_home_assistant"


def test_api_test_connection_and_disconnect(tmp_path, monkeypatch):
    def fake_get(url, headers):
        assert "secret-token" not in url
        return [WASHER]

    import ems.web.routes.consumers as consumers_mod
    from ems.web.routes import consumers as consumers_routes

    db = str(tmp_path / "ems.sqlite")
    store = SettingsStore(db)
    # Patch probe's default path via router http_get inject — rebuild app with monkeypatch
    # on probe_ha_connection used by the route.
    monkeypatch.setattr(
        consumers_routes,
        "probe_ha_connection",
        lambda url, token, http_get=None: probe_ha_connection(
            url, token, http_get=fake_get,
        ),
    )
    _ = consumers_mod  # placate

    app = create_app(
        MockSource(),
        dry_run=True,
        config_dry_run=False,
        dev_mode="mock",
        store=HistoryStore(db),
        settings_store=store,
    )
    with TestClient(app) as c:
        r = c.post(
            "/api/consumers/ha/test-connection",
            json={"url": "http://ha.local:8123", "token": "secret-token"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert "secret-token" not in r.text
        assert body["devices"][0]["id"] == "switch.bijkeuken_washer"

        # Persist connection like the wizard save.
        assert c.post("/api/settings", json={
            "consumers.ha.url": "http://ha.local:8123",
            "consumers.ha.token": "secret-token",
            "consumers.ha.enabled": True,
            HA_DEVICE_IDS_KEY: '["switch.bijkeuken_washer"]',
        }).status_code == 200
        values = c.get("/api/settings").json()["values"]
        assert values["consumers.ha.enabled"] is True
        assert values["consumers.ha.token"] == ""
        assert values["consumers.ha.token.__set"] is True

        # Stored token must not follow a different URL (SSRF / credential redirect).
        hijack = c.post(
            "/api/consumers/ha/test-connection",
            json={"url": "http://evil.example:8123", "use_stored_token": True},
        )
        assert hijack.status_code == 200
        assert hijack.json()["ok"] is False
        assert hijack.json()["error"] == "url_mismatch"
        assert "evil" not in hijack.text.lower() or "url_mismatch" in hijack.json()["error"]

        # String "false" must not enable the stored-token path.
        fake_flag = c.post(
            "/api/consumers/ha/test-connection",
            json={"url": "http://evil.example:8123", "use_stored_token": "false"},
        )
        assert fake_flag.status_code == 200
        assert fake_flag.json()["ok"] is False

        # Same stored URL + use_stored_token is allowed.
        ok_reuse = c.post(
            "/api/consumers/ha/test-connection",
            json={"url": "http://ha.local:8123", "use_stored_token": True},
        )
        assert ok_reuse.status_code == 200
        assert ok_reuse.json()["ok"] is True
        assert "secret-token" not in ok_reuse.text

        # URL-only settings change must drop the stored token (no retarget via cache).
        assert c.post("/api/settings", json={
            "consumers.ha.url": "http://evil.example:8123",
        }).status_code == 200
        after_url = c.get("/api/settings").json()["values"]
        assert after_url["consumers.ha.url"] == "http://evil.example:8123"
        assert after_url.get("consumers.ha.token.__set") is False
        assert after_url["consumers.ha.enabled"] is False
        retarget = c.post(
            "/api/consumers/ha/test-connection",
            json={"use_stored_token": True},
        )
        assert retarget.status_code == 200
        assert retarget.json()["ok"] is False
        # Env HA_TOKEN must not re-bind to the hostile URL on the next seed pass.
        from ems.connection import _read_store, _seed_store

        _seed_store(db, {
            "consumers.ha.token": "secret-token",
            "consumers.ha.url": "http://ha.local:8123",
        })
        store_after = _read_store(db)
        assert "consumers.ha.token" not in store_after
        assert store_after.get("consumers.ha.url") == "http://evil.example:8123"
        # Restore a saved connection for the disconnect assertion below.
        assert c.post("/api/settings", json={
            "consumers.ha.url": "http://ha.local:8123",
            "consumers.ha.token": "secret-token",
            "consumers.ha.enabled": True,
        }).status_code == 200

        d = c.post("/api/consumers/ha/disconnect")
        assert d.status_code == 200
        assert d.json()["ok"] is True
        assert d.json()["restart_required"] is True
        after = c.get("/api/settings").json()["values"]
        assert after["consumers.ha.enabled"] is False
        assert after.get("consumers.ha.token.__set") is False
