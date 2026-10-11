"""Read-only automation bounds for Settings (#246 / B-35 slice 1).

GET /api/automation-bounds — curated homeowner overview of existing limits.
Never writes settings, never calls ModeController.decide / battery apply, and
**never probes the battery** — device power ceilings come only from the
control-loop ``CapabilityReport`` cache (Indevolt OpenData is write-sensitive).
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter

from ems.automation_bounds import build_automation_bounds
from ems.domain import CapabilityReport
from ems.web.context import AppContext

_log = logging.getLogger(__name__)


def build_router(
    ctx: AppContext,
    *,
    controller: Any | None = None,
    config_dry_run: bool = False,
    capability_getter: Callable[[], CapabilityReport | None] | None = None,
) -> APIRouter:
    router = APIRouter()

    @router.get("/api/automation-bounds")
    def automation_bounds() -> dict:
        """What EMS may do — read-only snapshot of existing settings + write floors."""
        capability: CapabilityReport | None = None
        probe_ok: bool | None = None

        if capability_getter is not None:
            try:
                capability = capability_getter()
            except Exception:
                _log.debug("automation-bounds: capability cache read failed", exc_info=True)
                capability = None
            # Cache hit only. Miss ⇒ probe_ok=None; power rows stay unknown (no RPC).
            probe_ok = True if capability is not None else None

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
            probe_ok=probe_ok,
            lifecycle_state=lifecycle_state,
        )

    return router
