"""Pure safety checks for the control decision path."""
from __future__ import annotations

from datetime import datetime, timedelta

from ems.application.protocols import DataQuality, Plan, PlanValidation, PlanValidator
from ems.control.failsafe import failsafe_intent
from ems.domain import BatteryIntent


class SafetyValidator:
    """Safety façade; command admission and writer fencing remain in ModeController."""
    def __init__(
        self,
        *,
        data_quality: DataQuality,
        validate_plan: PlanValidator,
    ):
        self._data_quality = data_quality
        self._validate_plan = validate_plan
    def data_is_safe(self, now: datetime) -> bool:
        return self._data_quality(now) != "unsafe"
    def validate(self, plan: Plan, now: datetime) -> PlanValidation:
        return self._validate_plan(plan, now)
    def failsafe(self, intent: BatteryIntent, now: datetime) -> tuple[BatteryIntent, str | None]:
        return failsafe_intent(intent, self._data_quality(now))
    @staticmethod
    def reserve_reached(soc_pct: float, reserve_pct: float, *, margin_pp: float = 0.0) -> bool:
        return soc_pct <= reserve_pct + margin_pp
    @staticmethod
    def dwell_elapsed(now: datetime, last_at: datetime | None, dwell: timedelta) -> bool:
        return last_at is None or now - last_at >= dwell
    @staticmethod
    def switch_cap_reached(switches_today: int, max_switches: int) -> bool:
        return switches_today >= max_switches
