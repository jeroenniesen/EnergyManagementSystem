"""Consumers (appliances) insight + HA wizard API (#224 / #227 / #263 / epic #222).

GET /api/consumers — opted-in devices + status.
GET /api/consumers/start-advice — cheapest contiguous start windows (dry-run default).
POST /api/consumers/ha/test-connection — wizard probe (token in body, never logged).
POST /api/consumers/ha/disconnect — clear HA token / disable adapter.

Never starts appliances and never writes to the battery.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ems.consumers.ha_probe import probe_ha_connection
from ems.consumers.models import ConsumerState, ConsumerStatus
from ems.consumers.ports import ConsumerPort
from ems.consumers.selection import apply_opt_in, default_device_ids_json, filter_opted_in
from ems.consumers.start_advice import assumed_kwh_for_kind, propose_start_window
from ems.settings import effective_settings, normalize_ha_url
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

_HORIZON = timedelta(hours=24)


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


def gather_start_advice(
    sources: Sequence[ConsumerPort],
    settings: dict[str, Any],
    price_slots: Sequence[Any],
    *,
    now: datetime,
) -> dict[str, Any]:
    """Cheapest start windows for eligible opted-in appliances (#227). Advice only."""
    dry_run = bool(settings.get("consumers.dry_run", True))
    enabled = bool(settings.get("consumers.start_advice_enabled", True))
    try:
        duration_slots = int(settings.get("consumers.default_duration_slots", 4))
    except (TypeError, ValueError):
        duration_slots = 4
    duration_slots = max(1, min(32, duration_slots))

    # Live delayed-start is not wired yet — always report unarmed.
    live_armed = False

    if not enabled:
        return {
            "title": "Start window advice",
            "enabled": False,
            "dry_run": dry_run,
            "live_armed": live_armed,
            "summary": "Start-window advice is turned off in settings.",
            "advice": [],
            "footnote": (
                "Advice only — nothing changes automatically. "
                "EMS does not start or stop appliances from this page."
            ),
        }

    catalog = gather_consumers(sources, settings)
    horizon_end = now + _HORIZON
    advice_out: list[dict[str, Any]] = []

    for device in catalog["devices"]:
        caps = device.get("capabilities") or {}
        status = device.get("status") or {}
        eligible = bool(
            caps.get("supports_delayed_start")
            and status.get("can_start")
            and status.get("state") == "idle"
        )
        entry: dict[str, Any] = {
            "device_id": device["id"],
            "device_name": device["name"],
            "kind": device["kind"],
            "eligible": eligible,
            "window": None,
            "skip_reason": None,
        }
        if not eligible:
            if not caps.get("supports_delayed_start"):
                entry["skip_reason"] = "This appliance doesn’t support delayed start yet."
            elif status.get("state") == "running":
                entry["skip_reason"] = "Already running — no start window needed."
            elif status.get("state") == "offline":
                entry["skip_reason"] = "Offline — can’t propose a start window."
            else:
                entry["skip_reason"] = "Not ready to start right now."
            advice_out.append(entry)
            continue

        window = propose_start_window(
            list(price_slots),
            duration_slots=duration_slots,
            now=now,
            horizon_end=horizon_end,
            assumed_kwh=assumed_kwh_for_kind(device["kind"]),
        )
        if window is None:
            entry["skip_reason"] = "Not enough day-ahead prices for a full cycle window."
        else:
            entry["window"] = window
        advice_out.append(entry)

    with_window = sum(1 for a in advice_out if a.get("window"))
    if not advice_out:
        summary = "No selected appliances to advise on."
    elif with_window == 0:
        summary = "No start window available right now."
    else:
        summary = (
            f"{with_window} start window{'s' if with_window != 1 else ''} "
            f"from day-ahead prices."
        )

    return {
        "title": "Start window advice",
        "enabled": True,
        "dry_run": dry_run,
        "live_armed": live_armed,
        "duration_slots": duration_slots,
        "summary": summary,
        "advice": advice_out,
        "footnote": (
            "Advice only — nothing changes automatically. "
            "EMS does not start or stop appliances from this page."
        ),
    }


def build_router(
    ctx: AppContext,
    *,
    sources: Sequence[ConsumerPort] | None = None,
    settings_store: Any | None = None,
    http_get: Any | None = None,
) -> APIRouter:
    router = APIRouter()
    wired: tuple[ConsumerPort, ...] = tuple(sources or ())

    @router.get("/api/consumers")
    def get_consumers() -> dict:
        """Selected appliances + status — insight only, no start/stop."""
        return gather_consumers(wired, ctx.settings_cache)

    @router.get("/api/consumers/start-advice")
    def get_start_advice() -> dict:
        """Cheapest delayed-start windows — dry-run advice only (#227)."""
        now = datetime.now(UTC)
        prices: list[Any] = []
        if ctx.price_source is not None:
            try:
                prices = list(ctx.price_source.slots())
            except Exception:
                _log.debug("consumers start-advice: price slots failed", exc_info=True)
                prices = []
        return gather_start_advice(
            wired, ctx.settings_cache, prices, now=now,
        )

    @router.post("/api/consumers/ha/test-connection")
    async def ha_test_connection(request: Request) -> dict:
        """Wizard probe: URL + token in body → candidate laundry entities (#263)."""
        try:
            body = await request.json()
        except Exception:
            body = {}
        if not isinstance(body, dict):
            body = {}
        url = str(body.get("url") or "").strip()
        # Strict true only — string "false" must not enable the stored-token path.
        use_stored = body.get("use_stored_token") is True
        token = str(body.get("token") or "").strip()
        if use_stored and not token:
            stored_token = str(ctx.settings_cache.get("consumers.ha.token") or "").strip()
            stored_url = str(ctx.settings_cache.get("consumers.ha.url") or "").strip()
            if not stored_token:
                return {
                    "ok": False,
                    "error": "invalid_token",
                    "message": "No Home Assistant token is saved yet. Paste a token to test.",
                }
            # Stored token may only go to the stored URL — never to a caller-chosen host.
            if url:
                try:
                    want = normalize_ha_url(url)
                    have = normalize_ha_url(stored_url) if stored_url else ""
                except ValueError:
                    return {
                        "ok": False,
                        "error": "invalid_url",
                        "message": "That Home Assistant address isn’t valid.",
                    }
                if not have or want != have:
                    return {
                        "ok": False,
                        "error": "url_mismatch",
                        "message": (
                            "To reuse the saved token, the address must match the one "
                            "already saved. Paste the token again to test a new address."
                        ),
                    }
                url = want
            else:
                url = stored_url
            token = stored_token
        # Keep the control-loop event loop free — probe does sync HTTP.
        result = await asyncio.to_thread(
            lambda: probe_ha_connection(url, token, http_get=http_get),
        )
        # Never echo secrets — strip any accidental key.
        result.pop("token", None)
        return result

    @router.post("/api/consumers/ha/disconnect")
    async def ha_disconnect() -> JSONResponse:
        """Clear HA token, disable adapter, empty device opt-in (#263)."""
        if settings_store is None:
            return JSONResponse(
                {"ok": False, "error": "settings_unavailable",
                 "message": "Settings store is not available."},
                status_code=503,
            )
        await settings_store.delete("consumers.ha.token")
        await settings_store.set_many({
            "consumers.ha.enabled": False,
            "consumers.ha.device_ids": default_device_ids_json(),
        })
        # Full keyset overlay (never clear()+update — concurrent readers).
        ctx.settings_cache.update(effective_settings(await settings_store.all()))
        return JSONResponse({
            "ok": True,
            "restart_required": True,
            "summary": "Home Assistant disconnected. Apply & restart to stop using it.",
        })

    return router
