"""Failure-state contract matrix for read and control API surfaces."""

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from ems.freshness import FreshnessTracker
from ems.sense import SIGNALS
from ems.sources.mock import MockSource
from ems.storage.settings import SettingsStore
from ems.web.api import create_app
from ems.web.models import DiagnosticsResponse, PlanResponse, ReportResponse

READS = (
    ("/api/plan", PlanResponse),
    ("/api/report?period=day", ReportResponse),
    ("/api/diagnostics", DiagnosticsResponse),
)


def test_empty_sources_return_safe_200_payloads():
    """An unconfigured installation remains inspectable and model-valid."""
    with TestClient(create_app(MockSource(), dry_run=True, dev_mode="mock")) as client:
        for path, model in READS:
            response = client.get(path)
            assert response.status_code == 200
            model.model_validate(response.json())
        decision = client.get("/api/decision")
        assert decision.status_code == 200
        assert decision.json()["outcome"] == "unconfigured"


def test_stale_inputs_fail_safe_without_control_writes():
    freshness = FreshnessTracker()
    freshness.register(*SIGNALS)
    old = datetime.now(UTC) - timedelta(hours=2)
    for signal in SIGNALS:
        freshness.mark(signal, old)
    app = create_app(MockSource(), dry_run=True, dev_mode="mock", freshness=freshness)
    with TestClient(app) as client:
        plan = client.get("/api/plan")
        assert plan.status_code == 200
        PlanResponse.model_validate(plan.json())
        decision = client.get("/api/decision")
        assert decision.status_code == 200
        body = decision.json()
        assert body["intent"] in {None, "allow_self_consumption"}
        assert body["applied"] is False


def test_unavailable_sources_are_reported_not_raised():
    class BrokenBattery:
        def probe(self):
            raise RuntimeError("source unavailable")

    app = create_app(MockSource(), dry_run=True, dev_mode="mock", battery=BrokenBattery())
    with TestClient(app) as client:
        response = client.get("/api/diagnostics")
        assert response.status_code == 200
        body = DiagnosticsResponse.model_validate(response.json())
        assert body.overall in {"warn", "degraded", "error", "fail", None}
        battery_check = next(check for check in body.checks or [] if check["key"] == "battery")
        assert battery_check["status"] == "warn"


def test_reads_require_auth_when_enabled(tmp_path):
    token = "matrix-token"
    app = create_app(
        MockSource(), dry_run=True, dev_mode="mock", web_auth_token=token,
        settings_store=SettingsStore(str(tmp_path / "ems.sqlite")),
    )
    auth = {"Authorization": f"Bearer {token}"}
    with TestClient(app) as client:
        settings_response = client.post(
            "/api/settings", json={"web.require_auth": True}, headers=auth
        )
        assert settings_response.status_code == 200
        for path, _model in READS:
            assert client.get(path).status_code == 401
            assert client.get(path, headers=auth).status_code == 200
        assert client.get("/api/decision").status_code == 401
        assert client.get("/api/decision", headers=auth).status_code == 200


def test_dry_run_control_response_is_explicit_and_safe():
    with TestClient(create_app(MockSource(), dry_run=True, dev_mode="mock")) as client:
        body = client.get("/api/decision").json()
        assert body["applied"] is False
        assert body["outcome"] in {"dry_run", "unconfigured"}
        assert body.get("writes", 0) in (0, None)
