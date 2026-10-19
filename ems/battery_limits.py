"""Shared battery SoC floors (#245).

Indevolt OpenData rejects / silently floors charge SoC writes below 5%
(`ems/sources/indevolt_driver.py` `_MIN_SOC`). The plan validator treats a
target below `CapabilityReport.min_target_soc` as **unsafe**
(`target_below_capability_min`) — product choice: do **not** silently
clamp-to-floor in the validator; keep planner/settings above the device
floor so validator and writer agree.

`DEFAULT_MIN_TARGET_SOC_PCT` is the Indevolt floor used for settings schema
and migrations when a live capability probe is not yet available. Runtime
paths prefer `capability.min_target_soc` when probed (> 0).
"""
from __future__ import annotations

from ems.domain import CapabilityReport

# Indevolt Gen-2 OpenData SoC write floor (%). Keep in sync with indevolt_driver._MIN_SOC.
DEFAULT_MIN_TARGET_SOC_PCT = 5.0


def device_min_target_soc(capability: CapabilityReport | None) -> float:
    """Probed device floor, else the Indevolt default (never below that default)."""
    if capability is not None and capability.min_target_soc > 0:
        return float(capability.min_target_soc)
    return DEFAULT_MIN_TARGET_SOC_PCT


def soc_control_floor(
    min_reserve_soc: float,
    *,
    capability: CapabilityReport | None = None,
    min_target_soc: float | None = None,
) -> float:
    """Lowest SoC the EMS may commit as a charge target / reserve floor.

    ``max(settings reserve, device min_target_soc)`` so honest-partial and
    recovery cannot emit a target the validator would mark unsafe.
    """
    device = (
        float(min_target_soc)
        if min_target_soc is not None and min_target_soc > 0
        else device_min_target_soc(capability)
    )
    return max(0.0, float(min_reserve_soc), device)


def effective_floor_soc(
    min_reserve_soc: float,
    *,
    capability: CapabilityReport | None = None,
    min_target_soc: float | None = None,
) -> float:
    """Plan / projection / validator SoC floor (#310 / audit F-16a).

    Alias for :func:`soc_control_floor` — ``max(user reserve, device min_target_soc)``.
    """
    return soc_control_floor(
        min_reserve_soc,
        capability=capability,
        min_target_soc=min_target_soc,
    )


def effective_floor_from_settings(
    settings: dict,
    *,
    capability: CapabilityReport | None = None,
) -> float:
    """Runtime floor from the settings store + optional probed capability."""
    return effective_floor_soc(
        float(settings.get("battery.min_reserve_soc", 10.0)),
        capability=capability,
    )
