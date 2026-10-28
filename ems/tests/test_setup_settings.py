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
    SETTINGS_BY_KEY,
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


def test_battery_numeric_setup_keys_match_schema():
    """Reserve is the sole onboarding exception among numeric battery settings."""
    derived = {
        f.key
        for f in SETTINGS_SCHEMA
        if f.key.startswith("battery.")
        and f.type in ("number", "int")
        and f.unit in ("W", "%", "kWh")
    }
    assert derived == set(SETUP_SETTINGS_BATTERY_POWER_LIMIT_KEYS | {"battery.min_reserve_soc"})


def test_battery_setup_keys_partition_schema():
    """Every battery.* schema key is either onboarding-safe (connection) or explicitly denied."""
    battery_keys = {f.key for f in SETTINGS_SCHEMA if f.key.startswith("battery.")}
    allowed = SETUP_SETTINGS_BATTERY_SAFE_KEYS | SETUP_SETTINGS_BATTERY_POWER_LIMIT_KEYS
    assert battery_keys == allowed


def test_reserve_default_within_schema_bounds():
    field = SETTINGS_BY_KEY["battery.min_reserve_soc"]
    assert field.min is not None and field.max is not None
    assert field.min <= field.default <= field.max
    assert field.applies == "live"


@pytest.mark.parametrize("boundary", ["min", "max"])
def test_setup_reserve_accepts_both_bounds_live_and_audits_key_only(tmp_path, boundary):
    field = SETTINGS_BY_KEY["battery.min_reserve_soc"]
    value = getattr(field, boundary)
    app, db = _app(tmp_path)
    _seed_admin(db)
    with TestClient(app) as c:
        h = _admin_headers(c)
        r = c.post("/api/setup/settings", json={field.key: value}, headers=h)
        assert r.status_code == 200
        assert r.json()["restart_required"] is False
        assert _settings_values(c, h)[field.key] == value
        entries = _audit_entries(c, h)
        assert entries and entries[0]["detail"]["keys"] == [field.key]
        assert "values" not in entries[0]["detail"]


@pytest.mark.parametrize("boundary", ["min", "max"])
def test_setup_reserve_rejects_outside_bounds_atomically(tmp_path, boundary):
    field = SETTINGS_BY_KEY["battery.min_reserve_soc"]
    value = getattr(field, boundary) + (-1 if boundary == "min" else 1)
    app, db = _app(tmp_path)
    _seed_admin(db)
    with TestClient(app) as c:
        h = _admin_headers(c)
        before = _settings_values(c, h)
        r = c.post("/api/setup/settings", json={field.key: value}, headers=h)
        assert r.status_code == 422
        assert field.key in r.json()["errors"]
        assert _settings_values(c, h) == before
        assert _audit_entries(c, h) == []


@pytest.mark.parametrize("value", ["geen getal", True, False, None])
def test_setup_reserve_rejects_non_numeric_values(tmp_path, value):
    key = "battery.min_reserve_soc"
    app, db = _app(tmp_path)
    _seed_admin(db)
    with TestClient(app) as c:
        h = _admin_headers(c)
        before = _settings_values(c, h)
        r = c.post("/api/setup/settings", json={key: value}, headers=h)
        assert r.status_code == 422
        assert key in r.json()["errors"]
        assert _settings_values(c, h) == before


@pytest.mark.parametrize("key", ["battery.night_reserve_kwh", "battery.max_charge_w"])
def test_setup_other_battery_settings_remain_forbidden(tmp_path, key):
    app, db = _app(tmp_path)
    _seed_admin(db)
    with TestClient(app) as c:
        h = _admin_headers(c)
        r = c.post("/api/setup/settings", json={key: SETTINGS_BY_KEY[key].default}, headers=h)
        assert r.status_code == 422
        assert key in r.json()["errors"]


