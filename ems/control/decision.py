"""Pure control intent decisions.

This module deliberately contains no device, web, or persistence dependencies.  The engine is
fed observations and callbacks by :class:`ControlService`; it owns no mutable control state.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any

from ems.control.car_mode import CarModeAction
from ems.control.safety import SafetyValidator
from ems.domain import BatteryIntent


class ControlDecisionEngine:
    """Resolve an effective intent and apply the car-charging safety guardrail."""

    def __init__(
        self,
        *,
        data_quality: Callable[[datetime], str],
        car_mode_action: Callable[..., CarModeAction | None],
        car_session_active: Callable[[], bool],
        settings: dict[str, Any],
        site_tz=None,
        allow_export_discharge: Callable[[], bool] | None = None,
        validate_plan: Callable[[Any, datetime], Any] | None = None,
        safety: SafetyValidator | None = None,
    ):
        self._data_quality = data_quality
        self._car_mode_action = car_mode_action
        self._car_session_active = car_session_active
        self._settings = settings
        self._site_tz = site_tz
        self._allow_export_discharge = allow_export_discharge or (lambda: False)
        self._safety = safety or SafetyValidator(
            data_quality=data_quality, validate_plan=validate_plan or (lambda plan, now: plan)
        )

    def _car_guard(
        self,
        now: datetime,
        intent: BatteryIntent | None,
        reason: str | None,
        *,
        current_setpoint_w: float | None = None,
    ):
        if intent is None or intent not in (
            BatteryIntent.DISCHARGE_FOR_LOAD,
            BatteryIntent.ALLOW_SELF_CONSUMPTION,
        ):
            return intent, reason, None
        action = self._car_mode_action(now, current_setpoint_w=current_setpoint_w)
        if action is None:
            return intent, reason, None
        if action.action == "discharge":
            if self._data_quality(now) == "unsafe":
                return (
                    BatteryIntent.HOLD_RESERVE,
                    "car charging — holding the battery so it won't discharge into the car "
                    "(sensor data is unsafe, so EMS won't discharge on an untrusted level)",
                    None,
                )
            return BatteryIntent.DISCHARGE_FOR_LOAD, action.reason, action
        if action.action == "hold":
            if action.reserve_hold and self._car_session_active():
                return BatteryIntent.HOLD_RESERVE, action.reason, action
            return BatteryIntent.HOLD_RESERVE, action.reason, None
        return intent, reason, None

    def effective_intent(
        self,
        now: datetime,
        *,
        override,
        current_plan: Callable[[], Any],
        price_horizon_status,
        validate_plan: Callable[[Any, datetime], Any],
    ):
        """Return the legacy seven-element effective-intent tuple."""
        cur = None
        val = None
        if override.active(now):
            assert override.intent is not None and override.expires_at is not None
            until = (
                override.expires_at.astimezone(self._site_tz).strftime("%H:%M")
                if self._site_tz
                else override.expires_at.strftime("%H:%M")
            )
            intent, override_active = override.intent, True
            risky = intent is not BatteryIntent.ALLOW_SELF_CONSUMPTION
            if risky and self._data_quality(now) == "unsafe":
                intent = BatteryIntent.ALLOW_SELF_CONSUMPTION
                reason = (
                    f"manual override held — sensor data is unsafe, so EMS won't force "
                    f"{override.intent.value}; holding self-consumption until {until}"
                )
            else:
                reason = f"manual override: {override.intent.value} until {until}"
        else:
            pp = current_plan()
            if pp is None:
                if callable(price_horizon_status):
                    price_horizon_status = price_horizon_status()
                if price_horizon_status is not None and not price_horizon_status.ok:
                    return (
                        BatteryIntent.ALLOW_SELF_CONSUMPTION,
                        "holding self-consumption — incomplete prices: "
                        f"{price_horizon_status.reason}",
                        False,
                        None,
                        None,
                        None,
                        None,
                    )
                return None, None, False, None, None, None, None
            cur = pp[2].intent_at(now)
            if cur is None:
                return None, None, False, None, None, None, None
            val = self._safety.validate(pp[2], now)
            if not val.ok:
                top = next((f for f in val.findings if f.severity == "unsafe"), None)
                note = top.message if top is not None else "plan failed validation"
                cur = None
                intent, reason, override_active = (
                    BatteryIntent.ALLOW_SELF_CONSUMPTION,
                    f"holding self-consumption — {note}",
                    False,
                )
            else:
                safe, fs_reason = self._safety.failsafe(cur.intent, now)
                intent, reason = (
                    (safe, fs_reason) if fs_reason is not None else (cur.intent, cur.reason)
                )
                override_active = False
        intent, reason, car_action = self._car_guard(now, intent, reason)
        target_soc = power_w = None
        if car_action is not None and car_action.action == "discharge":
            power_w = car_action.power_w
            target_soc = self._settings["battery.min_reserve_soc"]
        elif override_active:
            if intent is BatteryIntent.GRID_CHARGE_TO_TARGET:
                target_soc, power_w = 100.0, self._settings["battery.max_charge_w"]
            elif intent is BatteryIntent.DISCHARGE_FOR_LOAD and self._allow_export_discharge():
                target_soc, power_w = (
                    self._settings["battery.min_reserve_soc"],
                    self._settings["battery.max_discharge_w"],
                )
        elif cur is not None and intent is cur.intent:
            if intent is BatteryIntent.GRID_CHARGE_TO_TARGET:
                target_soc, power_w = cur.target_soc, cur.power_w
            elif intent is BatteryIntent.DISCHARGE_FOR_LOAD and self._allow_export_discharge():
                target_soc, power_w = cur.floor_soc, cur.power_w
        return intent, reason, override_active, target_soc, power_w, val, car_action
