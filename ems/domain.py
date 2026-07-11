"""Core domain types (SPEC §7.1, §13.2). Sign conventions per SPEC §4.1."""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class BatteryIntent(StrEnum):
    ALLOW_SELF_CONSUMPTION = "allow_self_consumption"
    GRID_CHARGE_TO_TARGET = "grid_charge_to_target"
    HOLD_RESERVE = "hold_reserve"
    DISCHARGE_FOR_LOAD = "discharge_for_load"


class PlannerMode(StrEnum):
    RULE_BASED = "rule_based"
    ML = "ml"
    ADVISORY = "advisory"


class PhysicalMode(StrEnum):
    """What the controller actually commands the battery into (SPEC §7.2)."""

    AUTO = "auto"  # vendor self-consumption (P1-zeroing)
    CHARGE = "charge"  # force charge to a target SoC
    DISCHARGE = "discharge"  # force discharge (deliberate export)
    IDLE = "idle"  # hold SoC


@dataclass(frozen=True)
class CapabilityReport:
    """Result of the M1a capability probe (SPEC §6.5)."""

    services: tuple[str, ...]  # e.g. ("charge", "discharge")
    energy_mode_options: tuple[str, ...]
    has_standby: bool
    has_grid_charge_switch: bool
    p1_paired: bool  # is the Indevolt reading the P1 meter?
    max_charge_w: float
    max_discharge_w: float


@dataclass(frozen=True)
class RawSample:
    """Sign-normalised instantaneous readings (SPEC §4.1)."""

    grid_power_w: float  # + import / - export
    solar_power_w: float  # >= 0 production
    battery_power_w: float  # + discharge / - charge
    ev_power_w: float  # >= 0 charging
    soc_pct: float  # 0..100
    # Cumulative gas meter reading (m³, monotonic); None when no gas meter is paired.
    total_gas_m3: float | None = None