def test_setup_live_prices_allowed_and_mixed_control_rejected(tmp_path):
    app, db = _app(tmp_path)
    _seed_admin(db)
    with TestClient(app) as c:
        h = _admin_headers(c)
        ok = c.post("/api/setup/settings", json={"connection.use_live_prices": True}, headers=h)
        assert ok.status_code == 200
        assert ok.json()["restart_required"] is True
        before = _settings_values(c, h)
        audit_before = _audit_entries(c, h)
        bad = c.post("/api/setup/settings", json={
            "prices.tibber_token": "never-save-this-token",
            "connection.use_live_prices": False,
            "control.operational": True,
        }, headers=h)
        assert bad.status_code == 422
        assert "control.operational" in bad.json()["errors"]
        assert "never-save-this-token" not in bad.text
        assert _settings_values(c, h) == before
        assert _audit_entries(c, h) == audit_before

    store = SettingsStore(db)

    async def stored_values():
        await store.init()
        values = await store.all()
        await store.close()
        return values

    assert "prices.tibber_token" not in asyncio.run(stored_values())


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
        r = c.post("/api/setup/settings", json={
            "prices.tibber_token": token,
            "connection.use_live_prices": True,
        }, headers=h)
        assert r.status_code == 200
        assert r.json()["restart_required"] is True
        values = r.json()["values"]
        assert values.get("prices.tibber_token") == ""
        assert values.get("prices.tibber_token.__set") is True
        assert token not in str(values)
        entries = _audit_entries(c, h)
        assert entries
        assert "prices.tibber_token" in entries[0]["detail"]["keys"]
        assert "prices.tibber_token" in entries[0]["detail"]["secrets"]
        assert token not in str(entries)

        keep = c.post("/api/setup/settings", json={
            "prices.tibber_token": "",
            "connection.use_live_prices": True,
        }, headers=h)
        assert keep.status_code == 200
        assert keep.json()["values"]["prices.tibber_token.__set"] is True
        assert token not in keep.text

    store = SettingsStore(db)

    async def stored_token():
        await store.init()
        values = await store.all()
        await store.close()
        return values.get("prices.tibber_token")

    assert asyncio.run(stored_token()) == token


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
                "battery.min_reserve_soc": SETTINGS_BY_KEY["battery.min_reserve_soc"].default,
                "prices.tibber_token": "spy-test-token",
                "connection.use_live_prices": True,
            },
            headers=h,
        )
    assert r.status_code == 200
    assert calls == []


@pytest.mark.parametrize("ip", ["192.168.1.20", "10.0.0.5", "172.16.4.4"])
def test_meters_p1_ip_private_ipv4_accepted(tmp_path, ip: str):
    app, db = _app(tmp_path)
    _seed_admin(db)
    with TestClient(app) as c:
        h = _admin_headers(c)
        r = c.post("/api/setup/settings", json={"meters.p1_ip": ip}, headers=h)
    assert r.status_code == 200
    assert r.json()["values"]["meters.p1_ip"] == ip
    assert r.json()["restart_required"] is True


def test_meters_p1_ip_mixed_control_operational_rejected(tmp_path):
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
            json={"meters.p1_ip": "192.168.1.20", "control.operational": True},
            headers=h,
        )
        assert r.status_code == 422
        assert _settings_values(c, h) == cache_before
        assert _audit_entries(c, h) == []
    assert asyncio.run(snapshot()) == before


def test_control_flags_unchanged_after_setup(tmp_path):
    app, db = _app(tmp_path)
    _seed_admin(db)
    with TestClient(app) as c:
        h = _admin_headers(c)
        c.post(
            "/api/setup/settings",
            json={
                "battery.min_reserve_soc": SETTINGS_BY_KEY["battery.min_reserve_soc"].default,
                "prices.tibber_token": "watch-only-test-token",
                "connection.use_live_prices": True,
            },
            headers=h,
        )
        vals = c.get("/api/settings", headers=h).json()["values"]
        assert vals.get("control.operational") is False
        assert vals.get("control.dry_run") is True
        status = c.get("/api/status", headers=h).json()
        assert status.get("dry_run") is True
        assert status.get("dry_run_cause") == "mock"
