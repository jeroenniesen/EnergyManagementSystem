from ems.control.failsafe import failsafe_intent
from ems.domain import BatteryIntent


def test_unsafe_forces_self_consumption_with_reason():
    for intent in (
        BatteryIntent.GRID_CHARGE_TO_TARGET,
        BatteryIntent.HOLD_RESERVE,
        BatteryIntent.DISCHARGE_FOR_LOAD,
    ):
        out, reason = failsafe_intent(intent, "unsafe")
        assert out is BatteryIntent.ALLOW_SELF_CONSUMPTION
        assert reason is not None and "fail-safe" in reason


def test_unsafe_keeps_self_consumption_without_reason():
    out, reason = failsafe_intent(BatteryIntent.ALLOW_SELF_CONSUMPTION, "unsafe")
    assert out is BatteryIntent.ALLOW_SELF_CONSUMPTION
    assert reason is None


def test_safe_quality_passes_intent_through():
    for dq in ("complete", "degraded", "price_fallback"):
        out, reason = failsafe_intent(BatteryIntent.GRID_CHARGE_TO_TARGET, dq)
        assert out is BatteryIntent.GRID_CHARGE_TO_TARGET
        assert reason is None
