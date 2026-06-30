"""Manual override in OPERATIONAL mode actually drives the battery AND the confirmed mode change is
recorded in the audit log. Guards: (1) the regression where the control loop's reachability check
stalled all control; (2) the user's ask — validate the mode changed and log it. Timing-tolerant."""
import time
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from ems.control.mode_controller import ModeController
from ems.domain import PhysicalMode
from ems.freshness import FreshnessTracker
from ems.lifecycle import Lifecycle
from ems.sense import SIGNALS
from ems.sources.battery import FailingMockBatteryDriver, MockBatteryDriver
from ems.sources.forecast import MockSolarForecastSource
from ems.sources.mock import MockSource
from ems.sources.prices import MockPriceSource
from ems.storage.audit import AuditStore
from ems.storage.settings import SettingsStore
from ems.web.api import create_app

AMS = ZoneInfo("Europe/Amsterdam")


def _fresh():
    fr = FreshnessTracker()
    fr.register(*SIGNALS)
    now = datetime.now(UTC)
    for s in SIGNALS:
        fr.mark(s, now)
    return fr


def _operational_app(tmp_path, driver):
    db = str(tmp_path / "ems.sqlite")
    ctl = ModeController(driver, Lifecycle(dry_run=False, startup_grace_seconds=0), dry_run=False)
    return create_app(
        MockSource(), dry_run=False, dev_mode="live", tz=AMS,
        price_source=MockPriceSource(AMS), solar_forecast=MockSolarForecastSource(AMS),
        controller=ctl, freshness=_fresh(),
        override_store=SettingsStore(db, table="runtime_state"), audit_store=AuditStore(db),
        control_cycle_seconds=0.02,
    )


def _battery_decision_entries(client):
    return [e for e in client.get("/api/audit").json()["entries"]
            if e["category"] == "battery_decision"]


def test_manual_charge_override_drives_the_battery_and_audits_the_confirmed_change(tmp_path):
    driver = MockBatteryDriver()  # starts AUTO
    with TestClient(_operational_app(tmp_path, driver)) as c:
        assert c.post("/api/override",
                      json={"intent": "grid_charge_to_target", "minutes": 30}).status_code == 200
        deadline = time.time() + 3.0
        while time.time() < deadline and driver.current_mode() is not PhysicalMode.CHARGE:
            time.sleep(0.05)
        assert driver.current_mode() is PhysicalMode.CHARGE  # the override actually took effect
        # ...AND the confirmed transition is in the audit log (the user's ask).
        decisions = _battery_decision_entries(c)
    assert decisions, "the mode change must be audited"
    top = decisions[0]
    assert top["detail"]["desired_mode"] == "charge"
    assert top["detail"]["accepted"] is True
    assert top["detail"]["outcome"] == "applied"
    assert "→ charge" in top["summary"] and "command sent" in top["summary"]


def test_manual_override_drives_battery_even_when_daily_cap_exhausted(tmp_path):
    # The live "manual charge does nothing" bug: a day of testing exhausted the daily switch cap, so
    # every "charge now" was silently blocked (cap_reached → no write, no audit). A manual override
    # is an explicit operator command and MUST bypass the cap and still drive the battery.
    db = str(tmp_path / "ems.sqlite")
    driver = MockBatteryDriver()  # starts AUTO
    ctl = ModeController(driver, Lifecycle(dry_run=False, startup_grace_seconds=0), dry_run=False)
    ctl.switches_today = 999  # cap thoroughly exhausted (any configured cap is exceeded)
    app = create_app(
        MockSource(), dry_run=False, dev_mode="live", tz=AMS,
        price_source=MockPriceSource(AMS), solar_forecast=MockSolarForecastSource(AMS),
        controller=ctl, freshness=_fresh(),
        override_store=SettingsStore(db, table="runtime_state"), audit_store=AuditStore(db),
        control_cycle_seconds=0.02,
    )
    with TestClient(app) as c:
        assert c.post("/api/override",
                      json={"intent": "grid_charge_to_target", "minutes": 30}).status_code == 200
        deadline = time.time() + 3.0
        while time.time() < deadline and driver.current_mode() is not PhysicalMode.CHARGE:
            time.sleep(0.05)
        assert driver.current_mode() is PhysicalMode.CHARGE  # bypassed the exhausted daily cap


def test_rejected_write_is_audited_as_failed(tmp_path):
    # A genuinely rejected/failed write must be audited as FAILED (not a silent "sent") — so the
    # operator can see the battery wasn't commanded.
    driver = FailingMockBatteryDriver(fail_times=99)  # every apply() fails
    with TestClient(_operational_app(tmp_path, driver)) as c:
        c.post("/api/override", json={"intent": "grid_charge_to_target", "minutes": 30})
        deadline = time.time() + 3.0
        while time.time() < deadline and not _battery_decision_entries(c):
            time.sleep(0.05)
        decisions = _battery_decision_entries(c)
    assert decisions, "a failed write must still be audited"
    top = decisions[0]
    assert top["detail"]["accepted"] is False
    assert top["detail"]["outcome"] in ("failed_recovered", "failed_unrecovered")
    assert "FAILED" in top["summary"]
