"""Discoverable composition catalog (SPEC §13).

Source-side Protocols live in `ems/sources/ports.py` and are re-exported here. Planner (and later
LoadForecaster / Explainer) ports live at this top level so adapters, tests, and the control loop
share one import surface. ML adapters plug in behind the same `Planner` Protocol and still pass
the unchanged §8.11 validator — they are not shipped on the Pi path (B-47 seam only).
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

from ems.sources.ports import BatteryDriver, PriceSource, SolarForecastSource, Source

if TYPE_CHECKING:
    from ems.planner.base import PlannerRequest
    from ems.planner.schedule import Plan

__all__ = [
    "Source",
    "BatteryDriver",
    "PriceSource",
    "SolarForecastSource",
    "Planner",
]


@runtime_checkable
class Planner(Protocol):
    """Produces a validated-shape `Plan` (SPEC §8 intro).

    Same contract for rule_based | ml | advisory.
    """

    def plan(self, request: PlannerRequest) -> Plan: ...
