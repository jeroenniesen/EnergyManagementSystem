"""Home Assistant connection probe for the Settings wizard (#263).

Read-only GET ``/api/states`` with an injectable transport. Never logs the token,
never writes settings, never calls HA services or the battery.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from ems.consumers.homeassistant import classify_ha_entity
from ems.settings import normalize_ha_url

_log = logging.getLogger(__name__)

JsonGet = Callable[[str, dict[str, str]], Any]

_ERROR_COPY: dict[str, str] = {
    "invalid_url": (
        "That doesn’t look like a Home Assistant address. "
        "Try something like http://homeassistant.local:8123."
    ),
    "unreachable": (
        "EMS can’t reach that address. Check the URL and that Home Assistant is online."
    ),
    "timeout": "Home Assistant took too long to answer. Try again in a moment.",
    "invalid_token": (
        "Home Assistant rejected the token. Create a new long-lived access token "
        "and paste it again."
    ),
    "tls_error": (
        "The secure connection failed (certificate problem). "
        "Use http:// on your LAN, or fix the HTTPS certificate."
    ),
    "not_home_assistant": (
        "That address answered, but it doesn’t look like Home Assistant. "
        "Check the URL (include the port, usually 8123)."
    ),
    "no_devices": (
        "Connected, but no washing machine, dryer, or dishwasher entities were found. "
        "You can still add an entity id by hand."
    ),
}


def probe_ha_connection(
    url: str,
    token: str,
    *,
    http_get: JsonGet | None = None,
    timeout: float = 8.0,
) -> dict[str, Any]:
    """Probe HA with url+token. Returns ``{ok, ...}`` — never includes the token."""
    token = (token or "").strip()
    if not token:
        return {
            "ok": False,
            "error": "invalid_token",
            "message": _ERROR_COPY["invalid_token"],
        }
    try:
        base = normalize_ha_url(url)
    except ValueError:
        return {
            "ok": False,
            "error": "invalid_url",
            "message": _ERROR_COPY["invalid_url"],
        }

    getter = http_get or _default_get(timeout)
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }
    try:
        payload = getter(f"{base}/api/states", headers)
    except Exception as exc:
        code = _classify_transport_error(exc)
        _log.debug("HA probe failed (%s)", code, exc_info=True)
        return {"ok": False, "error": code, "message": _ERROR_COPY[code], "url": base}

    if not isinstance(payload, list):
        return {
            "ok": False,
            "error": "not_home_assistant",
            "message": _ERROR_COPY["not_home_assistant"],
            "url": base,
        }

    devices: list[dict[str, str]] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        kind = classify_ha_entity(item)
        if kind is None:
            continue
        eid = str(item.get("entity_id") or "")
        if not eid:
            continue
        attrs = item.get("attributes") if isinstance(item.get("attributes"), dict) else {}
        name = str(attrs.get("friendly_name") or eid)
        devices.append({"id": eid, "name": name, "kind": str(kind)})

    if not devices:
        return {
            "ok": True,
            "url": base,
            "devices": [],
            "summary": "Connected — no laundry appliances found automatically.",
            "warning": "no_devices",
            "message": _ERROR_COPY["no_devices"],
        }
    n = len(devices)
    return {
        "ok": True,
        "url": base,
        "devices": devices,
        "summary": f"Found {n} appliance{'s' if n != 1 else ''}.",
    }


def _default_get(timeout: float) -> JsonGet:
    def _get(url: str, headers: dict[str, str]) -> Any:
        import httpx

        r = httpx.get(url, headers=headers, timeout=timeout)
        r.raise_for_status()
        return r.json()

    return _get


def _classify_transport_error(exc: BaseException) -> str:
    name = type(exc).__name__
    module = type(exc).__module__ or ""
    msg = str(exc).lower()

    # httpx status errors expose .response.status_code
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    if status == 401 or status == 403:
        return "invalid_token"
    if status is not None and status >= 400:
        return "not_home_assistant"

    if "timeout" in name.lower() or "timeout" in msg:
        return "timeout"
    if (
        "ssl" in name.lower()
        or "certificate" in msg
        or "tls" in msg
        or "Cert" in name
    ):
        return "tls_error"
    if "connect" in name.lower() or "connect" in msg or module.startswith("httpx"):
        return "unreachable"
    return "unreachable"
