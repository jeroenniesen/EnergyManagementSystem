"""#246 / B-35 slice 1: GET /api/automation-bounds contract."""
from fastapi.testclient import TestClient

from ems.sources.battery import MockBatteryDriver
from ems.sources.mock import MockSource
from ems.storage.history import HistoryStore
from ems.storage.settings import SettingsStore
from ems.web.api import create_app


def _app(tmp_path, *, dry_run=True, battery=None):
    db = str(tmp_path / "ems.sqlite")
    return create_app(
        MockSource(),
        dry_run=dry_run,
        config_dry_run=False,
        dev_mode="mock",
        store=HistoryStore(db),
        settings_store=SettingsStore(db),
        battery=battery or MockBatteryDriver(),
    )


def test_automation_bounds_lists_core_rows(tmp_path):
    with TestClient(_app(tmp_path)) as c:
        r = c.get("/api/automation-bounds")
        assert r.status_code == 200
        body = r.json()
    assert body["title"] == "What EMS may do"
    assert "summary" in body
    assert body["writes_allowed"] is False  # dry_run mock
    ids = [b["id"] for b in body["bounds"]]
    for need in (
        "battery_writes", "watch_only", "operational", "min_reserve_soc",
        "night_reserve_kwh", "max_charge_w", "max_discharge_w",
        "grid_fuse", "grid_limit_w", "export_armed",
    ):
        assert need in ids, need
    for row in body["bounds"]:
        assert row["status"] in ("known", "unknown", "effective")
        assert row["display"]
        assert row["explanation"]


def test_automation_bounds_fuse_unset_is_unknown(tmp_path):
    with TestClient(_app(tmp_path)) as c:
        body = c.get("/api/automation-bounds").json()
    fuse = next(b for b in body["bounds"] if b["id"] == "grid_fuse")
    assert fuse["status"] == "unknown"
    assert fuse["display"] == "Not confirmed"
    assert fuse["value"] is None


def test_automation_bounds_fuse_confirmed_is_known(tmp_path):
    with TestClient(_app(tmp_path)) as c:
        assert c.post("/api/settings", json={"control.grid_fuse": "3x25"}).status_code == 200
        body = c.get("/api/automation-bounds").json()
    fuse = next(b for b in body["bounds"] if b["id"] == "grid_fuse")
    assert fuse["status"] == "known"
    assert "3×25" in fuse["display"]
    limit = next(b for b in body["bounds"] if b["id"] == "grid_limit_w")
    assert limit["status"] == "known"
    assert limit["value"] == 17250.0


def test_automation_bounds_power_uses_capability_min(tmp_path):
    """Mock probe advertises 4000 W; settings lower → effective is settings."""
    with TestClient(_app(tmp_path)) as c:
        assert c.post("/api/settings", json={
            "battery.max_charge_w": 2000,
            "battery.max_discharge_w": 1800,
            "control.grid_fuse": "3x25",
        }).status_code == 200
        body = c.get("/api/automation-bounds").json()
    charge = next(b for b in body["bounds"] if b["id"] == "max_charge_w")
    assert charge["status"] == "effective"
    assert charge["value"] == 2000.0
    discharge = next(b for b in body["bounds"] if b["id"] == "max_discharge_w")
    assert discharge["value"] == 1800.0


def test_automation_bounds_survives_failed_probe(tmp_path):
    class _Bad:
        def probe(self):
            raise RuntimeError("offline")

        @property
        def armed(self):
            return False

        def configure_power_limits(self, *, max_charge_w, max_discharge_w):
            return None

        def current_mode(self):
            from ems.domain import PhysicalMode
            return PhysicalMode.AUTO

        def apply(self, mode, *, target_soc=None, power_w=None):
            return False

    with TestClient(_app(tmp_path, battery=_Bad())) as c:
        r = c.get("/api/automation-bounds")
        assert r.status_code == 200
        charge = next(b for b in r.json()["bounds"] if b["id"] == "max_charge_w")
    assert charge["status"] == "unknown"
    assert charge["display"] == "Unknown"
    assert "did not report" in charge["explanation"].lower()


def test_automation_bounds_homeowner_summary_no_config_jargon(tmp_path):
    with TestClient(_app(tmp_path, dry_run=True)) as c:
        body = c.get("/api/automation-bounds").json()
    assert "config.yaml" not in body["summary"].lower()
    assert "dev.mode" not in body["summary"].lower()


def test_build_automation_bounds_manual_override_blocks_writes():
    from ems.automation_bounds import build_automation_bounds
    from ems.settings import defaults

    body = build_automation_bounds(
        defaults(),
        dry_run=False,
        config_dry_run=False,
        dev_mode="live",
        lifecycle_state="manual_override",
        probe_ok=None,
    )
    assert body["writes_allowed"] is False
    writes = next(b for b in body["bounds"] if b["id"] == "battery_writes")
    assert writes["display"] == "Blocked"
