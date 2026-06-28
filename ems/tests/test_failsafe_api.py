from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from ems.control.mode_controller import ModeController
from ems.freshness import FreshnessTracker
from ems.lifecycle import Lifecycle
from ems.sense import SIGNALS
from ems.sources.battery import MockBatteryDriver
from ems.sources.mock import MockSource
from ems.sources.prices import MockPriceSource
from ems.storage.settings import SettingsStore
from ems.web.api import create_app

AMS = ZoneInfo("Europe/Amsterdam")


def _controller():
    return ModeController(MockBatteryDriver(), Lifecycle(dry_run=True), dry_run=True)


def _fresh_tracker():
    fr = FreshnessTracker()
    fr.register(*SIGNALS)
    now = datetime.now(UTC)
    for s in SIGNALS:
        fr.mark(s, now)
    return fr


def test_unsafe_data_forces_self_consumption_in_decision():
    # No freshness tracker -> critical signals missing -> data quality unsafe. Whatever the plan
    # would have wanted, the decision must fall back to self-consumption.
    app = create_app(
        MockSource(), dry_run=True, dev_mode="mock",
        price_source=MockPriceSource(AMS), controller=_controller(),
    )
    b = TestClient(app).get("/api/decision").json()
    assert b["intent"] == "allow_self_consumption"


def test_complete_data_does_not_trigger_failsafe():
    app = create_app(
        MockSource(), dry_run=True, dev_mode="mock",
        price_source=MockPriceSource(AMS), controller=_controller(), freshness=_fresh_tracker(),
    )
    b = TestClient(app).get("/api/decision").json()
    assert "fail-safe" not in (b["plan_reason"] or "")


def _override_app(tmp_path, *, freshness=None):
    return create_app(
        MockSource(), dry_run=True, dev_mode="mock",
        price_source=MockPriceSource(AMS), controller=_controller(), freshness=freshness,
        override_store=SettingsStore(str(tmp_path / "ems.sqlite"), table="runtime_state"),
    )


def test_risky_override_is_HELD_under_unsafe_data(tmp_path):
    # Energy review #5: EMS must NOT force charge/discharge when critical data is unsafe — it can't
    # trust SoC/reachability. The override stays "active" but is held to self-consumption.
    with TestClient(_override_app(tmp_path)) as c:  # no freshness → unsafe
        c.post("/api/override", json={"intent": "grid_charge_to_target", "minutes": 30})
        b = c.get("/api/decision").json()
        alerts = c.get("/api/alerts").json()["alerts"]
    assert b["intent"] == "allow_self_consumption"
    assert b["override_active"] is True and "held" in b["plan_reason"]
    # The alert must say HELD — never claim it's actually forcing the requested charge.
    ov_alert = next(a for a in alerts if a["key"] == "manual_override_active")
    assert "held" in ov_alert["message"]
    assert "forcing grid_charge_to_target" not in ov_alert["message"]


def test_risky_override_is_honoured_when_data_is_safe(tmp_path):
    with TestClient(_override_app(tmp_path, freshness=_fresh_tracker())) as c:
        c.post("/api/override", json={"intent": "grid_charge_to_target", "minutes": 30})
        b = c.get("/api/decision").json()
    assert b["intent"] == "grid_charge_to_target" and b["override_active"] is True


def test_self_consumption_override_is_always_allowed_even_unsafe(tmp_path):
    with TestClient(_override_app(tmp_path)) as c:  # unsafe data
        c.post("/api/override", json={"intent": "allow_self_consumption", "minutes": 30})
        b = c.get("/api/decision").json()
    assert b["intent"] == "allow_self_consumption" and b["override_active"] is True
