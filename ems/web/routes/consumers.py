"""Read-only consumers (appliances) insight API (#224 / epic #222).

GET /api/consumers — opted-in devices + status. Never starts appliances and never
writes to the battery.
"""
from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

from fastapi import APIRouter

from ems.consumers.models import ConsumerState, ConsumerStatus
from ems.consumers.ports import ConsumerPort
from ems.consumers.selection import apply_opt_in, filter_opted_in
from ems.web.context import AppContext

_log = logging.getLogger(__name__)

# Homeowner-facing status labels — never leak vendor tokens / stack traces.
_STATE_LABEL: dict[str, str] = {
    "idle": "Idle — ready when you are",
    "running": "Running",
    "delayed": "Scheduled to start later",
    "offline": "Offline — EMS can’t reach this appliance",
    "unknown": "Status unknown",
}


def _state_label(state: str) -> str:
    return _STATE_LABEL.get(state, "Status unknown")


def _status_dict(status: ConsumerStatus | None) -> dict[str, Any] | None:
    if status is None:
        return None
    state = str(status.state)
    return {
        "state": state,
        "state_label": _state_label(state),
        "power_w": status.power_w,
        "energy_kwh": status.energy_kwh,
        "can_start": bool(status.can_start),
        "observed_at": status.observed_at.isoformat() if status.observed_at else None,
        # Never pass through vendor/adapter detail strings — only our synthetic offline copy.
        "detail": status.detail if (
            status.state == ConsumerState.OFFLINE
            and status.detail == "No status from the integration right now."
        ) else None,
    }


def _device_dict(device: Any, status: ConsumerStatus | None) -> dict[str, Any]:
    caps = device.capabilities
    return {
        "id": device.id,
        "name": device.name,
        "kind": str(device.kind),
        "integration": device.integration,
        "opted_in": bool(device.opted_in),
        "capabilities": {
            "reports_power": caps.reports_power,
            "reports_energy": caps.reports_energy,
            "reports_status": caps.reports_status,
            "supports_delayed_start": caps.supports_delayed_start,
        },
        "status": _status_dict(status),
    }


def gather_consumers(
    sources: Sequence[ConsumerPort],
    settings: dict[str, Any],
) -> dict[str, Any]:
    """Pure gather used by the route and tests — no I/O beyond the ports."""
    devices_out: list[dict[str, Any]] = []
    list_failures = 0
    for src in sources:
        try:
            set_settings = getattr(src, "set_settings", None)
            if callable(set_settings):
                set_settings(settings)
            catalog = src.list_devices()
        except Exception:
            list_failures += 1
            _log.warning(
                "consumers: list_devices failed for %s",
                getattr(src, "integration", "?"),
                exc_info=True,
            )
            continue
        # Re-apply settings opt-in here so adapters that self-set opted_in cannot bypass
        # the homeowner selection list (mock already calls apply_opt_in; this is the belt).
        for device in filter_opted_in(apply_opt_in(catalog, settings)):
            try:
                status = src.get_status(device.id)
            except Exception:
                _log.debug("consumers: get_status failed for %s", device.id, exc_info=True)
                status = None
            if status is None:
                status = ConsumerStatus(
                    device_id=device.id,
                    state=ConsumerState.OFFLINE,
                    detail="No status from the integration right now.",
                )
            devices_out.append(_device_dict(device, status))

    if not sources:
        summary = "No appliance integrations are connected yet."
    elif not devices_out and list_failures:
        summary = (
            "EMS can’t reach your appliance integration right now — "
            "try again in a moment."
        )
    elif not devices_out:
        summary = "No appliances selected yet."
    else:
        n = len(devices_out)
        summary = f"{n} selected appliance{'s' if n != 1 else ''}."

    return {
        "title": "Appliances",
        "summary": summary,
        "read_only": True,
        "devices": devices_out,
    }


def build_router(
    ctx: AppContext,
    *,
    sources: Sequence[ConsumerPort] | None = None,
) -> APIRouter:
    router = APIRouter()
    wired: tuple[ConsumerPort, ...] = tuple(sources or ())

    @router.get("/api/consumers")
    def get_consumers() -> dict:
        """Selected appliances + status — insight only, no start/stop."""
        return gather_consumers(wired, ctx.settings_cache)

    return router
