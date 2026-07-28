from __future__ import annotations

from fastapi import APIRouter

from ems.application.services import VerificationService
from ems.web.context import AppContext


def build_router(ctx: AppContext, service: VerificationService) -> APIRouter:
    router = APIRouter()

    @router.get("/api/plan-verification")
    def plan_verification() -> dict:
        return service.verify()

    return router
