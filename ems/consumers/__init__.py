"""Vendor-neutral consumer (appliance) integrations (#223 / epic #222).

Phase 1 seam: device model + ``ConsumerPort`` + opt-in selection.
Concrete SmartThings / HA clients and web UI arrive in later slices.
"""
from __future__ import annotations

from ems.consumers.factory import build_consumer_sources
from ems.consumers.models import (
    ConsumerCapabilities,
    ConsumerDevice,
    ConsumerKind,
    ConsumerState,
    ConsumerStatus,
)
from ems.consumers.ports import ConsumerPort
from ems.consumers.selection import (
    HA_DEVICE_IDS_KEY,
    SMARTTHINGS_DEVICE_IDS_KEY,
    apply_opt_in,
    filter_opted_in,
    opted_in_ids,
    parse_device_ids,
)

__all__ = [
    "ConsumerCapabilities",
    "ConsumerDevice",
    "ConsumerKind",
    "ConsumerPort",
    "ConsumerState",
    "ConsumerStatus",
    "HA_DEVICE_IDS_KEY",
    "SMARTTHINGS_DEVICE_IDS_KEY",
    "apply_opt_in",
    "build_consumer_sources",
    "filter_opted_in",
    "opted_in_ids",
    "parse_device_ids",
]
