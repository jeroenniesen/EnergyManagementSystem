"""Typed protocols for application and control service seams."""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Protocol

from ems.control.car_mode import CarModeAction
from ems.control.mode_controller import ActionDecision
from ems.domain import PhysicalMode
from ems.planner.schedule import Plan
from ems.planner.validator import PlanValidation


class DataQuality(Protocol):
    def __call__(self, now: datetime) -> str: ...

class PlanValidator(Protocol):
    def __call__(self, plan: Plan, now: datetime) -> PlanValidation: ...

class PlanProvider(Protocol):
    def __call__(self) -> Plan | None: ...

class CarModeActionProvider(Protocol):
    def __call__(
        self, now: datetime, *, current_setpoint_w: float | None = None
    ) -> CarModeAction | None: ...

class BatteryDriver(Protocol):
    def apply(self, mode: PhysicalMode) -> bool: ...

class BatteryController(Protocol):
    driver: BatteryDriver
    def decide(self, intent: object, now: datetime, **kwargs: object) -> ActionDecision: ...

type TickCallback = Callable[[datetime], None]
type ClockCallback = Callable[[], datetime]
