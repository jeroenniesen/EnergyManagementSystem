from __future__ import annotations

from fastapi import APIRouter

from ems.application.services import DiagnosticsService
from ems.web.context import AppContext
from ems.web.models import DiagnosticsResponse


def build_router(ctx: AppContext, service: DiagnosticsService) -> APIRouter:
    router = APIRouter()

    @router.get(
        "/api/diagnostics", response_model=DiagnosticsResponse, response_model_exclude_unset=True
    )
    async def diagnostics_endpoint() -> DiagnosticsResponse:
        return await service.get_snapshot()

    return router
