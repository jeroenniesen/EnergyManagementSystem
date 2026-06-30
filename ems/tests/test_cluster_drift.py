"""Cluster-consistency on the switching path: a tower that doesn't follow the commanded mode (the
silent slave-not-following bug) must be DETECTED and audited, not reported as a clean switch."""
import time
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from ems.control.mode_controller import ModeController
from ems.domain import PhysicalMode, RawSample
from ems.freshness import FreshnessTracker
from ems.lifecycle import Lifecycle
from ems.sense import SIGNALS
from ems.sources.battery import MockBatteryDriver
from ems.sources.indevolt import TowerReading
from ems.sources.prices import MockPriceSource
from ems.storage.audit import AuditStore
from ems.storage.settings import SettingsStore
from ems.web.api import _commanded_family, _tower_family, create_app

AMS = ZoneInfo("Europe/Amsterdam")


def test_mode_family_distinguishes_self_consumption_from_realtime():
    # The stable distinction the drift check keys off: self-consumption (mode 1) vs real-time
    # (mode 4: standby/charging/discharging). Transient charge/discharge states are all "real-time".
    assert _tower_family("self-consumption") == "self-consumption"
    assert _tower_family("standby") == "real-time"
    assert _tower_family("charging") == "real-time"
    assert _tower_family("discharging") == "real-time"
    assert _tower_family("outdoor") is None  # not judged
    assert _commanded_family(PhysicalMode.AUTO) == "self-consumption"
    assert _commanded_family(PhysicalMode.IDLE) == "real-time"
    assert _commanded_family(PhysicalMode.CHARGE) == "real-time"


class _Cluster:
    def __init__(self, towers):
        self.towers = towers

    def read_towers(self):
        return self.towers

    def read_power_soc(self):
        online = [t for t in self.towers if t.online and t.soc_pct is not None]
        return sum(t.power_w for t in online), sum(t.soc_pct for t in online) / len(online)


class _Source:
    """A live-shaped source whose battery cluster reports fixed per-tower modes."""

    def __init__(self, towers):
        self.battery = _Cluster(towers)

    def read(self):
        power, soc = self.battery.read_power_soc()
        return RawSample(grid_power_w=0.0, solar_power_w=0.0, battery_power_w=power,
                         ev_power_w=0.0, soc_pct=soc)


def _fresh():
    fr = FreshnessTracker()
    fr.register(*SIGNALS)
    now = datetime.now(UTC)
    for s in SIGNALS:
        fr.mark(s, now)
    return fr


def test_slave_not_following_is_detected_and_audited(tmp_path):
    # Master is in self-consumption (matches the commanded ALLOW_SELF_CONSUMPTION → idempotent), but
    # the slave is stuck in real-time standby. The steady-state cluster check must audit the
    # mismatch so it isn't silent.
    towers = [
        TowerReading("10.0.0.1", 50.0, 0.0, 5.4, "master", True, mode="self-consumption"),
        TowerReading("10.0.0.2", 50.0, 0.0, 5.4, "slave", True, mode="standby"),
    ]
    db = str(tmp_path / "ems.sqlite")
    ctl = ModeController(MockBatteryDriver(), Lifecycle(dry_run=False, startup_grace_seconds=0),
                         dry_run=False)
    app = create_app(
        _Source(towers), dry_run=False, dev_mode="live", tz=AMS,
        price_source=MockPriceSource(AMS), controller=ctl, freshness=_fresh(),
        override_store=SettingsStore(db, table="runtime_state"), audit_store=AuditStore(db),
        control_cycle_seconds=0.02,
    )
    with TestClient(app) as c:
        # Pin a self-consumption override so the master read is idempotent (steady state) — the path
        # that runs the cluster-consistency check.
        c.post("/api/override", json={"intent": "allow_self_consumption", "minutes": 30})

        def _drift():
            return [e for e in c.get("/api/audit").json()["entries"]
                    if e["detail"].get("event") == "cluster_drift"]

        deadline = time.time() + 3.0
        while time.time() < deadline and not _drift():
            time.sleep(0.05)
        drift = _drift()
    assert drift, "a slave not following the commanded mode must be audited"
    assert "10.0.0.2" in drift[0]["detail"]["laggards"]
    assert "MISMATCH" in drift[0]["summary"]
