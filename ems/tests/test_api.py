from fastapi.testclient import TestClient

from ems.sources.mock import MockSource
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
