"""/api/energy-forecast: recorded SoC (past) + a forward 24h projection (future) + a summary."""
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from ems.freshness import FreshnessTracker
from ems.sense import SIGNALS, Recorder
from ems.sources.forecast import MockSolarForecastSource
from ems.sources.mock import MockSource
from ems.sources.prices import MockPriceSource
from ems.storage.history import HistoryStore
from ems.storage.settings import SettingsStore
from ems.web.api import create_app

AMS = ZoneInfo("Europe/Amsterdam")


def _app(tmp_path, *, with_recorder=False):
    db = str(tmp_path / "ems.sqlite")
    store = HistoryStore(db)
    recorder = None
    if with_recorder:
        fresh = FreshnessTracker()
        fresh.register(*SIGNALS)
        recorder = Recorder(MockSource(), store, fresh, cycle_seconds=999)
    return create_app(
        MockSource(), dry_run=True, dev_mode="mock", tz=AMS,
        store=store, recorder=recorder,
        price_source=MockPriceSource(AMS),
        solar_forecast=MockSolarForecastSource(AMS),
        settings_store=SettingsStore(db),
    )


def test_energy_forecast_returns_projection_and_summary(tmp_path):
    with TestClient(_app(tmp_path)) as c:
        b = c.get("/api/energy-forecast").json()
    assert b["current_soc_pct"] is not None
    assert b["reserve_soc_pct"] == 10.0
    assert len(b["projection"]) > 0
    p0 = b["projection"][0]
    assert set(p0) >= {"start", "intent", "soc_pct", "battery_w", "grid_w", "solar_w", "load_w"}
    # Projected SoC always stays within physical bounds.
    assert all(0.0 <= s["soc_pct"] <= 100.0 for s in b["projection"])
    assert isinstance(b["summary"], str) and b["soc_end_pct"] is not None


def test_energy_forecast_includes_recorded_history(tmp_path):
    # The lifespan startup sample (via the recorder) lands in history.
    with TestClient(_app(tmp_path, with_recorder=True)) as c:
        b = c.get("/api/energy-forecast").json()
    assert len(b["history"]) >= 1
    assert "soc_pct" in b["history"][0] and "ts" in b["history"][0]


def test_energy_forecast_respects_reserve_floor_in_projection(tmp_path):
    # MockSource starts at 55% SoC. A 50% reserve floor sits just below, so overnight discharge
    # can draw the battery down but the projection must clamp at the floor — never below it.
    with TestClient(_app(tmp_path)) as c:
        c.post("/api/settings", json={"battery.min_reserve_soc": 50.0})
        b = c.get("/api/energy-forecast").json()
    assert b["reserve_soc_pct"] == 50.0
    assert all(s["soc_pct"] >= 50.0 - 1e-6 for s in b["projection"])
