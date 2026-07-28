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
    start: Any | None = None
    end: Any | None = None
    mode: Any | None = None
    target_soc_pct: Any | None = None
    reason: Any | None = None


class PlanResponse(APIResponseModel):
    created_at: Any | None = None
    current_intent: Any | None = None
    current_reason: Any | None = None
    slots: list[PlanSlot] | None = None


class VerificationResponse(APIResponseModel):
    status: Any | None = None
    planned: Any | None = None
    actual: Any | None = None


class ReportResponse(APIResponseModel):
    period: Any | None = None
    window_start: Any | None = None
    window_end: Any | None = None
    label: Any | None = None
    partial: Any | None = None
    flows: dict[str, Any] | None = None
    scores: list[dict[str, Any]] | None = None
    series: list[dict[str, Any]] | None = None
    gas: Any | None = None
    tariff_policy: dict[str, Any] | None = None
    tariff_warnings: list[dict[str, Any]] | None = None


class FinanceResponse(APIResponseModel):
    period: Any | None = None
    label: Any | None = None
    window_start: Any | None = None
    window_end: Any | None = None
    partial: Any | None = None
    days: list[dict[str, Any]] | None = None
    totals: dict[str, Any] | None = None


class SavingsResponse(APIResponseModel):
    today_eur: Any | None = None
    tariff_warnings: list[dict[str, Any]] | None = None


class DiagnosticsResponse(APIResponseModel):
    overall: Any | None = None
    checks: list[dict[str, Any]] | None = None
    cache: Any | None = None
    readiness: dict[str, Any] | None = None
    storage: Any | None = None
    recorder: Any | None = None
    perf: dict[str, Any] | None = None
