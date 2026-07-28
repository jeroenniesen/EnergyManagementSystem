"""Contract checks for the typed response boundaries."""

from ems.web.models import (
    DiagnosticsResponse,
    FinanceResponse,
    PlanResponse,
    ReportResponse,
    SavingsResponse,
    VerificationResponse,
)


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
        assert model.model_validate({}).model_dump() == {}
