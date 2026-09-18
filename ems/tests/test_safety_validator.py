from datetime import UTC, datetime, timedelta

from ems.control.safety import SafetyValidator
from ems.domain import BatteryIntent


def test_failsafe_and_freshness_gate():
    now = datetime.now(UTC)
    validator = SafetyValidator(data_quality=lambda _: "unsafe", validate_plan=lambda p, n: p)
    assert not validator.data_is_safe(now)
    assert (
        validator.failsafe(BatteryIntent.GRID_CHARGE_TO_TARGET, now)[0]
        is BatteryIntent.ALLOW_SELF_CONSUMPTION
    )


def test_reserve_dwell_and_switch_cap_boundaries():
    now = datetime.now(UTC)
    validator = SafetyValidator(data_quality=lambda _: "ok", validate_plan=lambda p, n: p)
    assert validator.reserve_reached(10, 10)
    assert validator.reserve_reached(10.5, 10, margin_pp=1)
    assert validator.dwell_elapsed(now, now - timedelta(minutes=10), timedelta(minutes=10))
    assert not validator.dwell_elapsed(now, now - timedelta(minutes=9), timedelta(minutes=10))
    assert validator.switch_cap_reached(10, 10)
    assert not validator.switch_cap_reached(9, 10)
