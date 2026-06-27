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


@dataclass(frozen=True)
class RawSample:
    """Sign-normalised instantaneous readings (SPEC §4.1)."""

    grid_power_w: float  # + import / - export
    solar_power_w: float  # >= 0 production
    battery_power_w: float  # + discharge / - charge
    ev_power_w: float  # >= 0 charging
    soc_pct: float  # 0..100
