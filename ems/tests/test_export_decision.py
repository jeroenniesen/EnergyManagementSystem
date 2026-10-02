"""B-108: export car-hold and unarmed plan-only reason on ControlDecisionEngine."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from ems.control.car_mode import CarModeAction
from ems.control.decision import ControlDecisionEngine
from ems.control.override import NONE as OVERRIDE_NONE
from ems.domain import BatteryIntent
from ems.planner.schedule import Plan, PlanSlot
from ems.planner.validator import PlanValidation

NOW = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
_PLAN_ONLY = (
    "export plan-only (live forced discharge not armed); holding vendor AUTO"
)


def _engine(*, allow: bool = False, car_action: CarModeAction | None = None):
    return ControlDecisionEngine(
        data_quality=lambda _now: "complete",
        car_mode_action=lambda _now, current_setpoint_w=None: car_action,
        car_session_active=lambda: False,
        settings={
            "battery.min_reserve_soc": 10.0,
            "battery.max_charge_w": 4000.0,
            "battery.max_discharge_w": 4000.0,
        },
        allow_export_discharge=lambda: allow,
        validate_plan=lambda _plan, _now: PlanValidation("valid"),
    )


def _resolve(engine: ControlDecisionEngine, reason: str | None):
    plan = Plan(
        created_at=NOW,
        slots=(PlanSlot(NOW, BatteryIntent.EXPORT_FOR_PROFIT, reason),),  # type: ignore[arg-type]
    )
    return engine.effective_intent(
        NOW,
        override=OVERRIDE_NONE,
        current_plan=lambda _now: (None, None, plan),
        price_horizon_status=None,
        validate_plan=lambda _plan, _now: PlanValidation("valid"),
    )


def test_export_held_while_car_charging():
    """EXPORT_FOR_PROFIT pauses to HOLD_RESERVE; never a car-session discharge."""
    feeding = CarModeAction(
        action="discharge", power_w=1500.0, reason="cover the house", recommand=True,
    )
    intent, reason, override_active, target, power, _val, car_action = _resolve(
        _engine(allow=True, car_action=feeding), "sell the peak",
    )
    assert intent is BatteryIntent.HOLD_RESERVE
    assert reason is not None and "export paused" in reason
    assert car_action is None
    assert override_active is False
    assert target is None and power is None


@pytest.mark.parametrize("raw", [None, ""])
def test_unarmed_export_plan_only_suffix_when_reason_missing(raw):
    intent, reason, *_rest = _resolve(_engine(allow=False), raw)
    assert intent is BatteryIntent.EXPORT_FOR_PROFIT
    assert reason == _PLAN_ONLY


def test_unarmed_export_appends_plan_only_to_existing_reason():
    intent, reason, *_rest = _resolve(_engine(allow=False), "sell the peak")
    assert intent is BatteryIntent.EXPORT_FOR_PROFIT
    assert reason is not None
    assert reason.startswith("sell the peak")
    assert _PLAN_ONLY in reason
