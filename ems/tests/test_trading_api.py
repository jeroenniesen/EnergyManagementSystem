"""E-11 PR C: Trading portal API (B-106 / B-107 / B-108)."""
from __future__ import annotations

from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from ems.control.mode_controller import ModeController
from ems.domain import RawSample
from ems.lifecycle import Lifecycle
from ems.sources.battery import MockBatteryDriver
from ems.sources.forecast import MockSolarForecastSource
from ems.sources.prices import MockPriceSource
from ems.storage.cache import CacheStore
from ems.storage.settings import SettingsStore
from ems.web.api import create_app
from ems.web.authz import Tier, required_tier

AMS = ZoneInfo("Europe/Amsterdam")


class _Source:
    def read(self) -> RawSample:
        return RawSample(
            grid_power_w=0.0, solar_power_w=0.0, battery_power_w=0.0,
            ev_power_w=0.0, soc_pct=70.0,
        )


def _app(tmp_path, *, dry_run: bool = True, operational: bool = False):
    db = str(tmp_path / "ems.sqlite")
    driver = MockBatteryDriver(armed=True)
    controller = ModeController(driver, Lifecycle(dry_run=dry_run), dry_run=dry_run)
    app = create_app(
        _Source(), dry_run=dry_run, dev_mode="mock", tz=AMS,
        price_source=MockPriceSource(AMS), solar_forecast=MockSolarForecastSource(AMS),
        controller=controller, settings_store=SettingsStore(db), cache_store=CacheStore(db),
    )
    return app, controller


def test_trading_get_defaults_off(tmp_path):
    app, _ = _app(tmp_path)
    with TestClient(app) as c:
        r = c.get("/api/trading")
        assert r.status_code == 200
        body = r.json()
        assert body["trading_enabled"] is False
        assert body["allow_export_discharge"] is False
        assert body["min_extra_eur"] == 0.50
        assert body["max_export_kwh_per_day"] == 0.0
        assert body["export_price_model"] == "spot_minus_tax"
        assert "evaluation" in body and "reason" in body["evaluation"]
        assert body["copy"]["house"]
        assert body["copy"]["sell"]


def test_trading_settings_roundtrip(tmp_path):
    app, _ = _app(tmp_path)
    with TestClient(app) as c:
        c.post("/api/settings", json={
            "planner.trading_enabled": True,
            "planner.trading_min_extra_eur": 0.75,
            "planner.max_export_kwh_per_day": 4.0,
            "planner.export_mode": "full_dump",
        })
        body = c.get("/api/trading").json()
        assert body["trading_enabled"] is True
        assert body["min_extra_eur"] == 0.75
        assert body["max_export_kwh_per_day"] == 4.0
        assert body["export_mode"] == "full_dump"


def test_test_battery_refuses_dry_run(tmp_path):
    app, _ = _app(tmp_path, dry_run=True, operational=True)
    with TestClient(app) as c:
        c.post("/api/settings", json={"control.operational": True})
        r = c.post("/api/trading/test-battery", json={})
        assert r.status_code == 409
        assert "dry-run" in r.json()["detail"].lower() or "dry" in r.json()["detail"].lower()
        assert r.json()["ok"] is False


def test_test_battery_refuses_watch_only_detail(tmp_path):
    """Watch-only detail is returned when dry_run is false but operational is off.

    Process dry_run is a create_app floor — for this test we only assert the helper's
    operational messaging via GET /api/trading block_reason after settings POST.
    """
    app, _ = _app(tmp_path, dry_run=True)
    with TestClient(app) as c:
        c.post("/api/settings", json={"control.operational": False})
        body = c.get("/api/trading").json()
        assert body["writes_allowed"] is False
        assert body["block_reason"]
        r = c.post("/api/trading/test-battery", json={})
        assert r.status_code == 409
        assert r.json()["ok"] is False


def test_test_battery_operate_gated():
    assert required_tier("/api/trading/test-battery", "POST") == Tier.OPERATE
    assert required_tier("/api/trading", "GET") == Tier.VIEW


def test_arming_flag_surfaces(tmp_path):
    app, controller = _app(tmp_path)
    with TestClient(app) as c:
        c.post("/api/settings", json={"control.allow_export_discharge": True})
        body = c.get("/api/trading").json()
        assert body["allow_export_discharge"] is True
        assert controller.allow_export_discharge is True


def test_export_probe_does_not_flip_arm_flag():
    """B-107: begin_export_probe must not set allow_export_discharge."""
    from datetime import UTC, datetime, timedelta

    driver = MockBatteryDriver(armed=True)
    ctl = ModeController(driver, Lifecycle(dry_run=True), dry_run=True)
    assert ctl.allow_export_discharge is False
    ctl.begin_export_probe(datetime.now(UTC) + timedelta(seconds=60))
    assert ctl.allow_export_discharge is False
    assert ctl.export_probe_active(datetime.now(UTC)) is True
    ctl.clear_export_probe()
    assert ctl.export_probe_active(datetime.now(UTC)) is False
