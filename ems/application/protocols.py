"""Typed protocols for application and control service seams."""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Protocol, TypedDict

from ems.control.car_mode import CarModeAction
from ems.control.mode_controller import ActionDecision
from ems.domain import BatteryIntent, PhysicalMode, RawSample
from ems.planner.schedule import Plan
from ems.planner.validator import PlanValidation
from ems.sources.prices import PriceSlot


class DataQuality(Protocol):
    def __call__(self, now: datetime) -> str: ...

class PlanValidator(Protocol):
    def __call__(self, plan: Plan, now: datetime) -> PlanValidation: ...

class PlanProvider(Protocol):
    def __call__(
        self, now: datetime | None = None,
    ) -> tuple[datetime, list[PriceSlot], Plan] | None: ...

class CarModeActionProvider(Protocol):
    def __call__(
        self, now: datetime, *, current_setpoint_w: float | None = None
    ) -> CarModeAction | None: ...

class BatteryDriver(Protocol):
    def apply(self, mode: PhysicalMode) -> bool: ...

class BatteryController(Protocol):
    @property
    def driver(self) -> BatteryDriver: ...

    def decide(
        self, intent: BatteryIntent, now: datetime, *,
        target_soc: float | None = None, power_w: float | None = None,
        observed_mode: PhysicalMode | None = None, manual: bool = False,
        priority: bool = False, car_session: bool = False, force: bool = False,
        count_toward_cap: bool = True, commitment: bool = False,
    ) -> ActionDecision: ...

type TickCallback = Callable[[datetime], None]
type ClockCallback = Callable[[], datetime]


class SampleProvider(Protocol):
    def __call__(self, now: datetime) -> RawSample | None: ...


class TariffPolicyProvider(Protocol):
    def __call__(self, settings: dict[str, object], /) -> dict[str, object]: ...


class TariffWarningsProvider(Protocol):
    def __call__(self, settings: dict[str, object], /) -> list[dict[str, str]]: ...


class ReportProvider(Protocol):
    async def __call__(
        self, period: str, start: datetime, end: datetime, label: str,
        partial: bool, now_local: datetime,
    ) -> dict[str, object]: ...


class FinanceDay(TypedDict, total=False):
    day: str
    has_data: bool
    price_coverage: float
    sample_coverage: float
    grid_cost_eur: float | None
    battery_cost_eur: float | None
    baseline_cost_eur: float | None
    saved_eur: float | None
    grid_import_kwh: float
    grid_export_kwh: float
    battery_charge_kwh: float
    battery_discharge_kwh: float
    calc_v: int


class FinanceProvider(Protocol):
    async def __call__(
        self, start: datetime, end: datetime, now_local: datetime,
    ) -> list[FinanceDay]: ...


class SavingsProvider(Protocol):
    def __call__(self, now: datetime) -> dict[str, object]: ...


class DiagnosticsProvider(Protocol):
    async def __call__(self, now: datetime) -> dict[str, object]: ...
