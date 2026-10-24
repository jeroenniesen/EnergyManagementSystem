"""POST /api/setup/settings — onboarding allowlist (#291)."""
from __future__ import annotations

import asyncio
import types

import pytest
from fastapi.testclient import TestClient

from ems.authn import hash_password
from ems.control.mode_controller import ModeController
from ems.domain import PhysicalMode, RawSample
from ems.lifecycle import Lifecycle
from ems.settings import (
    SECRET_KEYS,
    SETTINGS_SCHEMA,
    SETUP_SETTINGS_BATTERY_POWER_LIMIT_KEYS,
    SETUP_SETTINGS_BATTERY_SAFE_KEYS,
    SETUP_SETTINGS_SECRET_ALLOWLIST,
)
from ems.sources.battery import MockBatteryDriver
from ems.storage.audit import AuditStore
from ems.storage.auth import AuthStore
from ems.storage.settings import SettingsStore
from ems.web.api import create_app


class _SetupTestSource:
    """Minimal Source double for setup-settings API tests (no mock source import)."""

    def read(self) -> RawSample:
        return RawSample(
            grid_power_w=200.0,
            solar_power_w=0.0,
            battery_power_w=800.0,
            ev_power_w=0.0,
            soc_pct=55.0,
            total_gas_m3=None,
        )


def _seed_user(db: str, username: str, password: str, role: str):
    s = AuthStore(db)

    async def run():
        await s.init()
        await s.create_user(username, hash_password(password), role)
        await s.close()

    asyncio.run(run())


def _app(tmp_path, **kw):
    db = str(tmp_path / "ems.sqlite")
    if "audit_store" not in kw:
        kw = {**kw, "audit_store": AuditStore(db)}
    return create_app(
        _SetupTestSource(), dry_run=True, dev_mode="mock",
        settings_store=SettingsStore(db),
        auth_store=AuthStore(db),
        **kw,
    ), db


def _settings_values(client: TestClient, headers: dict[str, str]) -> dict:
    return client.get("/api/settings", headers=headers).json()["values"]


def _audit_entries(client: TestClient, headers: dict[str, str]) -> list:
    return client.get("/api/audit", params={"category": "config_change"}, headers=headers).json()[
        "entries"
    ]


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
        cache_before = _settings_values(c, h)
        r = c.post(
            "/api/setup/settings",
            json={"battery.indevolt_ip": "192.168.1.20", "control.operational": True},
            headers=h,
        )
        assert r.status_code == 422
        assert _settings_values(c, h) == cache_before
        assert _audit_entries(c, h) == []
    after = asyncio.run(snapshot())
    assert before == after


def test_battery_power_limit_deny_keys_match_schema():
    """AC 17: deny-set stays aligned with battery.* number fields in W / % / kWh."""
    derived = {
        f.key
        for f in SETTINGS_SCHEMA
        if f.key.startswith("battery.")
        and f.type in ("number", "int")
        and f.unit in ("W", "%", "kWh")
    }
    assert derived == set(SETUP_SETTINGS_BATTERY_POWER_LIMIT_KEYS)


def test_battery_setup_keys_partition_schema():
    """Every battery.* schema key is either onboarding-safe (connection) or explicitly denied."""
    battery_keys = {f.key for f in SETTINGS_SCHEMA if f.key.startswith("battery.")}
    allowed = SETUP_SETTINGS_BATTERY_SAFE_KEYS | SETUP_SETTINGS_BATTERY_POWER_LIMIT_KEYS
    assert battery_keys == allowed


@pytest.mark.parametrize("key", sorted(SETUP_SETTINGS_BATTERY_POWER_LIMIT_KEYS))
def test_denied_battery_limit_keys_return_422_without_persist(tmp_path, key: str):
    app, db = _app(tmp_path)
    store = SettingsStore(db)

    async def snapshot():
        await store.init()
        return await store.all()

    before = asyncio.run(snapshot())
    _seed_admin(db)
    with TestClient(app) as c:
        h = _admin_headers(c)
        cache_before = _settings_values(c, h)
        r = c.post("/api/setup/settings", json={key: 1234}, headers=h)
        assert r.status_code == 422
        assert key in r.json()["errors"]
        assert _settings_values(c, h) == cache_before
        assert _audit_entries(c, h) == []
    assert asyncio.run(snapshot()) == before


