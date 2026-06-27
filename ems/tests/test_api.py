import asyncio

from fastapi.testclient import TestClient

from ems.domain import RawSample
from ems.load_model import reconstruct
from ems.sources.mock import MockSource
from ems.storage.history import HistoryStore
from ems.web.api import create_app


def _client():
    return TestClient(create_app(MockSource(), dry_run=True, dev_mode="mock"))


def test_health_live():
    r = _client().get("/health/live")
    assert r.status_code == 200
    assert r.json()["status"] == "alive"


def test_health_ready_reports_mode():
    r = _client().get("/health/ready")
    assert r.status_code == 200
    body = r.json()
    assert body["dry_run"] is True
    assert body["dev_mode"] == "mock"


def test_status_reconstructs_house_load():
    r = _client().get("/api/status")
    assert r.status_code == 200
    body = r.json()
    assert body["house_load_w"] == 1000  # 200 + 0 + 800 (MockSource)
    assert body["non_ev_load_w"] == 1000
    assert body["soc_pct"] == 55
    assert body["dry_run"] is True


def test_series_empty_without_store():
    r = _client().get("/api/series")
    assert r.status_code == 200
    assert r.json() == {"raw": [], "derived": []}


def test_series_returns_recorded_samples(tmp_path):
    store = HistoryStore(str(tmp_path / "ems.sqlite"))
    raw = RawSample(
        grid_power_w=200, solar_power_w=0, battery_power_w=800, ev_power_w=0, soc_pct=55
    )

    async def seed():
        await store.init()
        await store.record("2026-06-27T10:00:00+02:00", raw, reconstruct(raw))

    asyncio.run(seed())
    client = TestClient(create_app(MockSource(), dry_run=True, dev_mode="mock", store=store))
    body = client.get("/api/series").json()
    assert body["raw"][0]["grid_power_w"] == 200
    assert body["derived"][0]["house_load_w"] == 1000


def test_lifespan_auto_inits_store(tmp_path):
    # A fresh store (init NEVER called manually); the FastAPI lifespan must create the schema,
    # so /api/series works without erroring. Using TestClient as a context manager runs lifespan.
    store = HistoryStore(str(tmp_path / "ems.sqlite"))
    app = create_app(MockSource(), dry_run=True, dev_mode="mock", store=store)
    with TestClient(app) as client:
        r = client.get("/api/series")
        assert r.status_code == 200
        assert r.json() == {"raw": [], "derived": []}


def test_series_rejects_out_of_range_limit():
    r = _client().get("/api/series?limit=0")
    assert r.status_code == 422  # ge=1
    r2 = _client().get("/api/series?limit=999999")
    assert r2.status_code == 422  # le=2000


def test_serves_spa_index_when_static_dir_present(tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<!doctype html><title>EMS</title><div id=root></div>")
    app = create_app(MockSource(), dry_run=True, dev_mode="mock", static_dir=str(dist))
    client = TestClient(app)
    assert client.get("/api/status").status_code == 200  # API still matched first
    r = client.get("/")
    assert r.status_code == 200
    assert "EMS" in r.text  # SPA index served at "/"


def test_prices_endpoint_returns_slots_and_current():
    from zoneinfo import ZoneInfo

    from ems.sources.prices import MockPriceSource

    app = create_app(
        MockSource(),
        dry_run=True,
        dev_mode="mock",
        price_source=MockPriceSource(ZoneInfo("Europe/Amsterdam")),
    )
    b = TestClient(app).get("/api/prices").json()
    assert b["resolution"] == "quarter_hourly"
    assert b["currency"] == "EUR"
    assert len(b["slots"]) == 192  # today + tomorrow, 15-min
    assert b["current_eur_per_kwh"] is not None


def test_prices_endpoint_without_source():
    b = _client().get("/api/prices").json()
    assert b["slots"] == []
    assert b["current_eur_per_kwh"] is None


def test_forecast_endpoint_returns_slots_and_today_kwh():
    from zoneinfo import ZoneInfo

    from ems.sources.forecast import MockSolarForecastSource

    app = create_app(
        MockSource(),
        dry_run=True,
        dev_mode="mock",
        solar_forecast=MockSolarForecastSource(ZoneInfo("Europe/Amsterdam")),
    )
    b = TestClient(app).get("/api/forecast").json()
    assert len(b["slots"]) == 192
    assert b["today_kwh_p50"] is not None
    assert b["slots"][0]["p10_w"] <= b["slots"][0]["p50_w"] <= b["slots"][0]["p90_w"]


def test_forecast_endpoint_without_source():
    b = _client().get("/api/forecast").json()
    assert b["slots"] == []
    assert b["today_kwh_p50"] is None


def test_plan_endpoint_returns_slots_and_current_intent():
    from zoneinfo import ZoneInfo

    from ems.sources.prices import MockPriceSource

    app = create_app(
        MockSource(),
        dry_run=True,
        dev_mode="mock",
        price_source=MockPriceSource(ZoneInfo("Europe/Amsterdam")),
    )
    b = TestClient(app).get("/api/plan").json()
    assert len(b["slots"]) > 0
    assert b["current_intent"] in {
        "allow_self_consumption", "grid_charge_to_target", "hold_reserve", "discharge_for_load",
    }
    assert b["slots"][0]["reason"]


def test_plan_endpoint_without_source():
    b = _client().get("/api/plan").json()
    assert b["slots"] == []
    assert b["current_intent"] is None


def test_unknown_api_path_returns_json_404(tmp_path):
    # An unknown /api/* path must be JSON 404, not the SPA index served as 200.
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<!doctype html><div id=root></div>")
    app = create_app(MockSource(), dry_run=True, dev_mode="mock", static_dir=str(dist))
    r = TestClient(app).get("/api/nonexistent")
    assert r.status_code == 404
    assert r.headers["content-type"].startswith("application/json")

