"""The battery driver — the ONLY writer to the battery (SPEC §6.5). `BatteryDriver` is the port;
`MockBatteryDriver` is a CapabilityReport-driven in-memory Indevolt for dev/tests (no HA/hardware).

`intent_to_mode` maps a planner BatteryIntent to the physical mode the controller commands.
Per SPEC §8.3, "serve load during a peak" is really vendor self-consumption; the forced DISCHARGE
mode is for deliberate export. For this first mock cut we map DISCHARGE_FOR_LOAD -> DISCHARGE;
the serve-load-via-AUTO refinement (gated on the M1 P1-zeroing probe) comes with live control.
"""
from __future__ import annotations

from typing import Protocol

from ems.domain import BatteryIntent, CapabilityReport, PhysicalMode

_INTENT_TO_MODE: dict[BatteryIntent, PhysicalMode] = {
    BatteryIntent.ALLOW_SELF_CONSUMPTION: PhysicalMode.AUTO,
    BatteryIntent.GRID_CHARGE_TO_TARGET: PhysicalMode.CHARGE,
    BatteryIntent.HOLD_RESERVE: PhysicalMode.IDLE,
}


def intent_to_mode(intent: BatteryIntent, *, allow_export_discharge: bool = False) -> PhysicalMode:
    """Map a planner intent to the physical mode to command.

    DISCHARGE_FOR_LOAD serves the house via vendor self-consumption (AUTO) by default; it only
    becomes a forced DISCHARGE (deliberate grid export) when `allow_export_discharge` is set.
    Defaulting to AUTO keeps it fail-safe so a control loop can't export by accident
    (SPEC §7.1/§8.3). KeyError on any unmapped intent is intentional (loud failure).
    """
    if intent is BatteryIntent.DISCHARGE_FOR_LOAD:
        return PhysicalMode.DISCHARGE if allow_export_discharge else PhysicalMode.AUTO
    return _INTENT_TO_MODE[intent]


class BatteryDriver(Protocol):
    def probe(self) -> CapabilityReport: ...
    def current_mode(self) -> PhysicalMode: ...
    def apply(self, mode: PhysicalMode) -> bool:
        """Set the battery to `mode`. Returns True only if the transition was **confirmed**
        (post-write poll matched). False = command sent but unconfirmed; the caller must run
        the SPEC §6.5 failure path (retry → AUTO → alert). Never raise for an unconfirmed write."""
        ...


class MockBatteryDriver:
    """Fake Indevolt: a SolidFlex-2000-shaped cluster. `apply` is idempotent and self-confirms."""

    def __init__(self) -> None:
        self._mode = PhysicalMode.AUTO
        self._capabilities = CapabilityReport(
            services=("charge", "discharge"),
            energy_mode_options=("self_consumed_prioritized", "real_time_control"),
            has_standby=True,
            has_grid_charge_switch=True,
            p1_paired=True,
            max_charge_w=4000.0,
            max_discharge_w=4000.0,
        )

    def probe(self) -> CapabilityReport:
        return self._capabilities

    def current_mode(self) -> PhysicalMode:
        return self._mode

    def apply(self, mode: PhysicalMode) -> bool:
        # Idempotent: re-applying the current mode is a no-op but still "confirmed".
        self._mode = mode
        return True


class FailingMockBatteryDriver(MockBatteryDriver):
    """Test double whose apply() returns False (unconfirmed) for the first `fail_times` calls
    WITHOUT changing the mode — for exercising the failure/recovery path (SPEC §6.5)."""

    def __init__(self, fail_times: int = 1) -> None:
        super().__init__()
        self._remaining = fail_times

    def apply(self, mode: PhysicalMode) -> bool:
        if self._remaining > 0:
            self._remaining -= 1
            return False  # command not confirmed; mode unchanged
        return super().apply(mode)