@pytest.mark.parametrize(
    "secret_key",
    sorted(k for k in SECRET_KEYS if k not in SETUP_SETTINGS_SECRET_ALLOWLIST),
)
def test_non_allowlisted_secret_keys_rejected(tmp_path, secret_key: str):
    app, db = _app(tmp_path)
    store = SettingsStore(db)

    async def snapshot():
        await store.init()
        return await store.all()

    before = asyncio.run(snapshot())
    _seed_admin(db)
    with TestClient(app) as c:
        h = _admin_headers(c)
        cache_before = _settings_values(c, h)
        r = c.post("/api/setup/settings", json={secret_key: "leak-me"}, headers=h)
        assert r.status_code == 422
        assert secret_key in r.json()["errors"]
        assert _settings_values(c, h) == cache_before
        assert _audit_entries(c, h) == []
    assert asyncio.run(snapshot()) == before


def test_setup_tibber_token_non_string_rejected_without_echo(tmp_path):
    app, db = _app(tmp_path)
    _seed_admin(db)
    secret_num = 42424242
    with TestClient(app) as c:
        h = _admin_headers(c)
        r = c.post(
            "/api/setup/settings",
            json={"prices.tibber_token": secret_num},
            headers=h,
        )
        assert r.status_code == 422
        body = r.json()
        assert "prices.tibber_token" in body["errors"]
        assert str(secret_num) not in r.text


def test_setup_tibber_token_masked_and_audited_without_value(tmp_path):
    app, db = _app(tmp_path)
    _seed_admin(db)
    token = "tibber-test-token-xyz"
    with TestClient(app) as c:
        h = _admin_headers(c)
        r = c.post("/api/setup/settings", json={"prices.tibber_token": token}, headers=h)
        assert r.status_code == 200
        values = r.json()["values"]
        assert values.get("prices.tibber_token") == ""
        assert values.get("prices.tibber_token.__set") is True
        assert token not in str(values)
        entries = _audit_entries(c, h)
        assert entries
        assert "prices.tibber_token" in entries[0]["detail"]["keys"]
        assert "prices.tibber_token" in entries[0]["detail"]["secrets"]
        assert token not in str(entries)


def test_setup_save_audit_lists_keys_only(tmp_path):
    app, db = _app(tmp_path)
    _seed_admin(db)
    with TestClient(app) as c:
        h = _admin_headers(c)
        r = c.post(
            "/api/setup/settings",
            json={
                "battery.indevolt_ip": "192.168.1.61",
                "connection.use_live_devices": True,
            },
            headers=h,
        )
        assert r.status_code == 200
        entries = _audit_entries(c, h)
        assert entries
        keys = entries[0]["detail"]["keys"]
        assert set(keys) == {"battery.indevolt_ip", "connection.use_live_devices"}
        assert "192.168.1.61" not in entries[0]["summary"]


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
    "ip_key",
    [
        "battery.indevolt_ip",
        "meters.p1_ip",
        "meters.solar_ip",
        "meters.car_ip",
    ],
)
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
def test_private_ipv4_only(tmp_path, ip_key: str, bad_ip: str):
    app, db = _app(tmp_path)
    _seed_admin(db)
    with TestClient(app) as c:
        h = _admin_headers(c)
        r = c.post(
            "/api/setup/settings",
            json={ip_key: bad_ip, "connection.use_live_devices": True},
            headers=h,
        )
    assert r.status_code == 422
    assert ip_key in r.json()["errors"]


def test_indevolt_ips_extra_rejects_public_ip(tmp_path):
    app, db = _app(tmp_path)
    _seed_admin(db)
    with TestClient(app) as c:
        h = _admin_headers(c)
        r = c.post(
            "/api/setup/settings",
            json={
                "battery.indevolt_ips_extra": "8.8.8.8, 192.168.1.2",
                "connection.use_live_devices": True,
            },
            headers=h,
        )
    assert r.status_code == 422
    assert "battery.indevolt_ips_extra" in r.json()["errors"]


def test_zero_battery_write_calls_on_setup_save(tmp_path):
    calls: list = []

    def spy(mode, *, target_soc=None, power_w=None):
        calls.append((mode, target_soc, power_w))

    driver = MockBatteryDriver(write_transport=spy)
    assert driver.armed is False
    driver.apply(PhysicalMode.CHARGE)
    assert calls == []

    def apply_would_hit_transport(
        self, mode, *, target_soc=None, power_w=None,
    ) -> bool:
        if self._write_transport is not None:
            self._write_transport(mode, target_soc=target_soc, power_w=power_w)
        self._mode = mode
        self.last_target_soc, self.last_power_w = target_soc, power_w
        return True

    driver.apply = types.MethodType(apply_would_hit_transport, driver)
    driver.apply(PhysicalMode.DISCHARGE)
    assert calls == [(PhysicalMode.DISCHARGE, None, None)]
    calls.clear()

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
            json={
                "battery.indevolt_ip": "192.168.1.77",
                "connection.use_live_devices": True,
            },
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
        assert status.get("dry_run_cause") == "mock"
