from __future__ import annotations

from fastapi import APIRouter

from ems.application.services import PlanService
from ems.web.context import AppContext
from ems.web.models import PlanResponse


def build_router(ctx: AppContext, service: PlanService) -> APIRouter:
    router = APIRouter()

    @router.get("/api/plan", response_model=PlanResponse, response_model_exclude_unset=True)
    def plan_endpoint() -> PlanResponse:
        return service.get_plan(settings=ctx.settings_cache)

    return router
