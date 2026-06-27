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

