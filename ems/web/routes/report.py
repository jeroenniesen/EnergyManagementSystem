from __future__ import annotations

from datetime import date as date_cls

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from ems.application.services import ReportService
from ems.reporting import resolve_window
from ems.web.context import AppContext
from ems.web.models import FinanceResponse, ReportResponse, SavingsResponse


def build_router(ctx: AppContext, service: ReportService) -> APIRouter:
    router = APIRouter()

    @router.get("/api/report", response_model=ReportResponse)
    async def report(
        period: str = Query(default="day", pattern="^(day|week|month|year)$"),
        date: str | None = None,
    ) -> ReportResponse:
        now_local = service.context.clock.now_local(ctx.site_tz)
        if date:
            try:
                anchor = date_cls.fromisoformat(date)
            except ValueError:
                return JSONResponse({"detail": "date must be YYYY-MM-DD"}, status_code=422)  # type: ignore[return-value]
        else:
            anchor = now_local.date()
        start, end, label, partial = resolve_window(period, anchor, ctx.site_tz, now_local)
        return await service.report(period, start, end, label, partial, now_local)

    @router.get("/api/finance", response_model=FinanceResponse)
    async def finance(
        period: str = Query(default="day", pattern="^(day|week|month|year)$"),
        date: str | None = None,
    ) -> FinanceResponse:
        now_local = service.context.clock.now_local(ctx.site_tz)
        if date:
            try:
                anchor = date_cls.fromisoformat(date)
            except ValueError:
                return JSONResponse({"detail": "date must be YYYY-MM-DD"}, status_code=422)  # type: ignore[return-value]
        else:
            anchor = now_local.date()
        start, end, label, partial = resolve_window(period, anchor, ctx.site_tz, now_local)
        return await service.finance(start, end, now_local, period, label, partial)

    @router.get("/api/savings", response_model=SavingsResponse)
    def savings_endpoint() -> SavingsResponse:
        return service.savings()

    return router
