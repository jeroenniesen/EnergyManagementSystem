"""Typed (but forwards-compatible) response contracts for the application API.

The services intentionally continue to return dictionaries.  These models provide a
stable validation/documentation boundary while allowing additive fields from newer
planner and diagnostic versions.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class APIResponseModel(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)


class PlanSlot(APIResponseModel):
    start: str | None = None
    end: str | None = None
    intent: str | None = None
    target_soc: float | None = None
    target_kwh: float | None = None
    power_w: float | None = None
    floor_soc: float | None = None


class PlanResponse(APIResponseModel):
    created_at: str | None = None
    current_intent: str | None = None
    current_reason: str | None = None
    slots: list[PlanSlot] | None = None


class VerificationResponse(APIResponseModel):
    status: str | None = None
    planned: dict[str, Any] | None = None
    actual: dict[str, Any] | None = None


class FlowSummary(APIResponseModel):
    date: str | None = None
    has_data: bool | None = None
    partial: bool | None = None
    solar_to_home: float | None = None
    solar_to_car: float | None = None
    solar_to_battery: float | None = None
    solar_to_grid: float | None = None
    grid_import_kwh: float | None = None
    grid_export_kwh: float | None = None
    battery_charge_kwh: float | None = None
    battery_discharge_kwh: float | None = None
    home_kwh: float | None = None
    car_kwh: float | None = None
    solar_kwh: float | None = None
    self_sufficiency_pct: float | None = None
    solar_self_consumption_pct: float | None = None
    car_guard_leak_kwh: float | None = None


class ReportScore(APIResponseModel):
    key: str | None = None
    label: str | None = None
    value: float | None = None
    raw: float | None = None
    unit: str | None = None
    explanation: str | None = None


class ReportSeriesPoint(APIResponseModel):
    start: str | None = None
    grid_import_kwh: float | None = None
    grid_export_kwh: float | None = None
    house_kwh: float | None = None
    car_kwh: float | None = None
    solar_kwh: float | None = None
    samples: int | None = None


class GasSummary(APIResponseModel):
    daily_m3: float | None = None
    annualized_eur: float | None = None


class EconomicSnapshotModel(APIResponseModel):
    import_price_eur_per_kwh: float | None = None
    export_price_eur_per_kwh: float | None = None
    round_trip_efficiency: float | None = None
    degradation_eur_per_kwh: float | None = None
    risk_margin_eur_per_kwh: float | None = None
    export_model: str | None = None
    energy_tax_eur_per_kwh: float | None = None
    fixed_feed_in_eur_per_kwh: float | None = None
    raw_price_eur_per_kwh: float | None = None
    import_fee_eur_per_kwh: float | None = None
    export_fee_eur_per_kwh: float | None = None


class TariffPolicyModel(APIResponseModel):
    tibber_total_includes_all: bool | None = None
    import_fee_eur_per_kwh: float | None = None
    export_fee_eur_per_kwh: float | None = None
    basis: str | None = None


class TariffWarning(APIResponseModel):
    code: str | None = None
    severity: str | None = None
    message: str | None = None


class ReportResponse(APIResponseModel):
    period: str | None = None
    window_start: Any | None = None
    window_end: Any | None = None
    label: str | None = None
    partial: bool | None = None
    flows: FlowSummary | None = None
    scores: list[ReportScore] | None = None
    series: list[ReportSeriesPoint] | None = None
    gas: GasSummary | None = None
    tariff_policy: TariffPolicyModel | None = None
    tariff_warnings: list[TariffWarning] | None = None
    economic_snapshot: EconomicSnapshotModel | None = None


class FinanceResponse(APIResponseModel):
    period: str | None = None
    label: str | None = None
    window_start: str | None = None
    window_end: str | None = None
    partial: bool | None = None
    days: list[dict[str, Any]] | None = None
    totals: dict[str, Any] | None = None


class SavingsResponse(APIResponseModel):
    today_eur: Any | None = None
    tariff_warnings: list[dict[str, Any]] | None = None
    economic_snapshot: dict[str, Any] | None = None


class DiagnosticsResponse(APIResponseModel):
    overall: str | None = None
    checks: list[dict[str, Any]] | None = None
    cache: dict[str, Any] | None = None
    readiness: dict[str, Any] | None = None
    storage: dict[str, Any] | None = None
    recorder: dict[str, Any] | None = None
    perf: dict[str, Any] | None = None
