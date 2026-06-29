from concurrent.futures import ThreadPoolExecutor
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from ems.domain import RawSample
from ems.sources.forecast import MockSolarForecastSource
from ems.sources.prices import MockPriceSource
from ems.storage.history import HistoryStore
from ems.storage.settings import SettingsStore
from ems.web.api import create_app

AMS = ZoneInfo("Europe/Amsterdam")


class CountingSource:
    def __init__(self) -> None:
        self.reads = 0

    def read(self) -> RawSample:
        self.reads += 1
        return RawSample(
            grid_power_w=120.0,
            solar_power_w=450.0,
            battery_power_w=80.0,
            ev_power_w=0.0,
            soc_pct=64.0,
        )


def app_for(tmp_path, source):
    db = str(tmp_path / "ems.sqlite")
    return create_app(
        source,
        dry_run=True,
        dev_mode="mock",
        tz=AMS,
        store=HistoryStore(db),
        price_source=MockPriceSource(AMS),
        solar_forecast=MockSolarForecastSource(AMS),
        settings_store=SettingsStore(db),
    )


def test_dashboard_returns_versioned_top_level_contract(tmp_path):
    src = CountingSource()
    client = TestClient(app_for(tmp_path, src))

    body = client.get("/api/dashboard").json()

    assert body["api_version"] == 1
    assert body["cache_ttl_seconds"] == 10
    assert body["server_name"] == "Home EMS"
    assert body["degraded_sections"] == []
    for key in (
        "generated_at",
        "server_time",
        "readiness",
        "status",
        "freshness",
        "strategy",
        "decision",
        "alerts",
        "battery",
        "charge_need",
        "savings",
        "energy_story",
        "ai_validation",
    ):
        assert key in body


def test_dashboard_snapshot_is_reused_inside_ttl(tmp_path):
    src = CountingSource()
    client = TestClient(app_for(tmp_path, src))

    first = client.get("/api/dashboard").json()
    second = client.get("/api/dashboard").json()

    assert second["generated_at"] == first["generated_at"]
    assert src.reads == 1


def test_concurrent_dashboard_requests_share_one_snapshot(tmp_path):
    src = CountingSource()
    client = TestClient(app_for(tmp_path, src))

    def fetch():
        return client.get("/api/dashboard").json()["generated_at"]

    with ThreadPoolExecutor(max_workers=8) as pool:
        stamps = list(pool.map(lambda _: fetch(), range(8)))

    assert len(set(stamps)) == 1
    assert src.reads == 1


def test_dashboard_degrades_section_instead_of_failing_response(tmp_path, monkeypatch):
    import ems.web.api as api

    src = CountingSource()
    client = TestClient(app_for(tmp_path, src))

    def boom(*args, **kwargs):
        raise RuntimeError("battery unavailable")

    monkeypatch.setattr(api, "battery_payload", boom, raising=False)
    body = client.get("/api/dashboard").json()

    assert "battery" in body["degraded_sections"]
    assert body["battery"]["state"] == "degraded"
    assert "temporarily unavailable" in body["battery"]["message"]
