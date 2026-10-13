"""Home Assistant ConsumerPort adapter (#226 / epic #222).

Read-only: discover/select washer/dryer-like entities, status + power/energy when
sensors expose them. Never starts appliances and never writes to the battery
(or any HA service). Injectable HTTP for tests — no live HA in CI.

Distinct from B-18/#107 (EMS→HA MQTT publish); this is HA→EMS for appliances only.
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
from ems.consumers.selection import apply_opt_in, opted_in_ids
from ems.ha_url import normalize_ha_url

_log = logging.getLogger(__name__)

# ``(url, headers) -> json`` — dict for one entity, or list for GET /api/states.
JsonGet = Callable[[str, dict[str, str]], Any]

def _kind_from_text(*parts: str) -> ConsumerKind | None:
    blob = " ".join(p for p in parts if p).lower().replace(" ", "_")
    if "dishwasher" in blob or "vaatwasser" in blob:
        return ConsumerKind.DISHWASHER
    if "dryer" in blob or "tumble" in blob or "droger" in blob:
        return ConsumerKind.DRYER
    if "washer" in blob or "washing" in blob or "wasmachine" in blob:
        return ConsumerKind.WASHER
    return None


def classify_ha_entity(entity: dict[str, Any]) -> ConsumerKind | None:
    """Heuristic laundry classification from entity_id / friendly_name.

    Power/energy sensors are never appliances themselves — they are sibling metrics
    for a switch/binary_sensor primary entity.
    """
    eid = str(entity.get("entity_id") or "")
    attrs = entity.get("attributes") if isinstance(entity.get("attributes"), dict) else {}
    device_class = str(attrs.get("device_class") or "").lower()
    if device_class in ("power", "energy", "current", "voltage"):
        return None
    domain = eid.split(".", 1)[0] if "." in eid else ""
    # Bare power/energy sensors often share the laundry name — keep them as siblings only.
    if domain == "sensor" and (
        eid.endswith("_power") or eid.endswith("_energy")
        or eid.endswith("_power_w") or eid.endswith("_energy_kwh")
    ):
        return None
    name = str(attrs.get("friendly_name") or "")
    return _kind_from_text(eid, name)


def _state_from_ha(raw_state: str) -> ConsumerState:
    s = (raw_state or "").strip().lower()
    if s in ("unavailable", "unknown", ""):
        return ConsumerState.OFFLINE if s == "unavailable" else ConsumerState.UNKNOWN
    if s in ("on", "running", "active", "washing", "drying"):
        return ConsumerState.RUNNING
    if s in ("off", "idle", "standby", "ready"):
        return ConsumerState.IDLE
    if s in ("delayed", "scheduled"):
        return ConsumerState.DELAYED
    # binary_sensor / switch numeric-ish leftovers
    if s == "open":
        return ConsumerState.RUNNING
    if s == "closed":
        return ConsumerState.IDLE
    return ConsumerState.UNKNOWN


def _num_attr(attrs: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        if key not in attrs:
            continue
        try:
            return float(attrs[key])
        except (TypeError, ValueError):
            continue
    return None


def _power_from_entity(entity: dict[str, Any]) -> float | None:
    attrs = entity.get("attributes") if isinstance(entity.get("attributes"), dict) else {}
    # Explicit power attribute, or the state itself when device_class/power unit says W.
    power = _num_attr(attrs, "power", "current_power_w", "power_w")
    if power is not None:
        return power
    dc = str(attrs.get("device_class") or "").lower()
    unit = str(attrs.get("unit_of_measurement") or "").lower()
    if dc == "power" or unit in ("w", "watt", "watts", "kw"):
        try:
            value = float(entity.get("state"))
        except (TypeError, ValueError):
            return None
        if unit == "kw":
            return value * 1000.0
        return value
    return None


def _energy_from_entity(entity: dict[str, Any]) -> float | None:
    attrs = entity.get("attributes") if isinstance(entity.get("attributes"), dict) else {}
    energy = _num_attr(attrs, "energy", "energy_kwh")
    if energy is not None:
        return energy
    dc = str(attrs.get("device_class") or "").lower()
    unit = str(attrs.get("unit_of_measurement") or "").lower()
    if dc == "energy" or unit in ("kwh", "wh", "mwh"):
        try:
            value = float(entity.get("state"))
        except (TypeError, ValueError):
            return None
        if unit == "wh":
            return value / 1000.0
        if unit == "mwh":
            return value * 1000.0
        return value
    return None


class HomeAssistantConsumerSource:
    """ConsumerPort over HA REST (long-lived token). Sync, injectable HTTP."""

    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        http_get: JsonGet | None = None,
        settings: dict[str, Any] | None = None,
        timeout: float = 8.0,
    ) -> None:
        self._base = normalize_ha_url(base_url)
        self._token = token.strip()
        self._settings = settings or {}
        self._timeout = timeout
        self._get = http_get or self._default_get
        # None = not fetched / last fetch failed (retry); list = last success.
        self._catalog: list[ConsumerDevice] | None = None
        self._by_id: dict[str, dict[str, Any]] = {}

    @property
    def integration(self) -> str:
        return "ha"

    def set_settings(self, settings: dict[str, Any]) -> None:
        prev = opted_in_ids(self._settings, "ha")
        self._settings = settings
        # Opt-in set may add entities the heuristic missed — rediscover next list.
        if opted_in_ids(self._settings, "ha") != prev:
            self._catalog = None
            return
        if self._catalog is not None:
            self._catalog = apply_opt_in(self._catalog, self._settings)

    def list_devices(self) -> list[ConsumerDevice]:
        self._ensure_catalog()
        if self._catalog is None:
            raise RuntimeError("Home Assistant device list unavailable")
        return list(self._catalog)

    def get_status(self, device_id: str) -> ConsumerStatus | None:
        self._ensure_catalog()
        if self._catalog is None:
            return ConsumerStatus(
                device_id=device_id,
                state=ConsumerState.OFFLINE,
                detail="Home Assistant did not return states just now.",
                observed_at=datetime.now(UTC),
            )
        known = {d.id for d in self._catalog}
        if device_id not in known:
            return None
        device = next(d for d in self._catalog if d.id == device_id)
        try:
            entity = self._get(
                f"{self._base}/api/states/{device_id}",
                self._headers(),
            )
        except Exception:
            _log.debug("HA consumer status failed for %s", device_id, exc_info=True)
            return ConsumerStatus(
                device_id=device_id,
                state=ConsumerState.OFFLINE,
                detail="Home Assistant did not return a status just now.",
                observed_at=datetime.now(UTC),
            )
        if not isinstance(entity, dict):
            return ConsumerStatus(
                device_id=device_id,
                state=ConsumerState.UNKNOWN,
                observed_at=datetime.now(UTC),
            )
        state = _state_from_ha(str(entity.get("state") or ""))
        power = _power_from_entity(entity) if device.capabilities.reports_power else None
        energy = _energy_from_entity(entity) if device.capabilities.reports_energy else None
        # Fresh sibling reads each status call (do not reuse the list-time snapshot).
        if power is None and device.capabilities.reports_power:
            power = self._fetch_sibling_metric(device_id, "power")
        if energy is None and device.capabilities.reports_energy:
            energy = self._fetch_sibling_metric(device_id, "energy")
        return ConsumerStatus(
            device_id=device_id,
            state=state,
            power_w=power,
            energy_kwh=energy,
            can_start=False,
            observed_at=datetime.now(UTC),
        )

    def _sibling_ids(self, device_id: str, kind: str) -> tuple[str, ...]:
        stem = device_id.split(".", 1)[-1]
        if kind == "power":
            return (f"sensor.{stem}_power", f"sensor.{stem}_power_w")
        return (f"sensor.{stem}_energy", f"sensor.{stem}_energy_kwh")

    def _fetch_sibling_metric(self, device_id: str, kind: str) -> float | None:
        """Best-effort live read of sensor.<stem>_power / _energy next to switch.<stem>."""
        for cid in self._sibling_ids(device_id, kind):
            try:
                ent = self._get(f"{self._base}/api/states/{cid}", self._headers())
            except Exception:
                continue
            if not isinstance(ent, dict):
                continue
            val = _power_from_entity(ent) if kind == "power" else _energy_from_entity(ent)
            if val is not None:
                return val
        return None

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/json",
        }

    def _ensure_catalog(self) -> None:
        if self._catalog is not None:
            return
        try:
            payload = self._get(f"{self._base}/api/states", self._headers())
        except Exception:
            _log.warning("Home Assistant /api/states failed", exc_info=True)
            return
        if not isinstance(payload, list):
            _log.warning("Home Assistant /api/states: unexpected payload shape")
            return
        by_id: dict[str, dict[str, Any]] = {}
        for item in payload:
            if isinstance(item, dict) and item.get("entity_id"):
                by_id[str(item["entity_id"])] = item
        self._by_id = by_id

        built: list[ConsumerDevice] = []
        seen: set[str] = set()
        for eid, entity in by_id.items():
            kind = classify_ha_entity(entity)
            if kind is None:
                continue
            built.append(self._to_device(eid, entity, kind))
            seen.add(eid)

        # Opted-in ids the heuristic missed still appear (user selected them).
        for eid in opted_in_ids(self._settings, "ha"):
            if eid in seen:
                continue
            entity = by_id.get(eid)
            if entity is None:
                # Placeholder so opt-in is visible; status will be offline/unknown.
                built.append(ConsumerDevice(
                    id=eid,
                    name=eid,
                    kind=ConsumerKind.OTHER,
                    integration="ha",
                    capabilities=ConsumerCapabilities(reports_status=True),
                ))
            else:
                kind = classify_ha_entity(entity) or ConsumerKind.OTHER
                built.append(self._to_device(eid, entity, kind))
            seen.add(eid)

        self._catalog = apply_opt_in(built, self._settings)

    def _to_device(
        self, eid: str, entity: dict[str, Any], kind: ConsumerKind,
    ) -> ConsumerDevice:
        attrs = entity.get("attributes") if isinstance(entity.get("attributes"), dict) else {}
        name = str(attrs.get("friendly_name") or eid)
        stem = eid.split(".", 1)[-1]
        reports_power = (
            _power_from_entity(entity) is not None
            or f"sensor.{stem}_power" in self._by_id
            or f"sensor.{stem}_power_w" in self._by_id
        )
        reports_energy = (
            _energy_from_entity(entity) is not None
            or f"sensor.{stem}_energy" in self._by_id
            or f"sensor.{stem}_energy_kwh" in self._by_id
        )
        return ConsumerDevice(
            id=eid,
            name=name,
            kind=kind,
            integration="ha",
            capabilities=ConsumerCapabilities(
                reports_power=reports_power,
                reports_energy=reports_energy,
                reports_status=True,
                supports_delayed_start=False,
            ),
        )

    def _default_get(self, url: str, headers: dict[str, str]) -> Any:
        import httpx
        r = httpx.get(url, headers=headers, timeout=self._timeout)
        r.raise_for_status()
        return r.json()
