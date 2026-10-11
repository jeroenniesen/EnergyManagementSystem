"""In-memory ConsumerPort for unit tests (#223). No cloud / LAN I/O."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from ems.consumers.models import (
    ConsumerCapabilities,
    ConsumerDevice,
    ConsumerKind,
    ConsumerStatus,
)
from ems.consumers.selection import apply_opt_in


class MockConsumerSource:
    """Deterministic appliance catalog. Never touches the battery."""

    def __init__(
        self,
        devices: list[ConsumerDevice] | None = None,
        *,
        statuses: dict[str, ConsumerStatus] | None = None,
        settings: dict[str, Any] | None = None,
    ) -> None:
        self._devices = list(devices) if devices is not None else _default_catalog()
        self._statuses = dict(statuses or {})
        self._settings = settings or {}

    @property
    def integration(self) -> str:
        return "mock"

    def set_settings(self, settings: dict[str, Any]) -> None:
        """Replace the settings snapshot used for opt-in (tests / wiring)."""
        self._settings = settings

    def list_devices(self) -> list[ConsumerDevice]:
        return apply_opt_in(self._devices, self._settings)

    def get_status(self, device_id: str) -> ConsumerStatus | None:
        if device_id in self._statuses:
            return self._statuses[device_id]
        known = {d.id for d in self._devices}
        if device_id not in known:
            return None
        device = next(d for d in self._devices if d.id == device_id)
        return ConsumerStatus(
            device_id=device_id,
            state="idle",
            power_w=0.0 if device.capabilities.reports_power else None,
            energy_kwh=None,
            can_start=device.capabilities.supports_delayed_start,
            observed_at=datetime.now(UTC),
        )


def _default_catalog() -> list[ConsumerDevice]:
    caps_power = ConsumerCapabilities(
        reports_power=True,
        reports_energy=True,
        reports_status=True,
        supports_delayed_start=True,
    )
    return [
        ConsumerDevice(
            id="mock-washer-1",
            name="Mock washer",
            kind=ConsumerKind.WASHER,
            integration="mock",
            capabilities=caps_power,
        ),
        ConsumerDevice(
            id="mock-dryer-1",
            name="Mock dryer",
            kind=ConsumerKind.DRYER,
            integration="mock",
            capabilities=caps_power,
        ),
    ]
