import asyncio

from fastapi.testclient import TestClient

from ems.domain import RawSample
from ems.load_model import reconstruct
from ems.sources.mock import MockSource
from ems.storage.history import HistoryStore
from ems.web.api import create_app


def _client():
    return TestClient(create_app(MockSource(), dry_run=True, dev_mode="mock"))


def _seeded_client(tmp_path):
    store = HistoryStore(str(tmp_path / "ems.sqlite"))
    raw = RawSample(
        grid_power_w=200, solar_power_w=0, battery_power_w=800, ev_power_w=0, soc_pct=55
    )

    async def seed():
        await store.init()
        await store.record("2026-06-27T10:00:00+02:00", raw, reconstruct(raw))

    asyncio.run(seed())
    return TestClient(create_app(MockSource(), dry_run=True, dev_mode="mock", store=store))


def test_raw_csv_has_header_and_row(tmp_path):
    r = _seeded_client(tmp_path).get("/api/export?kind=raw&format=csv")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"]
    lines = r.text.strip().splitlines()
    assert lines[0].split(",") == ["ts", "grid_power_w", "solar_power_w", "battery_power_w",
                                   "ev_power_w", "soc_pct"]
    assert "200" in lines[1] and "55" in lines[1]


def test_derived_csv_header(tmp_path):
    r = _seeded_client(tmp_path).get("/api/export?kind=derived&format=csv")
    assert r.text.splitlines()[0] == "ts,house_load_w,non_ev_load_w"
    assert "1000" in r.text  # reconstructed house load


def test_json_format_returns_rows(tmp_path):
    r = _seeded_client(tmp_path).get("/api/export?kind=raw&format=json")
    assert r.status_code == 200
    body = r.json()
    assert body[0]["grid_power_w"] == 200


def test_csv_header_present_without_store():
    # No store wired -> still a valid CSV with just the header (no crash).
    r = _client().get("/api/export?kind=raw&format=csv")
    assert r.status_code == 200
    assert r.text.strip() == "ts,grid_power_w,solar_power_w,battery_power_w,ev_power_w,soc_pct"


def test_invalid_params_rejected():
    assert _client().get("/api/export?kind=bogus").status_code == 422
    assert _client().get("/api/export?format=xml").status_code == 422
    assert _client().get("/api/export?limit=0").status_code == 422
    assert _client().get("/api/export?limit=99999").status_code == 422
