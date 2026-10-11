"""Samsung SmartThings ConsumerPort adapter (#225 / epic #222).

Read-only: discover washer/dryer, status + power/energy when the API exposes them.
Never starts appliances and never writes to the battery. Injectable HTTP for tests.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from ems.consumers.models import (
    ConsumerCapabilities,
    ConsumerDevice,
    ConsumerKind,
    ConsumerState,
    ConsumerStatus,
)
from ems.consumers.selection import apply_opt_in

_log = logging.getLogger(__name__)

ST_API_BASE = "https://api.smartthings.com/v1"

# ``(url, headers) -> json dict`` — same shape as Solcast / ElectricityMaps injectors.
JsonGet = Callable[[str, dict[str, str]], dict]

# Capability ids that mark laundry appliances on the ST catalog.
_WASHER_CAPS = frozenset({"washerOperatingState", "samsungce.washerOperatingState"})
_DRYER_CAPS = frozenset({"dryerOperatingState", "samsungce.dryerOperatingState"})

_STATE_MAP: dict[str, ConsumerState] = {
    "ready": ConsumerState.IDLE,
    "stop": ConsumerState.IDLE,
    "stopped": ConsumerState.IDLE,
    # Paused mid-cycle — not "ready when you are".
    "pause": ConsumerState.UNKNOWN,
    "paused": ConsumerState.UNKNOWN,
    "run": ConsumerState.RUNNING,
    "running": ConsumerState.RUNNING,
    "wash": ConsumerState.RUNNING,
    "spin": ConsumerState.RUNNING,
    "rinse": ConsumerState.RUNNING,
    "dry": ConsumerState.RUNNING,
    "cooling": ConsumerState.RUNNING,
    "delayWash": ConsumerState.DELAYED,
    "delayedStart": ConsumerState.DELAYED,
    "weightSensing": ConsumerState.RUNNING,
}


def _capability_ids(device: dict[str, Any]) -> set[str]:
    ids: set[str] = set()
    for comp in device.get("components") or []:
        if not isinstance(comp, dict):
            continue
        for cap in comp.get("capabilities") or []:
            if isinstance(cap, dict) and cap.get("id"):
                ids.add(str(cap["id"]))
            elif isinstance(cap, str):
                ids.add(cap)
    return ids


def classify_laundry(device: dict[str, Any]) -> ConsumerKind | None:
    """Return washer/dryer when the device looks like laundry; else None.

    Prefer capability ids; fall back only to vendor type fields (not free-form
    label/name — those false-positive on e.g. \"Washer room light\").
    """
    caps = _capability_ids(device)
    if caps & _WASHER_CAPS:
        return ConsumerKind.WASHER
    if caps & _DRYER_CAPS:
        return ConsumerKind.DRYER
    blob = " ".join(
        str(device.get(k) or "")
        for k in ("deviceTypeName", "ocfDeviceType")
    ).lower()
    if "washer" in blob or "washing" in blob:
        return ConsumerKind.WASHER
    if "dryer" in blob or "tumble" in blob:
        return ConsumerKind.DRYER
    return None


def _cap_value(status_payload: dict[str, Any], capability: str, attribute: str) -> Any:
    """Read ``components.main.<capability>.<attribute>.value`` (ST status shape)."""
    comps = status_payload.get("components") or {}
    main = comps.get("main") if isinstance(comps, dict) else None
    if not isinstance(main, dict):
        return None
    cap = main.get(capability)
    if not isinstance(cap, dict):
        return None
    attr = cap.get(attribute)
    if not isinstance(attr, dict):
        return None
    return attr.get("value")


def _operating_state(status_payload: dict[str, Any], kind: ConsumerKind) -> ConsumerState:
    keys = (
        ("washerOperatingState", "machineState"),
        ("samsungce.washerOperatingState", "operatingState"),
    ) if kind is ConsumerKind.WASHER else (
        ("dryerOperatingState", "machineState"),
        ("samsungce.dryerOperatingState", "operatingState"),
    )
    for cap, attr in keys:
        raw = _cap_value(status_payload, cap, attr)
        if raw is None:
            continue
        mapped = _STATE_MAP.get(str(raw))
        if mapped is not None:
            return mapped
        # Unknown vendor token — honest unknown, not invented idle.
        return ConsumerState.UNKNOWN
    # switch as weak fallback
    sw = _cap_value(status_payload, "switch", "switch")
    if sw == "on":
        return ConsumerState.RUNNING
    if sw == "off":
        return ConsumerState.IDLE
    return ConsumerState.UNKNOWN


def _parse_power_consumption(raw: Any) -> dict[str, Any] | None:
    """Samsung often returns powerConsumption as a dict or a JSON string."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip().startswith("{"):
        import json
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def _power_w(status_payload: dict[str, Any]) -> float | None:
    raw = _cap_value(status_payload, "powerMeter", "power")
    if raw is not None:
        try:
            return float(raw)
        except (TypeError, ValueError):
            pass
    pcr = _parse_power_consumption(
        _cap_value(status_payload, "powerConsumptionReport", "powerConsumption")
    )
    if pcr is not None and pcr.get("power") is not None:
        try:
            return float(pcr["power"])
        except (TypeError, ValueError):
            pass
    return None


def _energy_kwh(status_payload: dict[str, Any]) -> float | None:
    """Energy in kWh. ``energyMeter`` is typically kWh; PCR ``energy`` is Wh."""
    em = _cap_value(status_payload, "energyMeter", "energy")
    if em is not None:
        try:
            value = float(em)
        except (TypeError, ValueError):
            value = None
        else:
            unit = _cap_value(status_payload, "energyMeter", "unit")
            if unit is None:
                # ST energyMeter attribute often nests unit beside value.
                comps = (status_payload.get("components") or {}).get("main") or {}
                attr = (comps.get("energyMeter") or {}).get("energy") or {}
                if isinstance(attr, dict):
                    unit = attr.get("unit")
            if str(unit or "").lower() in ("wh", "watt-hour", "watthour"):
                return value / 1000.0
            return value
    pcr = _parse_power_consumption(
        _cap_value(status_payload, "powerConsumptionReport", "powerConsumption")
    )
    if pcr is not None and pcr.get("energy") is not None:
        try:
            # Samsung powerConsumptionReport.energy is watt-hours.
            return float(pcr["energy"]) / 1000.0
        except (TypeError, ValueError):
            pass
    return None


class SmartThingsConsumerSource:
    """ConsumerPort over the SmartThings REST API (PAT). Sync, injectable HTTP."""

    def __init__(
        self,
        token: str,
        *,
        http_get: JsonGet | None = None,
        settings: dict[str, Any] | None = None,
        base_url: str = ST_API_BASE,
        timeout: float = 10.0,
    ) -> None:
        self._token = token.strip()
        self._settings = settings or {}
        self._base = base_url.rstrip("/")
        self._timeout = timeout
        self._get = http_get or self._default_get
        # None = not fetched yet; list = last successful discovery (may be empty).
        # A failed fetch leaves this None so the next call retries.
        self._catalog: list[tuple[ConsumerDevice, dict[str, Any]]] | None = None

    @property
    def integration(self) -> str:
        return "smartthings"

    def set_settings(self, settings: dict[str, Any]) -> None:
        self._settings = settings
        # Opt-in may change without rediscovery.
        if self._catalog is not None:
            devices = [d for d, _ in self._catalog]
            marked = apply_opt_in(devices, self._settings)
            self._catalog = list(zip(marked, [raw for _, raw in self._catalog], strict=True))

    def list_devices(self) -> list[ConsumerDevice]:
        self._ensure_catalog()
        if self._catalog is None:
            # Surface failure to gather_consumers (honest “can’t reach” summary).
            raise RuntimeError("SmartThings device list unavailable")
        return [d for d, _ in self._catalog]

    def get_status(self, device_id: str) -> ConsumerStatus | None:
        self._ensure_catalog()
        if self._catalog is None:
            return ConsumerStatus(
                device_id=device_id,
                state=ConsumerState.OFFLINE,
                detail="SmartThings did not return the device list just now.",
                observed_at=datetime.now(UTC),
            )
        match = next(((d, r) for d, r in self._catalog if d.id == device_id), None)
        if match is None:
            return None
        device, _raw = match
        try:
            payload = self._get(
                f"{self._base}/devices/{device_id}/status",
                self._headers(),
            )
        except Exception:
            _log.debug("SmartThings status failed for %s", device_id, exc_info=True)
            return ConsumerStatus(
                device_id=device_id,
                state=ConsumerState.OFFLINE,
                detail="SmartThings did not return a status just now.",
                observed_at=datetime.now(UTC),
            )
        if not isinstance(payload, dict):
            return ConsumerStatus(
                device_id=device_id,
                state=ConsumerState.UNKNOWN,
                observed_at=datetime.now(UTC),
            )
        state = _operating_state(payload, device.kind)
        power = _power_w(payload) if device.capabilities.reports_power else None
        energy = _energy_kwh(payload) if device.capabilities.reports_energy else None
        return ConsumerStatus(
            device_id=device_id,
            state=state,
            power_w=power,
            energy_kwh=energy,
            can_start=device.capabilities.supports_delayed_start,
            observed_at=datetime.now(UTC),
        )

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/json",
        }

    def _ensure_catalog(self) -> None:
        if self._catalog is not None:
            return
        try:
            payload = self._get(f"{self._base}/devices", self._headers())
        except Exception:
            _log.warning("SmartThings device list failed", exc_info=True)
            # Leave _catalog None so the next list/status call retries.
            return
        items = payload.get("items") if isinstance(payload, dict) else None
        if not isinstance(items, list):
            _log.warning("SmartThings device list: unexpected payload shape")
            return
        built: list[tuple[ConsumerDevice, dict[str, Any]]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            kind = classify_laundry(item)
            if kind is None:
                continue
            device_id = str(item.get("deviceId") or item.get("id") or "").strip()
            if not device_id:
                continue
            name = str(item.get("label") or item.get("name") or device_id)
            caps_ids = _capability_ids(item)
            caps = ConsumerCapabilities(
                reports_power="powerMeter" in caps_ids or "powerConsumptionReport" in caps_ids,
                reports_energy="energyMeter" in caps_ids or "powerConsumptionReport" in caps_ids,
                reports_status=True,
                # ST delayed-start API not wired yet; #227 advice is dry-run only.
                supports_delayed_start=False,
            )
            built.append((
                ConsumerDevice(
                    id=device_id,
                    name=name,
                    kind=kind,
                    integration="smartthings",
                    capabilities=caps,
                ),
                item,
            ))
        devices = apply_opt_in([d for d, _ in built], self._settings)
        self._catalog = list(zip(devices, [raw for _, raw in built], strict=True))

    def _default_get(self, url: str, headers: dict[str, str]) -> dict:
        import httpx
        r = httpx.get(url, headers=headers, timeout=self._timeout)
        r.raise_for_status()
        data = r.json()
        if not isinstance(data, dict):
            raise ValueError("SmartThings response was not a JSON object")
        return data
