"""ConsumerPort — vendor-neutral seam for appliance integrations (#223 / epic #222).

Adapters (SmartThings, HA, mock) implement this Protocol. Never writes to the battery —
Indevolt/control remains the single battery writer.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from ems.consumers.models import ConsumerDevice, ConsumerStatus


@runtime_checkable
class ConsumerPort(Protocol):
    """List / status / optional power-energy / delayed-start capability.

    ``start`` is optional on the port for later slices (#227); this slice only exposes
    whether delayed start is available via status/capabilities. Concrete start methods
    land with the planner dry-run slice — not here.
    """

    @property
    def integration(self) -> str:
        """Stable integration id (``smartthings``, ``ha``, ``mock``, …)."""
        ...

    def list_devices(self) -> list[ConsumerDevice]:
        """Full discovered catalog with ``opted_in`` set from settings (via ``apply_opt_in``).

        Adapters must not pre-filter to opted-in only — callers use ``filter_opted_in``
        when they need participants.
        """
        ...

    def get_status(self, device_id: str) -> ConsumerStatus | None:
        """Current status, or None when the device is unknown / unreachable."""
        ...
