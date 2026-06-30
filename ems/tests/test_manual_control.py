"""Manual override in OPERATIONAL mode actually drives the battery: setting a grid-charge override
must take the (mock) battery to CHARGE via the live control loop. Guards the regression where the
control loop's reachability check stalled all control. Timing-tolerant (polls the driver)."""
import time
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from ems.control.mode_controller import ModeController
from ems.domain import PhysicalMode
from ems.freshness import FreshnessTracker
from ems.lifecycle import Lifecycle
from ems.sense import SIGNALS
from ems.sources.battery import MockBatteryDriver
from ems.sources.forecast import MockSolarForecastSource
from ems.sources.mock import MockSource
from ems.sources.prices import MockPriceSource
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


def test_manual_charge_override_drives_the_battery_in_operational_mode(tmp_path):
    driver = MockBatteryDriver()  # starts AUTO
    # Operational (dry_run False), no startup grace so the loop reaches CONTROLLING promptly.
    ctl = ModeController(driver, Lifecycle(dry_run=False, startup_grace_seconds=0), dry_run=False)
    db = str(tmp_path / "ems.sqlite")
    app = create_app(
        MockSource(), dry_run=False, dev_mode="live", tz=AMS,
        price_source=MockPriceSource(AMS), solar_forecast=MockSolarForecastSource(AMS),
        controller=ctl, freshness=_fresh(),
        override_store=SettingsStore(db, table="runtime_state"),
        control_cycle_seconds=0.02,
    )
    with TestClient(app) as c:
        r = c.post("/api/override", json={"intent": "grid_charge_to_target", "minutes": 30})
        assert r.status_code == 200
        deadline = time.time() + 3.0
        while time.time() < deadline and driver.current_mode() is not PhysicalMode.CHARGE:
            time.sleep(0.05)
    assert driver.current_mode() is PhysicalMode.CHARGE  # the override actually took effect
