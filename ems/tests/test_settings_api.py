from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from ems.control.mode_controller import ModeController
from ems.lifecycle import Lifecycle
from ems.sources.battery import MockBatteryDriver
from ems.sources.mock import MockSource
from ems.sources.prices import MockPriceSource
from ems.storage.settings import SettingsStore
from ems.web.api import create_app


def _app(tmp_path, **kw):
    return create_app(
        MockSource(), dry_run=True, dev_mode="mock",
        settings_store=SettingsStore(str(tmp_path / "ems.sqlite")), **kw,
    )


def test_get_settings_returns_schema_and_defaults(tmp_path):
    with TestClient(_app(tmp_path)) as c:
        b = c.get("/api/settings").json()
    assert any(f["key"] == "ui.theme" for f in b["schema"])
    assert b["values"]["ui.theme"] == "auto"  # default until changed
    assert b["values"]["planner.charge_slots"] == 12


def test_post_settings_persists_and_is_reflected(tmp_path):
    with TestClient(_app(tmp_path)) as c:
        r = c.post("/api/settings", json={"ui.theme": "dark", "planner.charge_slots": 8})
        assert r.status_code == 200
        assert r.json()["values"]["ui.theme"] == "dark"
        assert c.get("/api/settings").json()["values"]["planner.charge_slots"] == 8


def test_post_settings_survives_restart(tmp_path):
    # A second app on the same DB must load the persisted value (real persistence, not memory).
    with TestClient(_app(tmp_path)) as c:
        c.post("/api/settings", json={"ui.theme": "light"})
    with TestClient(_app(tmp_path)) as c2:
        assert c2.get("/api/settings").json()["values"]["ui.theme"] == "light"


def test_post_invalid_settings_returns_422_and_does_not_persist(tmp_path):
    with TestClient(_app(tmp_path)) as c:
        r = c.post("/api/settings", json={"ui.theme": "neon", "planner.charge_slots": 8})
        assert r.status_code == 422
        assert "ui.theme" in r.json()["errors"]
        # Whole payload rejected — the valid key must NOT have been saved either.
        assert c.get("/api/settings").json()["values"]["planner.charge_slots"] == 12


def test_post_settings_without_store_returns_503():
    app = create_app(MockSource(), dry_run=True, dev_mode="mock")  # no settings_store
    r = TestClient(app).post("/api/settings", json={"ui.theme": "dark"})
    assert r.status_code == 503


def test_control_settings_applied_to_controller_live(tmp_path):
    controller = ModeController(MockBatteryDriver(), Lifecycle(dry_run=True), dry_run=True)
    app = _app(tmp_path, controller=controller)
    with TestClient(app) as c:
        c.post("/api/settings", json={"control.max_switches_per_day": 3,
                                      "control.allow_export_discharge": True})
        assert controller.max_switches_per_day == 3
        assert controller.allow_export_discharge is True


def test_safety_limits_reject_unsafe_values_and_leave_controller_safe(tmp_path):
    # The dwell floor (60s), the switch-cap ceiling (20) and the bool-only export flag must all
    # reject bad values via POST and leave the live controller at its safe defaults.
    controller = ModeController(MockBatteryDriver(), Lifecycle(dry_run=True), dry_run=True)
    app = _app(tmp_path, controller=controller)
    with TestClient(app) as c:
        r = c.post("/api/settings", json={
            "control.min_dwell_seconds": 0,          # below the 60s floor
            "control.max_switches_per_day": 48,       # above the 20 ceiling
            "control.allow_export_discharge": 1,      # not a real bool
        })
        assert r.status_code == 422
        errs = r.json()["errors"]
        assert {"control.min_dwell_seconds", "control.max_switches_per_day",
                "control.allow_export_discharge"} <= set(errs)
    # Rejected payload must not have mutated the controller's safe defaults.
    assert controller.allow_export_discharge is False
    assert controller.max_switches_per_day == 10
    assert controller.min_dwell.total_seconds() == 600.0


def test_planner_settings_change_the_plan(tmp_path):
    # With an impossibly large risk margin, no trade can clear break-even -> all self-consumption.
    app = _app(tmp_path, price_source=MockPriceSource(ZoneInfo("Europe/Amsterdam")))
    with TestClient(app) as c:
        c.post("/api/settings", json={"planner.risk_margin_eur_per_kwh": 0.5})
        plan = c.get("/api/plan").json()
    assert all(s["intent"] == "allow_self_consumption" for s in plan["slots"])
