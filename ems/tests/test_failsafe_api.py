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


def test_manual_override_bypasses_failsafe_under_unsafe_data(tmp_path):
    # An explicit, time-boxed operator override is honoured even with unsafe data (deliberate).
    app = create_app(
        MockSource(), dry_run=True, dev_mode="mock",
        price_source=MockPriceSource(AMS), controller=_controller(),
        override_store=SettingsStore(str(tmp_path / "ems.sqlite"), table="runtime_state"),
    )
    with TestClient(app) as c:
        c.post("/api/override", json={"intent": "grid_charge_to_target", "minutes": 30})
        b = c.get("/api/decision").json()
    assert b["intent"] == "grid_charge_to_target"
    assert b["override_active"] is True
