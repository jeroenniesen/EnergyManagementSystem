from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from ems.freshness import FreshnessTracker
from ems.sense import SIGNALS
from ems.sources.battery import MockBatteryDriver
from ems.sources.forecast import MockSolarForecastSource
from ems.sources.mock import MockSource
from ems.sources.prices import MockPriceSource
from ems.storage.history import HistoryStore
from ems.storage.settings import SettingsStore
from ems.web.api import create_app


def test_diagnostics_minimal_app_reports_checks():
    # No stores/sources wired -> still returns a structured report, with warnings (not a crash).
    b = TestClient(create_app(MockSource(), dry_run=True, dev_mode="mock")).get(
        "/api/diagnostics"
    ).json()
    assert "overall" in b
    keys = {c["key"] for c in b["checks"]}
    assert {"mode", "history_store", "prices", "battery", "data_quality", "auth"} <= keys


def test_diagnostics_survives_unreachable_battery():
    # A battery whose probe() raises must show as a warn check, not crash the endpoint (500).
    class _BadBattery:
        def probe(self):
            raise RuntimeError("battery unreachable")

    app = create_app(MockSource(), dry_run=True, dev_mode="mock", battery=_BadBattery())
    r = TestClient(app).get("/api/diagnostics")
    assert r.status_code == 200
    battery = next(c for c in r.json()["checks"] if c["key"] == "battery")
    assert battery["status"] == "warn"


def test_diagnostics_fully_wired_app_is_healthy(tmp_path):
    from datetime import UTC, datetime

    fr = FreshnessTracker()
    fr.register(*SIGNALS)
    now = datetime.now(UTC)
    for sig in SIGNALS:
        fr.mark(sig, now)
    ams = ZoneInfo("Europe/Amsterdam")
    app = create_app(
        MockSource(), dry_run=True, dev_mode="mock",
        store=HistoryStore(str(tmp_path / "ems.sqlite")),
        settings_store=SettingsStore(str(tmp_path / "ems.sqlite")),
        freshness=fr,
        price_source=MockPriceSource(ams),
        solar_forecast=MockSolarForecastSource(ams),
        battery=MockBatteryDriver(),
    )
    with TestClient(app) as c:
        b = c.get("/api/diagnostics").json()
    assert b["overall"] == "ok"
    store = next(x for x in b["checks"] if x["key"] == "history_store")
    assert store["status"] == "ok"
