"""A control cycle applies the battery mode in a worker thread, then commits the audit row
on the event loop. A reader that polls the mode and immediately GETs /api/audit must still
see that cycle's row: the insert is durable, and the read must not slip into the gap (#379).

The poll does not wait on the audit. ``sleep(0)`` only lets the app thread run; it is
the same gap the 50 ms poll in the car-session and manual-override tests can land in
under load.
"""
import time

from fastapi.testclient import TestClient

from ems.domain import PhysicalMode
from ems.sources.battery import MockBatteryDriver
from ems.tests.test_car_session import (
    _audit_summaries,
    _CarSource,
    _CountingDriver,
    _operational_car_app,
)
from ems.tests.test_manual_control import _battery_decision_entries, _operational_app


def _until(cond, timeout=4.0):
    """Poll with no delay, so the read runs in the post-apply / pre-commit gap."""
    deadline = time.time() + timeout
    while time.time() < deadline and not cond():
        time.sleep(0)
    return cond()


def test_session_end_audit_is_visible_as_soon_as_the_mode_is(tmp_path):
    driver = _CountingDriver()
    with TestClient(_operational_car_app(tmp_path, _CarSource(3000.0), driver)) as c:
        c.post("/api/settings", json={"strategy.mode": "winter",
                                      "control.car_charging_battery_mode": "static_discharge",
                                      "control.car_discharge_w": 900})
        assert _until(lambda: driver.current_mode() is PhysicalMode.DISCHARGE)
        c.post("/api/settings", json={"control.hold_battery_when_car_charging": False})
        assert _until(lambda: driver.current_mode() is PhysicalMode.AUTO)
        ended = _audit_summaries(c, "car session ended")
    assert ended, "the session end must be audited"


def test_manual_charge_audit_is_visible_as_soon_as_the_mode_is(tmp_path):
    driver = MockBatteryDriver()
    with TestClient(_operational_app(tmp_path, driver)) as c:
        assert c.post("/api/override",
                      json={"intent": "grid_charge_to_target", "minutes": 30}).status_code == 200
        assert _until(lambda: driver.current_mode() is PhysicalMode.CHARGE)
        decisions = _battery_decision_entries(c)
    assert decisions, "the mode change must be audited"
    top = decisions[0]
    assert top["detail"]["desired_mode"] == "charge"
    assert top["detail"]["accepted"] is True
    assert top["detail"]["outcome"] == "applied"
