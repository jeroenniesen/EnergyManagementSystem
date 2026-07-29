"""Contract checks for the typed response boundaries."""

from fastapi.testclient import TestClient

from ems.sources.mock import MockSource
from ems.web.api import create_app
from ems.web.models import (
    DiagnosticsResponse,
    FinanceResponse,
    PlanResponse,
    ReportResponse,
    SavingsResponse,
    VerificationResponse,
)


def test_routes_validate_representative_payloads():
    client = TestClient(create_app(MockSource(), dry_run=True, dev_mode="mock"))
    endpoints = {
        "/api/plan": PlanResponse,
        "/api/plan-verification": VerificationResponse,
        "/api/report": ReportResponse,
        "/api/finance": FinanceResponse,
        "/api/savings": SavingsResponse,
        "/api/diagnostics": DiagnosticsResponse,
    }
    for path, model in endpoints.items():
        response = client.get(path)
        assert response.status_code == 200
        model.model_validate(response.json())


def test_models_accept_representative_payloads_and_preserve_additive_fields():
    payloads = [
        (PlanResponse, {"slots": [{"mode": "auto", "target_soc_pct": None}], "new": 1}),
        (VerificationResponse, {"status": "awaiting_measurement", "observed": None}),
        (ReportResponse, {"period": "day", "days": [], "empty": True}),
        (FinanceResponse, {"period": "day", "days": [], "total_eur": None}),
        (SavingsResponse, {"today_eur": None, "week_eur": 0.0}),
        (DiagnosticsResponse, {"status": "degraded", "checks": []}),
    ]
    for model, payload in payloads:
        parsed = model.model_validate(payload)
        if model is PlanResponse:
            assert parsed.model_dump(exclude_none=False)["new"] == 1


def test_models_allow_empty_and_unavailable_payloads():
    for model in (
        PlanResponse,
        VerificationResponse,
        ReportResponse,
        FinanceResponse,
        SavingsResponse,
        DiagnosticsResponse,
    ):
        assert model.model_validate({}).model_dump(exclude_unset=True) == {}


def test_nested_report_contract_covers_empty_stale_and_unavailable_values():
    payload = {
        "period": "day",
        "partial": True,
        "flows": {"has_data": False, "grid_import_kwh": 0.0},
        "scores": [{"key": "co2", "value": None, "explanation": "No energy recorded yet."}],
        "series": [{"start": "2026-07-29T00:00:00+00:00", "samples": 0}],
        "gas": None,
        "tariff_policy": {"basis": "provider total plus configured import fee"},
        "tariff_warnings": [{"code": "missing_import_fee", "severity": "info"}],
        "economic_snapshot": {"import_price_eur_per_kwh": 0.0},
    }
    parsed = ReportResponse.model_validate(payload)
    assert parsed.flows is not None and parsed.flows.has_data is False
    assert parsed.scores and parsed.scores[0].value is None


def test_verification_failure_states_keep_nullable_nested_payloads():
    for status in ("no_plan", "awaiting_measurement", "unexpected_charge"):
        parsed = VerificationResponse.model_validate(
            {"status": status, "planned": None, "actual": None}
        )
        assert parsed.status == status
