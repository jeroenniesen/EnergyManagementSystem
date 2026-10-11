"""Read-only automation bounds for Settings (#246 / B-35 slice 1).

GET /api/automation-bounds — curated homeowner overview of existing limits.
Never writes settings or calls ModeController.decide / battery apply.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter

from ems.automation_bounds import build_automation_bounds
from ems.web.context import AppContext

_log = logging.getLogger(__name__)


def build_router(
    ctx: AppContext,
    *,
    battery: Any | None = None,
    controller: Any | None = None,
    config_dry_run: bool = False,
) -> APIRouter:
    router = APIRouter()

    @router.get("/api/automation-bounds")
    def automation_bounds() -> dict:
        """What EMS may do — read-only snapshot of existing settings + write floors."""
        capability = None
        batt = battery
        if batt is None and controller is not None:
            batt = getattr(controller, "driver", None)
        if batt is not None:
            try:
                capability = batt.probe()
            except Exception:
                _log.debug("automation-bounds: battery probe failed (non-fatal)", exc_info=True)
                capability = None

        lifecycle_state = None
        if controller is not None:
            try:
                lifecycle_state = str(controller.lifecycle.state)
            except Exception:
                lifecycle_state = None

        return build_automation_bounds(
            ctx.settings_cache,
            dry_run=bool(ctx.dry_run),
            config_dry_run=bool(config_dry_run),
            dev_mode=str(ctx.dev_mode),
            capability=capability,
            lifecycle_state=lifecycle_state,
        )

    return router
