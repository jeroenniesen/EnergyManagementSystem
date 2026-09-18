from __future__ import annotations

from fastapi import APIRouter

from ems.application.services import VerificationService
from ems.web.context import AppContext
from ems.web.models import VerificationResponse


def build_router(ctx: AppContext, service: VerificationService) -> APIRouter:
    router = APIRouter()

    @router.get(
        "/api/plan-verification",
        response_model=VerificationResponse,
        response_model_exclude_unset=True,
    )
    def plan_verification() -> VerificationResponse:
        return service.verify()

    return router
