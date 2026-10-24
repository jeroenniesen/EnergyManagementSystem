"""Setup wizard save against the real FastAPI app (#415) — no route mocks."""
from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient

from ems.storage.settings import SettingsStore
from ems.tests.test_setup_settings import _admin_headers, _app, _seed_admin

# Mirrors buildBatteryLinkPayload() in ems/web/frontend/src/setupFlow.ts L17-29:
# battery IP, connection.use_live_devices, optional meters.p1_ip when the wizard asks for P1.
_WIZARD_BATTERY_LINK_PAYLOAD = {
    "battery.indevolt_ip": "192.168.1.61",
    "connection.use_live_devices": True,
    "meters.p1_ip": "192.168.1.10",
}

_ARM_STEP2_BLOCK_DETAIL = "Ontbreekt: stap 2 — bekijk de dry-run"


def test_wizard_battery_link_payload_persists_via_setup_settings(tmp_path):
    app, db = _app(tmp_path)
    store = SettingsStore(db)
    _seed_admin(db)
    with TestClient(app) as c:
        h = _admin_headers(c)
        r = c.post("/api/setup/settings", json=_WIZARD_BATTERY_LINK_PAYLOAD, headers=h)
        assert r.status_code == 200
        body = r.json()
        assert body["restart_required"] is True
        values = body["values"]
        assert values["battery.indevolt_ip"] == "192.168.1.61"
        assert values["connection.use_live_devices"] is True
        assert values["meters.p1_ip"] == "192.168.1.10"
        assert values.get("control.allow_export_discharge") is False

        get_vals = c.get("/api/settings", headers=h).json()["values"]
        assert get_vals["battery.indevolt_ip"] == "192.168.1.61"
        assert get_vals["connection.use_live_devices"] is True
        assert get_vals["meters.p1_ip"] == "192.168.1.10"
        assert get_vals.get("control.allow_export_discharge") is False

    async def stored():
        await store.init()
        return await store.all()

    persisted = asyncio.run(stored())
    assert persisted.get("battery.indevolt_ip") == "192.168.1.61"
    assert persisted.get("connection.use_live_devices") is True
    assert persisted.get("meters.p1_ip") == "192.168.1.10"
    assert persisted.get("control.allow_export_discharge") is not True


def test_setup_route_rejects_allow_export_discharge_arm(tmp_path):
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
            json={
                **_WIZARD_BATTERY_LINK_PAYLOAD,
                "control.allow_export_discharge": True,
            },
            headers=h,
        )
        assert r.status_code == 422
        assert "control.allow_export_discharge" in r.json()["errors"]
        assert (
            c.get("/api/settings", headers=h).json()["values"].get(
                "control.allow_export_discharge"
            )
            is False
        )
    assert asyncio.run(snapshot()) == before


def test_settings_rejects_allow_export_discharge_arm_without_dry_run_step(tmp_path):
    app, db = _app(tmp_path)
    _seed_admin(db)
    with TestClient(app) as c:
        h = _admin_headers(c)
        r = c.post("/api/settings", json={"control.allow_export_discharge": True}, headers=h)
        assert r.status_code == 409
        detail = r.json()["detail"]
        # Default test app is mock+dry_run — trading floors block before stap 2 (#339).
        floor_or_step2 = (
            detail == _ARM_STEP2_BLOCK_DETAIL
            or "dry-run" in detail.lower()
            or "watch only" in detail.lower()
        )
        assert floor_or_step2
        vals = c.get("/api/settings", headers=h).json()["values"]
        assert vals["control.allow_export_discharge"] is False
