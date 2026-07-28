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
    pass


class VerificationResponse(APIResponseModel):
    pass


class ReportResponse(APIResponseModel):
    pass


class FinanceResponse(APIResponseModel):
    pass


class SavingsResponse(APIResponseModel):
    pass


class DiagnosticsResponse(APIResponseModel):
    pass
