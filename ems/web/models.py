"""Typed (but forwards-compatible) response contracts for the application API.

The services intentionally continue to return dictionaries.  These models provide a
stable validation/documentation boundary while allowing additive fields from newer
planner and diagnostic versions.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, with_config
from typing_extensions import TypedDict


class APIResponseModel(BaseModel):
    model_config = ConfigDict(
        extra="allow", populate_by_name=True, strict=True, allow_inf_nan=False
    )


Intent = Literal[
    "allow_self_consumption", "grid_charge_to_target", "hold_reserve", "discharge_for_load"
]
Period = Literal["day", "week", "month", "year"]
CheckStatus = Literal["ok", "warn", "fail"]


class ValidationFinding(APIResponseModel):
    severity: Literal["warn", "unsafe"] | None = None
    code: str | None = None
    message: str | None = None


class PlanValidation(APIResponseModel):
    status: Literal["valid", "warn", "unsafe"] | None = None
    ok: bool | None = None
    findings: list[ValidationFinding] | None = None


class PlanSlot(APIResponseModel):
    start: str | None = None
    end: str | None = None
    intent: Intent | None = None
    reason: str | None = None
    target_soc: float | None = None
    target_kwh: float | None = None
    power_w: float | None = None
    floor_soc: float | None = None
    deadline: str | None = None


class PlannedOutcome(APIResponseModel):
    intent: Intent | None = None
    target_soc: float | None = None
    deadline: str | None = None
    reason: str | None = None


class ActualOutcome(APIResponseModel):
    soc_pct: float | None = None
    battery_power_w: float | None = None
    grid_power_w: float | None = None
    observed_at: str | None = None


class VerificationResponse(APIResponseModel):
    status: (
        Literal[
            "no_plan",
            "awaiting_measurement",
            "observed",
            "unexpected_discharge",
            "unexpected_charge",
        ]
        | None
    ) = None
    planned: PlannedOutcome | None = None
    actual: ActualOutcome | None = None
    checked_at: str | None = None


class FlowSummary(APIResponseModel):
    date: str | None = None
    has_data: bool | None = None
    partial: bool | None = None
    solar_to_home: float | None = None
    solar_to_car: float | None = None
    solar_to_battery: float | None = None
    solar_to_grid: float | None = None
    grid_to_home: float | None = None
    grid_to_car: float | None = None
    grid_to_battery: float | None = None
    battery_to_home: float | None = None
    battery_to_car: float | None = None
    battery_to_grid: float | None = None
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
    m3: float | None = None
    kwh_eq: float | None = None
    eur: float | None = None
    co2_kg: float | None = None
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
    severity: Literal["info", "warning"] | None = None
    message: str | None = None


class PlanResponse(APIResponseModel):
    created_at: str | None = None
    strategy: Literal["summer", "winter"] | None = None
    target_soc: float | None = None
    deadline: str | None = None
    current_intent: Intent | None = None
    current_reason: str | None = None
    slots: list[PlanSlot] | None = None
    validation: PlanValidation | None = None
    tariff_policy: TariffPolicyModel | None = None
    tariff_warnings: list[TariffWarning] | None = None


class ReportResponse(APIResponseModel):
    period: Period | None = None
    window_start: str | None = None
    window_end: str | None = None
    label: str | None = None
    partial: bool | None = None
    flows: FlowSummary | None = None
    scores: list[ReportScore] | None = None
    series: list[ReportSeriesPoint] | None = None
    gas: GasSummary | None = None
    tariff_policy: TariffPolicyModel | None = None
    tariff_warnings: list[TariffWarning] | None = None
    economic_snapshot: EconomicSnapshotModel | None = None


class FinanceDay(APIResponseModel):
    day: str | None = None
    has_data: bool | None = None
    price_coverage: float | None = None
    sample_coverage: float | None = None
    grid_cost_eur: float | None = None
    battery_cost_eur: float | None = None
    baseline_cost_eur: float | None = None
    saved_eur: float | None = None
    grid_import_kwh: float | None = None
    grid_export_kwh: float | None = None
    battery_charge_kwh: float | None = None
    battery_discharge_kwh: float | None = None
    calc_v: int | None = None


class FinanceTotals(APIResponseModel):
    grid_cost_eur: float | None = None
    battery_cost_eur: float | None = None
    saved_eur: float | None = None
    grid_import_kwh: float | None = None
    grid_export_kwh: float | None = None
    days_with_prices: int | None = None
    days_with_data: int | None = None


class FinanceResponse(APIResponseModel):
    period: Period | None = None
    label: str | None = None
    window_start: str | None = None
    window_end: str | None = None
    partial: bool | None = None
    days: list[FinanceDay] | None = None
    totals: FinanceTotals | None = None


class SavingsResponse(APIResponseModel):
    today_eur: float | None = None
    tariff_warnings: list[TariffWarning] | None = None
    economic_snapshot: EconomicSnapshotModel | None = None


@with_config(ConfigDict(extra="allow", strict=True))
class DiagnosticCheck(TypedDict, total=False):
    key: str
    label: str
    status: CheckStatus
    detail: str


class Readiness(APIResponseModel):
    alive: bool | None = None
    dashboard_ready: bool | None = None
    sensing_ready: bool | None = None
    planning_ready: bool | None = None
    control_ready: bool | None = None
    summary: str | None = None


class RecorderHealth(APIResponseModel):
    last_success_at: str | None = None
    consecutive_failures: int | None = None
    last_error: str | None = None
    clamped_samples: int | None = None
    invalid_reconstructions: int | None = None
    last_reconstruction_flags: list[str] | None = None


class BackupHealth(APIResponseModel):
    last_backup_ts: str | None = None
    last_backup_ok: bool | None = None
    last_backup_size: int | None = None
    backups_kept: int | None = None


class ForecastJobHealth(APIResponseModel):
    last_success_date: str | None = None
    last_attempt_iso: str | None = None
    ok: bool | None = None


class HistoryStoreHealth(APIResponseModel):
    consecutive_persist_failures: int | None = None
    last_reheal_iso: str | None = None


class StorageHealth(APIResponseModel):
    db_bytes: int | None = None
    wal_bytes: int | None = None
    raw_rows: int | None = None
    derived_rows: int | None = None
    backup: BackupHealth | None = None
    canonical_forecast: ForecastJobHealth | None = None
    history_store: HistoryStoreHealth | None = None


class TimingSummary(APIResponseModel):
    p50_ms: float | None = None
    p95_ms: float | None = None
    p99_ms: float | None = None
    max_ms: float | None = None
    n: int | None = None
    over_budget_count: int | None = None
    last_overrun_at: float | None = None


class MemoryUsage(APIResponseModel):
    current_mb: float | None = None
    peak_mb: float | None = None
    over_ceiling_count: int | None = None


class BudgetOverrun(APIResponseModel):
    ts: float | None = None
    name: str | None = None
    duration_ms: float | None = None


class PerformanceMetrics(APIResponseModel):
    budgets: dict[str, float] | None = None
    tiers: dict[str, TimingSummary] | None = None
    control_cycle: TimingSummary | None = None
    rss_mb: MemoryUsage | None = None
    last_overruns: list[BudgetOverrun] | None = None


class DiagnosticsResponse(APIResponseModel):
    overall: CheckStatus | None = None
    checks: list[DiagnosticCheck] | None = None
    cache: dict[str, int] | None = None
    readiness: Readiness | None = None
    storage: StorageHealth | None = None
    recorder: RecorderHealth | None = None
    perf: PerformanceMetrics | None = None
