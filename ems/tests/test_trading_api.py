"""E-11 PR C: Trading portal API (B-106 / B-107 / B-108)."""
from __future__ import annotations

from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from ems.control.mode_controller import ModeController
from ems.control.override import NONE as OVERRIDE_NONE
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
    def __init__(self, *, ev_power_w: float = 0.0) -> None:
        self.ev_power_w = ev_power_w

    def read(self) -> RawSample:
        return RawSample(
            grid_power_w=0.0, solar_power_w=0.0, battery_power_w=0.0,
            ev_power_w=self.ev_power_w, soc_pct=70.0,
        )


def _app(
    tmp_path,
    *,
    dry_run: bool = True,
    operational: bool = False,
    ev_power_w: float = 0.0,
    with_override_store: bool = False,
    controller: ModeController | None = None,
    control_cycle_seconds: float = 3600.0,
):
    del operational  # applied via settings POST when needed; create_app floor is dry_run
    db = str(tmp_path / "ems.sqlite")
    if controller is None:
        driver = MockBatteryDriver(armed=True)
        controller = ModeController(driver, Lifecycle(dry_run=dry_run), dry_run=dry_run)
    kwargs: dict = {
        "price_source": MockPriceSource(AMS),
        "solar_forecast": MockSolarForecastSource(AMS),
        "controller": controller,
        "settings_store": SettingsStore(db),
        "cache_store": CacheStore(db),
        "control_cycle_seconds": control_cycle_seconds,
    }
    if with_override_store:
        kwargs["override_store"] = SettingsStore(db, table="runtime_state")
    app = create_app(
        _Source(ev_power_w=ev_power_w), dry_run=dry_run, dev_mode="mock", tz=AMS,
        **kwargs,
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
        ev = body["evaluation"]
        assert ev["projection"] is True
        assert "not the act decision" in ev["projection_note"]
        assert isinstance(ev["data_quality"], str) and ev["data_quality"]
        assert ev["validator"] in (None, "valid", "warn", "unsafe")
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
    """Watch only is the 409 when dry-run is off and control.operational is off."""
    app, _ = _app(tmp_path, dry_run=False)
    with TestClient(app) as c:
        c.post("/api/settings", json={"control.operational": False})
        body = c.get("/api/trading").json()
        assert body["writes_allowed"] is False
        assert body["dry_run"] is False
        assert "watch only" in (body["block_reason"] or "").lower()
        r = c.post("/api/trading/test-battery", json={})
        assert r.status_code == 409
        assert r.json()["ok"] is False
        assert "watch only" in r.json()["detail"].lower()


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

    from ems.domain import BatteryIntent, PhysicalMode

    driver = MockBatteryDriver(armed=True)
    ctl = ModeController(driver, Lifecycle(dry_run=True), dry_run=True)
    assert ctl.allow_export_discharge is False
    now = datetime.now(UTC)
    ctl.begin_export_probe(now + timedelta(seconds=60))
    assert ctl.allow_export_discharge is False
    assert ctl.export_probe_active(now) is True
    # Mapping under the probe window must allow DISCHARGE without arming.
    assert (
        ctl._desired(BatteryIntent.EXPORT_FOR_PROFIT, now=now) is PhysicalMode.DISCHARGE
    )
    ctl.clear_export_probe()
    assert ctl.export_probe_active(now) is False
    assert ctl._desired(BatteryIntent.EXPORT_FOR_PROFIT, now=now) is PhysicalMode.AUTO


def test_preview_during_probe_matches_discharge():
    """Dashboard preview must pass `now` so an open probe shows DISCHARGE, not AUTO."""
    from datetime import UTC, datetime, timedelta

    from ems.domain import BatteryIntent, PhysicalMode

    driver = MockBatteryDriver(armed=True)
    ctl = ModeController(driver, Lifecycle(dry_run=True), dry_run=True)
    now = datetime.now(UTC)
    before = ctl.preview(
        BatteryIntent.EXPORT_FOR_PROFIT, now, observed_mode=PhysicalMode.AUTO,
    )
    assert before.desired_mode is PhysicalMode.AUTO
    ctl.begin_export_probe(now + timedelta(seconds=60))
    during = ctl.preview(
        BatteryIntent.EXPORT_FOR_PROFIT, now, observed_mode=PhysicalMode.AUTO,
    )
    assert during.desired_mode is PhysicalMode.DISCHARGE
    assert ctl.allow_export_discharge is False
    ctl.clear_export_probe()
    after = ctl.preview(
        BatteryIntent.EXPORT_FOR_PROFIT, now, observed_mode=PhysicalMode.AUTO,
    )
    assert after.desired_mode is PhysicalMode.AUTO


def test_test_battery_refuses_when_car_charging(tmp_path):
    """B-107: probe must not force DISCHARGE while the car is charging."""
    from datetime import UTC, datetime

    app, controller = _app(
        tmp_path, dry_run=False, ev_power_w=5000.0, with_override_store=True,
    )
    with TestClient(app) as c:
        c.post("/api/settings", json={"control.operational": True})
        r = c.post("/api/trading/test-battery", json={})
        assert r.status_code == 409
        body = r.json()
        assert body["ok"] is False
        assert "car" in body["detail"].lower()
        assert controller.allow_export_discharge is False
        assert controller.export_probe_active(datetime.now(UTC)) is False
        assert app.state.trading_probe_active is False
        assert app.state.control_service._ctx.override_box["ov"] == OVERRIDE_NONE


def test_test_battery_failed_decide_leaves_no_override(tmp_path, monkeypatch):
    """B-107: 409/failure path must not leave an EXPORT override or probe window."""
    from datetime import UTC, datetime

    from ems.control.mode_controller import ActionDecision
    from ems.domain import BatteryIntent, PhysicalMode

    app, controller = _app(tmp_path, dry_run=False, with_override_store=True)
    with TestClient(app) as c:
        c.post("/api/settings", json={"control.operational": True})

        def _boom(*_a, **_k):
            return ActionDecision(
                intent=BatteryIntent.EXPORT_FOR_PROFIT,
                desired_mode=PhysicalMode.AUTO,
                applied=False,
                outcome="rejected",
                reason="forced failure for test",
            )

        monkeypatch.setattr(controller, "decide", _boom)
        r = c.post("/api/trading/test-battery", json={})
        assert r.status_code == 409
        assert r.json()["ok"] is False
        assert controller.export_probe_active(datetime.now(UTC)) is False
        assert controller.allow_export_discharge is False
        assert app.state.trading_probe_active is False
        assert app.state.control_service._ctx.override_box["ov"] == OVERRIDE_NONE


def test_test_battery_success_discharges_then_restores(tmp_path, monkeypatch):
    """CONTROLLING probe writes DISCHARGE, leaves the arm flag off, then restores AUTO."""
    import asyncio
    import threading
    import time
    from datetime import UTC, datetime, timedelta

    from ems.domain import BatteryIntent, PhysicalMode
    from ems.lifecycle import OwnershipState

    driver = MockBatteryDriver(armed=True)
    started = datetime.now(UTC)
    lc = Lifecycle(dry_run=False, startup_grace_seconds=0)
    lc.start(started - timedelta(seconds=1))
    lc.mark_sensors_validated()
    lc.mark_probe_ok()
    lc.mark_plan_loaded()
    lc.tick(datetime.now(UTC))
    assert lc.state is OwnershipState.CONTROLLING
    controller = ModeController(driver, lc, dry_run=False)

    release = threading.Event()
    real_sleep = asyncio.sleep

    async def _gated(delay, result=None):
        if delay >= 60:
            while not release.is_set():
                await real_sleep(0.01)
            return result
        return await real_sleep(delay, result)

    monkeypatch.setattr(asyncio, "sleep", _gated)
    app, bound = _app(
        tmp_path, dry_run=False, with_override_store=True, controller=controller,
    )
    assert bound is controller
    try:
        with TestClient(app) as c:
            c.post("/api/settings", json={"control.operational": True})
            r = c.post("/api/trading/test-battery", json={})
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["ok"] is True
            assert body["mode"] == "discharge"
            assert body["allow_export_discharge"] is False
            assert controller.allow_export_discharge is False
            assert lc.state is OwnershipState.CONTROLLING
            assert driver.current_mode() is PhysicalMode.DISCHARGE
            assert driver.last_power_w == 4000.0
            assert driver.last_target_soc == 10.0
            ov = app.state.control_service._ctx.override_box["ov"]
            assert ov.intent is BatteryIntent.EXPORT_FOR_PROFIT
            assert controller.export_probe_active(datetime.now(UTC)) is True

            release.set()
            deadline = time.monotonic() + 3.0
            restored = False
            while time.monotonic() < deadline:
                ov = app.state.control_service._ctx.override_box["ov"]
                if (
                    driver.current_mode() is PhysicalMode.AUTO
                    and ov == OVERRIDE_NONE
                    and not app.state.trading_probe_active
                    and not controller.export_probe_active(datetime.now(UTC))
                ):
                    restored = True
                    break
                time.sleep(0.02)
            assert restored, (
                driver.current_mode(),
                app.state.trading_probe_active,
                app.state.control_service._ctx.override_box["ov"],
            )
            assert controller.allow_export_discharge is False
    finally:
        release.set()
