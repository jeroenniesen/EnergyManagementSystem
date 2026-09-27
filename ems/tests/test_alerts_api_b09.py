"""B-09 / #73 — /api/alerts wiring for sticky write outcomes (not preview)."""
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from ems.control.mode_controller import ModeController
from ems.domain import PhysicalMode
from ems.freshness import FreshnessTracker
from ems.lifecycle import Lifecycle
from ems.sense import SIGNALS
from ems.sources.battery import MockBatteryDriver
from ems.sources.mock import MockSource
from ems.sources.prices import MockPriceSource
from ems.web.api import create_app

AMS = ZoneInfo("Europe/Amsterdam")


def _fresh_tracker():
    fr = FreshnessTracker()
    fr.register(*SIGNALS)
    now = datetime.now(UTC)
    for s in SIGNALS:
        fr.mark(s, now)
    return fr


def _app(controller: ModeController):
    return create_app(
        MockSource(), dry_run=True, dev_mode="mock",
        price_source=MockPriceSource(AMS), controller=controller, freshness=_fresh_tracker(),
    )


def _controller() -> ModeController:
    return ModeController(MockBatteryDriver(), Lifecycle(dry_run=True), dry_run=True)


def test_alerts_surface_last_command_unconfirmed_not_preview():
    """preview() never emits unconfirmed — production must use last_command_unconfirmed."""
    ctl = _controller()
    ctl.last_command_unconfirmed = True
    ctl.last_write_outcome = None  # flag alone must be enough
    with TestClient(_app(ctl)) as c:
        alerts = c.get("/api/alerts").json()["alerts"]
    keys = {a["key"] for a in alerts}
    assert "battery_command_unconfirmed" in keys
    unconfirmed = next(a for a in alerts if a["key"] == "battery_command_unconfirmed")
    assert unconfirmed["ems_doing"].strip()
    assert unconfirmed["safe"].strip()
    assert unconfirmed["action"].strip()


def test_alerts_surface_sticky_failed_unrecovered():
    ctl = _controller()
    ctl.last_write_outcome = "failed_unrecovered"
    ctl.last_command_unconfirmed = True
    with TestClient(_app(ctl)) as c:
        alerts = c.get("/api/alerts").json()["alerts"]
    assert any(a["key"] == "battery_write_failed_unrecovered" for a in alerts)


def test_alerts_surface_sticky_failed_recovered():
    ctl = _controller()
    ctl.last_write_outcome = "failed_recovered"
    ctl.last_command_unconfirmed = False
    ctl.last_confirmed_action = PhysicalMode.AUTO
    with TestClient(_app(ctl)) as c:
        alerts = c.get("/api/alerts").json()["alerts"]
    assert any(a["key"] == "battery_write_failed_recovered" for a in alerts)


def test_alerts_include_ems_doing_on_every_item():
    ctl = _controller()
    with TestClient(_app(ctl)) as c:
        body = c.get("/api/alerts").json()
    assert body["alerts"]
    for a in body["alerts"]:
        assert a.get("ems_doing", "").strip(), f"{a['key']} missing ems_doing"
