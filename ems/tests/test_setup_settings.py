"""POST /api/setup/settings — onboarding allowlist (#291)."""
from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from ems.authn import hash_password
from ems.control.mode_controller import ModeController
from ems.lifecycle import Lifecycle
from ems.sources.battery import MockBatteryDriver
from ems.sources.mock import MockSource
from ems.storage.auth import AuthStore
from ems.storage.settings import SettingsStore
from ems.web.api import create_app


def _seed_user(db: str, username: str, password: str, role: str):
    s = AuthStore(db)

    async def run():
        await s.init()
        await s.create_user(username, hash_password(password), role)
        await s.close()

    asyncio.run(run())


def _app(tmp_path, **kw):
    db = str(tmp_path / "ems.sqlite")
    return create_app(
        MockSource(), dry_run=True, dev_mode="mock",
        settings_store=SettingsStore(db),
        auth_store=AuthStore(db),
        **kw,
    ), db


def _admin_headers(client: TestClient) -> dict[str, str]:
    login = client.post("/api/auth/login", json={"username": "admin", "password": "pw12345678"})
    assert login.status_code == 200
    return {"Authorization": f"Bearer {login.json()['token']}"}


def _seed_admin(db: str) -> None:
    _seed_user(db, "admin", "pw12345678", "admin")


@pytest.mark.parametrize(
    "key",
    [
        "control.operational",
        "control.dry_run",
        "control.grid_fuse",
        "web.require_auth",
        "nonsense.key",
    ],
)
def test_forbidden_keys_return_422(tmp_path, key: str):
    app, db = _app(tmp_path)
    _seed_admin(db)
    with TestClient(app) as c:
        h = _admin_headers(c)
        r = c.post("/api/setup/settings", json={key: True}, headers=h)
    assert r.status_code == 422
    assert key in r.json()["errors"]


def test_empty_or_non_object_body_422(tmp_path):
    app, db = _app(tmp_path)
    _seed_admin(db)
    with TestClient(app) as c:
        h = _admin_headers(c)
        assert c.post("/api/setup/settings", json={}, headers=h).status_code == 422
        assert c.post("/api/setup/settings", json="nope", headers=h).status_code == 422


def test_mixed_forbidden_key_rejects_without_persist(tmp_path):
    app, db = _app(tmp_path)
    store = SettingsStore(db)

    async def snapshot():
        await store.init()
        return await store.all()

    before = asyncio.run(snapshot())
    _seed_admin(db)
    with TestClient(app) as c:
        h = _admin_headers(c)
        r = c.post(
            "/api/setup/settings",
            json={"battery.indevolt_ip": "192.168.1.20", "control.operational": True},
            headers=h,
        )
        assert r.status_code == 422
    after = asyncio.run(snapshot())
    assert before == after


def test_auth_matrix(tmp_path):
    app, db = _app(tmp_path)
    payload = {"battery.indevolt_ip": "192.168.1.30", "connection.use_live_devices": True}
    _seed_admin(db)
    _seed_user(db, "oper", "pw12345678", "user")
    _seed_user(db, "rdr", "pw12345678", "reader")
    with TestClient(app) as c:
        assert c.post("/api/setup/settings", json=payload).status_code == 401
        for user, expect in (
            ("rdr", 403),
            ("oper", 403),
            ("admin", 200),
        ):
            login = c.post(
                "/api/auth/login", json={"username": user, "password": "pw12345678"},
            )
            h = {"Authorization": f"Bearer {login.json()['token']}"}
            r = c.post("/api/setup/settings", json=payload, headers=h)
            assert r.status_code == expect, user


def test_legacy_token_requires_auth(tmp_path):
    app, db = _app(tmp_path, web_auth_token="legacy-secret")
    _seed_admin(db)
    with TestClient(app) as c:
        r = c.post(
            "/api/setup/settings",
            json={"battery.indevolt_ip": "192.168.1.40", "connection.use_live_devices": True},
        )
        assert r.status_code in (401, 403)
        bad = c.post(
            "/api/setup/settings",
            json={"battery.indevolt_ip": "192.168.1.40", "connection.use_live_devices": True},
            headers={"Authorization": "Bearer wrong"},
        )
        assert bad.status_code in (401, 403)


def test_allowed_battery_save_masks_and_restart_flag(tmp_path):
    app, db = _app(tmp_path)
    _seed_admin(db)
    with TestClient(app) as c:
        h = _admin_headers(c)
        r = c.post(
            "/api/setup/settings",
            json={
                "battery.indevolt_ip": "192.168.1.55",
                "connection.use_live_devices": True,
                "meters.p1_ip": "10.0.0.8",
            },
            headers=h,
        )
    assert r.status_code == 200
    body = r.json()
    assert body["restart_required"] is True
    assert body["values"]["battery.indevolt_ip"] == "192.168.1.55"
    assert body["values"]["connection.use_live_devices"] is True


@pytest.mark.parametrize(
    "bad_ip",
    [
        "127.0.0.1",
        "169.254.1.1",
        "100.64.0.1",
        "8.8.8.8",
        "::1",
        "fe80::1",
        "http://192.168.1.1",
        "1.2.3.4:80",
        "myrouter.local",
        "",
    ],
)
def test_private_ipv4_only(tmp_path, bad_ip: str):
    app, db = _app(tmp_path)
    _seed_admin(db)
    with TestClient(app) as c:
        h = _admin_headers(c)
        r = c.post(
            "/api/setup/settings",
            json={"battery.indevolt_ip": bad_ip, "connection.use_live_devices": True},
            headers=h,
        )
    assert r.status_code == 422
    assert "battery.indevolt_ip" in r.json()["errors"]


def test_zero_battery_write_calls_on_setup_save(tmp_path):
    calls: list = []

    def spy(mode, *, target_soc=None, power_w=None):
        calls.append((mode, target_soc, power_w))

    driver = MockBatteryDriver(armed=True, write_transport=spy)
    app, db = _app(
        tmp_path,
        battery=driver,
        controller=ModeController(driver, Lifecycle(dry_run=True), dry_run=True),
    )
    _seed_admin(db)
    with TestClient(app) as c:
        h = _admin_headers(c)
        r = c.post(
            "/api/setup/settings",
            json={"battery.indevolt_ip": "192.168.1.77", "connection.use_live_devices": True},
            headers=h,
        )
    assert r.status_code == 200
    assert calls == []


def test_control_flags_unchanged_after_setup(tmp_path):
    app, db = _app(tmp_path)
    _seed_admin(db)
    with TestClient(app) as c:
        h = _admin_headers(c)
        c.post(
            "/api/setup/settings",
            json={"battery.indevolt_ip": "192.168.1.88", "connection.use_live_devices": True},
            headers=h,
        )
        vals = c.get("/api/settings", headers=h).json()["values"]
        assert vals.get("control.operational") is False
        assert vals.get("control.dry_run") is True
        status = c.get("/api/status", headers=h).json()
        assert status.get("dry_run") is True
