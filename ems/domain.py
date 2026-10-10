"""Core domain types (SPEC §7.1, §13.2). Sign conventions per SPEC §4.1."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class BatteryIntent(StrEnum):
    ALLOW_SELF_CONSUMPTION = "allow_self_consumption"
    GRID_CHARGE_TO_TARGET = "grid_charge_to_target"
    HOLD_RESERVE = "hold_reserve"
    DISCHARGE_FOR_LOAD = "discharge_for_load"
    EXPORT_FOR_PROFIT = "export_for_profit"  # E-11 trading: forced DISCHARGE when armed


class PlannerMode(StrEnum):
    RULE_BASED = "rule_based"
    ML = "ml"
    ADVISORY = "advisory"


@dataclass(frozen=True)
class PlannerInputSnapshot:
    """Inputs captured with every Plan for audit/replay (SPEC §8.11 / §13.2, control-model §9).

    Compact + digests (product choice B for B-47): counts, provenance, key scalars, and content
    hashes of the price/forecast/(optional) load series so drift is detectable without persisting
    full slot arrays. Full series stay on the live sources.
    """

    taken_at: datetime
    planner_mode: str
    strategy: str
    soc_pct: float
    price_slots: int
    price_resolution_minutes: int = 15
    price_provenance: str | None = None
    price_min_eur: float | None = None
    price_max_eur: float | None = None
    prices_digest: str | None = None
    forecast_slots: int = 0
    forecast_provider: str | None = None
    forecast_issued_at: datetime | None = None
    forecast_p50_kwh: float | None = None
    forecast_digest: str | None = None
    load_digest: str | None = None
    baseline: str | None = None
    # Winter EV exogenous (#181 / SPEC §4.5): expected car import re-added for sizing, or None/0
    # when unknown (fail-soft). Advice/forecast only — does not unlock charger control (B-17).
    expected_ev_kwh: float | None = None
    capability_report_ref: str | None = None
    config_hash: str | None = None


class IntelligenceState(StrEnum):
    """Runtime capability state of the scenario/ML intelligence layer (B-79). Derived from a
    real evaluation record, never asserted — see ems.web.api._intelligence_status."""
    NOT_ACTIVE = "not_active"                # not wired / never evaluated
    SHADOW_EVALUATION = "shadow_evaluation"  # evaluated alongside the plan, never steering
    ADVISORY = "advisory"                    # surfaced as advice, still not steering
    ACTIVE = "active"                        # actually steering the plan


class PhysicalMode(StrEnum):
    """What the controller actually commands the battery into (SPEC §7.2)."""

    AUTO = "auto"  # vendor self-consumption (P1-zeroing)
    CHARGE = "charge"  # force charge to a target SoC
    DISCHARGE = "discharge"  # force discharge (deliberate export)
    IDLE = "idle"  # hold SoC


@dataclass(frozen=True)
class CapabilityReport:
    """Result of the M1a capability probe (SPEC §6.5).

    Vendor-neutral control limits (#112 slice b) sit beside the existing Indevolt-flavoured
    fields. Defaults keep older call sites compiling; production drivers set the new fields
    explicitly from device limits. Slice d moves `energy_mode_options` /
    `has_grid_charge_switch` / `p1_paired` into adapter-specific `details`.
    """

    services: tuple[str, ...]  # e.g. ("charge", "discharge")
    energy_mode_options: tuple[str, ...]
    has_standby: bool
    has_grid_charge_switch: bool
    p1_paired: bool  # is the Indevolt reading the P1 meter?
    max_charge_w: float
    max_discharge_w: float
    # Vendor-neutral capability flags / floors (#112b). Fail-soft defaults: unknown adapters
    # advertise no forced control until the probe fills these in.
    supports_discharge_control: bool = False
    supports_grid_charge: bool = False
    supports_standby: bool = False
    min_power_w: float = 0.0
    min_target_soc: float = 0.0


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
