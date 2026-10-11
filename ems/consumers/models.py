"""Vendor-neutral consumer (appliance) device model (#223 / epic #222).

Pure types — no I/O. SmartThings / HA adapters (#225 / #226) map into these.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class ConsumerKind(StrEnum):
    """Broad appliance class. Unknown stays honest rather than inventing a type."""

    WASHER = "washer"
    DRYER = "dryer"
    DISHWASHER = "dishwasher"
    OTHER = "other"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ConsumerCapabilities:
    """What this device can report / do. Start = delayed start only (epic #222)."""

    reports_power: bool = False
    reports_energy: bool = False
    reports_status: bool = True
    supports_delayed_start: bool = False


@dataclass(frozen=True)
class ConsumerDevice:
    """One discovered / configured consumer.

    ``opted_in`` is resolved from settings (per-integration device-id list), not from the
    vendor — adapters list what exists; selection decides what EMS may use.
    """

    id: str
    name: str
    kind: ConsumerKind
    integration: str  # e.g. "smartthings", "ha", "mock"
    capabilities: ConsumerCapabilities
    opted_in: bool = False


@dataclass(frozen=True)
class ConsumerStatus:
    """Point-in-time status for a consumer. Missing telemetry stays None (honest)."""

    device_id: str
    state: str  # vendor-neutral token: idle | running | delayed | offline | unknown
    power_w: float | None = None
    energy_kwh: float | None = None
    can_start: bool = False
    observed_at: datetime | None = None
    detail: str | None = None
