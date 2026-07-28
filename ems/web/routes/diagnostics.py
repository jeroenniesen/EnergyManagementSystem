from __future__ import annotations

from fastapi import APIRouter

from ems.application.services import DiagnosticsService
from ems.web.context import AppContext


def build_router(ctx: AppContext, service: DiagnosticsService) -> APIRouter:
    router = APIRouter()

    @router.get("/api/diagnostics")
    async def diagnostics_endpoint() -> dict:
        return await service.get_snapshot()

    return router
