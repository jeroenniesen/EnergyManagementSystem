"""Invalid core values fail validation without discarding future additive fields."""

import pytest
from pydantic import ValidationError

from ems.web.models import (
    DiagnosticsResponse,
    FinanceResponse,
    PlanResponse,
    ReportResponse,
    SavingsResponse,
    VerificationResponse,
)


@pytest.mark.parametrize(
    ("model", "payload"),
    [
        (PlanResponse, {"target_soc": "not-a-number"}),
        (PlanResponse, {"strategy": []}),
        (PlanResponse, {"validation": "not-a-verdict"}),
        (PlanResponse, {"validation": {"status": "safe-ish"}}),
        (PlanResponse, {"slots": [{"intent": "export_everything"}]}),
        (PlanResponse, {"slots": [{"deadline": []}]}),
        (PlanResponse, {"tariff_policy": {"import_fee_eur_per_kwh": []}}),
        (VerificationResponse, {"status": "verified_probably"}),
        (VerificationResponse, {"actual": {"soc_pct": "full"}}),
        (VerificationResponse, {"planned": {"intent": "unexpected"}}),
        (ReportResponse, {"window_start": {"not": "a timestamp"}}),
        (ReportResponse, {"gas": {"eur": []}}),
        (ReportResponse, {"flows": {"grid_to_battery": "unknown"}}),
        (FinanceResponse, {"totals": {"grid_cost_eur": []}}),
        (FinanceResponse, {"days": [{"saved_eur": "free"}]}),
        (FinanceResponse, {"days": [{"has_data": "yes"}]}),
        (SavingsResponse, {"today_eur": {"not": "money"}}),
        (SavingsResponse, {"today_eur": True}),
        (SavingsResponse, {"today_eur": float("nan")}),
        (SavingsResponse, {"economic_snapshot": {"round_trip_efficiency": []}}),
        (DiagnosticsResponse, {"checks": [{"status": "mostly fine"}]}),
        (DiagnosticsResponse, {"readiness": {"control_ready": "maybe"}}),
        (DiagnosticsResponse, {"recorder": {"consecutive_failures": "several"}}),
        (DiagnosticsResponse, {"perf": {"rss_mb": {"current_mb": []}}}),
    ],
)
def test_malformed_core_fields_are_rejected(model, payload):
    with pytest.raises(ValidationError):
        model.model_validate(payload)


def test_additive_fields_and_unavailable_money_remain_unchanged():
    payload = {
        "today_eur": None,
        "new_metric": {"currency": "EUR"},
        "economic_snapshot": {"import_price_eur_per_kwh": -0.1, "new_policy": True},
    }
    assert SavingsResponse.model_validate(payload).model_dump(exclude_unset=True) == payload


def test_no_plan_response_preserves_absent_and_explicit_null_fields():
    payload = {"created_at": None, "current_intent": None, "current_reason": None, "slots": []}
    assert PlanResponse.model_validate(payload).model_dump(exclude_unset=True) == payload


def test_real_tariff_warning_round_trips_with_existing_severity():
    from ems.tariff_validation import validate_tariff_policy
    from ems.tariffs import TariffPolicy

    payload = {
        "today_eur": None,
        "tariff_warnings": [
            w.to_dict() for w in validate_tariff_policy(TariffPolicy(export_fee_eur_per_kwh=0.01))
        ],
    }
    assert SavingsResponse.model_validate(payload).model_dump(exclude_unset=True) == payload


def test_populated_api_responses_validate_real_nested_payloads(tmp_path):
    import asyncio
    from datetime import UTC, datetime, timedelta
    from zoneinfo import ZoneInfo

    from fastapi.testclient import TestClient

    from ems.domain import RawSample
    from ems.load_model import reconstruct
    from ems.sources.forecast import MockSolarForecastSource
    from ems.sources.mock import MockSource
    from ems.sources.prices import MockPriceSource
    from ems.storage.history import HistoryStore
    from ems.web.api import create_app

    day = (datetime.now(UTC) - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    store = HistoryStore(str(tmp_path / "responses.sqlite"))

    async def seed():
        await store.init()
        prices = []
        for i in range(16):
            ts = (day + timedelta(minutes=15 * i)).isoformat()
            raw = RawSample(
                grid_power_w=500, solar_power_w=0, battery_power_w=0, ev_power_w=0, soc_pct=50
            )
            await store.record(ts, raw, reconstruct(raw))
            prices.append((ts, 0.25))
        await store.upsert_price_slots(prices)

    asyncio.run(seed())
    tz = ZoneInfo("UTC")
    app = create_app(
        MockSource(),
        dry_run=True,
        dev_mode="mock",
        store=store,
        tz=tz,
        price_source=MockPriceSource(tz),
        solar_forecast=MockSolarForecastSource(tz),
        history_retention_days=0,
        history_backup_keep=0,
    )
    with TestClient(app) as client:
        pairs = [
            ("/api/plan", PlanResponse),
            ("/api/plan-verification", VerificationResponse),
            (f"/api/report?date={day.date()}", ReportResponse),
            (f"/api/finance?date={day.date()}", FinanceResponse),
            ("/api/savings", SavingsResponse),
            ("/api/diagnostics", DiagnosticsResponse),
        ]
        payloads = {}
        for path, model in pairs:
            response = client.get(path)
            assert response.status_code == 200
            payload = response.json()
            assert (
                model.model_validate(payload).model_dump(mode="json", exclude_unset=True) == payload
            )
            payloads[path.split("?")[0]] = payload
    assert payloads["/api/plan"]["slots"]
    assert payloads["/api/plan-verification"]["actual"]["soc_pct"] is not None
    assert payloads["/api/report"]["flows"]["has_data"]
    assert payloads["/api/finance"]["totals"]["grid_cost_eur"] is not None
    assert payloads["/api/savings"]["economic_snapshot"]["round_trip_efficiency"] > 0
    assert payloads["/api/diagnostics"]["storage"]["raw_rows"] == 16
