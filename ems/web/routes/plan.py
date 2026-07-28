from __future__ import annotations

from fastapi import APIRouter

from ems.application.services import PlanService
from ems.web.context import AppContext


def build_router(ctx: AppContext, service: PlanService) -> APIRouter:
    router = APIRouter()

    @router.get("/api/plan")
    def plan_endpoint() -> dict:
        return service.get_plan(settings=ctx.settings_cache)

    return router
