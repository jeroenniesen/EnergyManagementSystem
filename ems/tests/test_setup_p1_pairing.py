"""P1 onboarding (#292): no device writes while pairing."""
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from ems.authn import hash_password
from ems.control.mode_controller import ModeController
from ems.domain import RawSample
from ems.lifecycle import Lifecycle
from ems.sources.battery import MockBatteryDriver
from ems.sources.live import HomeWizardMeter
from ems.storage.audit import AuditStore
from ems.storage.auth import AuthStore
from ems.storage.settings import SettingsStore
from ems.web.api import create_app


class _SetupTestSource:
    def read(self) -> RawSample:
        return RawSample(
            grid_power_w=0.0,
            solar_power_w=0.0,
            battery_power_w=0.0,
            ev_power_w=0.0,
            soc_pct=50.0,
            total_gas_m3=None,
        )


def _seed_admin(db: str) -> None:
    async def run():
        s = AuthStore(db)
        await s.init()
        await s.create_user("admin", hash_password("pw12345678"), "admin")
        await s.close()

    asyncio.run(run())


def _admin_headers(client: TestClient) -> dict[str, str]:
    login = client.post("/api/auth/login", json={"username": "admin", "password": "pw12345678"})
    assert login.status_code == 200
    return {"Authorization": f"Bearer {login.json()['token']}"}


def test_zero_battery_and_http_writes_on_p1_setup_save(tmp_path, monkeypatch):
    """AC 6 (#292): pairing P1 does not POST/PUT devices or call battery write transport."""
    battery_calls: list = []
    http_calls: list[tuple[str, str]] = []

    def spy(mode, *, target_soc=None, power_w=None):
        battery_calls.append((mode, target_soc, power_w))

    driver = MockBatteryDriver(write_transport=spy)
    assert driver.armed is False

    def recording_request(method, url, **kwargs):
        http_calls.append((method.upper(), str(url)))
        mock = MagicMock()
        mock.raise_for_status = MagicMock()
        mock.json.return_value = {"active_power_w": 0}
        return mock

    monkeypatch.setattr("ems.http_client.request", recording_request)

    db = str(tmp_path / "ems.sqlite")
    app = create_app(
        _SetupTestSource(),
        dry_run=True,
        dev_mode="mock",
        settings_store=SettingsStore(db),
        auth_store=AuthStore(db),
        audit_store=AuditStore(db),
        battery=driver,
        controller=ModeController(driver, Lifecycle(dry_run=True), dry_run=True),
    )
    _seed_admin(db)
    with TestClient(app) as c:
        h = _admin_headers(c)
        r = c.post(
            "/api/setup/settings",
            json={"meters.p1_ip": "192.168.1.33"},
            headers=h,
        )
        assert r.status_code == 200
        for _ in range(3):
            c.get("/api/status", headers=h)
    assert battery_calls == []
    assert http_calls == []
    write_http = [m for m, _ in http_calls if m in ("POST", "PUT", "PATCH", "DELETE")]
    assert write_http == []


def test_homewizard_p1_read_uses_get_only():
    """AC 6 (#292): P1 meter adapter issues GET only (recording transport, no real network I/O)."""
    methods: list[str] = []

    def recording_get(url: str) -> dict:
        methods.append("GET")
        return {"active_power_w": 0}

    meter = HomeWizardMeter("192.168.1.44", http_get=recording_get)
    payload = meter.read()
    assert payload["active_power_w"] == 0
    assert methods == ["GET"]
