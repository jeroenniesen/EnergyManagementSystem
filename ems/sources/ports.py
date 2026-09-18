"""Typed ports for external energy-management sources.

These protocols are deliberately structural: production adapters and test doubles do not need to
inherit from a common base class.  The original modules re-export the protocols for compatibility.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

from ems.domain import CapabilityReport, PhysicalMode, RawSample

if TYPE_CHECKING:
    from .forecast import ForecastSlot
    from .prices import PriceSlot


@runtime_checkable
class Source(Protocol):
    def read(self) -> RawSample: ...


@runtime_checkable
class BatteryDriver(Protocol):
    def probe(self) -> CapabilityReport: ...
    def current_mode(self) -> PhysicalMode: ...
    def apply(
        self,
        mode: PhysicalMode,
        *,
        target_soc: float | None = None,
        power_w: float | None = None,
    ) -> bool: ...


@runtime_checkable
class PriceSource(Protocol):
    def slots(self) -> list[PriceSlot]: ...


@runtime_checkable
class SolarForecastSource(Protocol):
    def slots(self) -> list[ForecastSlot]: ...
