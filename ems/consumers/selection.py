"""Opt-in device-id selection for consumer integrations (#223).

Settings store JSON string lists per integration (default ``[]`` = nothing participates).
Tolerant on read — corrupt values fall back to empty rather than breaking the dashboard.
"""
from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

from ems.consumers.models import ConsumerDevice

# Settings keys (also registered in ems/settings.py). Default empty = opt-in, nothing selected.
SMARTTHINGS_DEVICE_IDS_KEY = "consumers.smartthings.device_ids"
HA_DEVICE_IDS_KEY = "consumers.ha.device_ids"
MOCK_DEVICE_IDS_KEY = "consumers.mock.device_ids"

INTEGRATION_DEVICE_ID_KEYS: dict[str, str] = {
    "smartthings": SMARTTHINGS_DEVICE_IDS_KEY,
    "ha": HA_DEVICE_IDS_KEY,
    "mock": MOCK_DEVICE_IDS_KEY,
}


def default_device_ids_json() -> str:
    return "[]"


def parse_device_ids(raw: Any) -> tuple[str, ...]:
    """Parse a settings value into a stable tuple of non-empty device ids.

    Never raises. Accepts JSON list, comma-separated string, or an already-parsed list/tuple.
    Duplicates are dropped (first wins); order is preserved.
    """
    if raw is None:
        return ()
    items: list[Any]
    if isinstance(raw, (list, tuple)):
        items = list(raw)
    elif isinstance(raw, str):
        text = raw.strip()
        if not text:
            return ()
        if text[:1] in "[{":
            try:
                parsed = json.loads(text)
            except (TypeError, ValueError, json.JSONDecodeError):
                return ()
            if not isinstance(parsed, list):
                return ()
            items = parsed
        else:
            items = [p.strip() for p in text.split(",")]
    else:
        return ()

    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item is None:
            continue
        device_id = str(item).strip()
        if not device_id or device_id in seen:
            continue
        seen.add(device_id)
        out.append(device_id)
    return tuple(out)


def opted_in_ids(settings: dict[str, Any], integration: str) -> frozenset[str]:
    """Device ids opted in for ``integration`` (empty = none)."""
    key = INTEGRATION_DEVICE_ID_KEYS.get(integration)
    if key is None:
        return frozenset()
    return frozenset(parse_device_ids(settings.get(key)))


def apply_opt_in(
    devices: Iterable[ConsumerDevice],
    settings: dict[str, Any],
) -> list[ConsumerDevice]:
    """Return devices with ``opted_in`` set from settings (per device.integration)."""
    out: list[ConsumerDevice] = []
    for device in devices:
        allowed = opted_in_ids(settings, device.integration)
        flag = device.id in allowed
        if device.opted_in == flag:
            out.append(device)
        else:
            out.append(ConsumerDevice(
                id=device.id,
                name=device.name,
                kind=device.kind,
                integration=device.integration,
                capabilities=device.capabilities,
                opted_in=flag,
            ))
    return out


def filter_opted_in(devices: Iterable[ConsumerDevice]) -> list[ConsumerDevice]:
    """Keep only devices already marked opted_in."""
    return [d for d in devices if d.opted_in]
