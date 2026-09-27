from datetime import UTC, datetime, timedelta

from ems.control.override import MAX_MINUTES, NONE, Override, as_plan, from_stored
from ems.domain import BatteryIntent
from ems.planner.validator import validate_plan


def _at(minutes_from_now: float) -> datetime:
    return datetime(2026, 6, 27, 12, 0, tzinfo=UTC) + timedelta(minutes=minutes_from_now)


def test_none_is_never_active():
    assert NONE.is_set is False
    assert NONE.active(_at(0)) is False
    assert NONE.seconds_remaining(_at(0)) == 0


def test_active_until_expiry_then_inactive():
    ov = Override(BatteryIntent.GRID_CHARGE_TO_TARGET, _at(30))
    now = _at(0)
    assert ov.active(now) is True
    assert ov.seconds_remaining(now) == 30 * 60
    assert ov.active(_at(31)) is False  # past expiry
    assert ov.seconds_remaining(_at(31)) == 0


def test_to_dict_shape():
    ov = Override(BatteryIntent.HOLD_RESERVE, _at(10))
    d = ov.to_dict(_at(0))
    assert d["intent"] == "hold_reserve"
    assert d["active"] is True
    assert d["seconds_remaining"] == 600
    assert d["expires_at"].startswith("2026-06-27T12:10")


def test_from_stored_roundtrip():
    exp = _at(15)
    ov = from_stored("discharge_for_load", exp.isoformat())
    assert ov.intent is BatteryIntent.DISCHARGE_FOR_LOAD
    assert ov.expires_at == exp


def test_from_stored_tolerates_bad_values():
    assert from_stored(None, None) is NONE
    assert from_stored("not_an_intent", _at(5).isoformat()) is NONE
    assert from_stored("hold_reserve", "not-a-date") is NONE
    # A naive (tz-less) expiry must degrade to NONE, not crash the comparison later.
    assert from_stored("hold_reserve", "2026-06-27T12:10:00") is NONE


def test_max_minutes_is_bounded_not_24h():
    # #135: a forgotten override must not strand the battery at max power for a full day.
    assert MAX_MINUTES == 8 * 60
    assert MAX_MINUTES < 24 * 60


def test_as_plan_covers_override_window_for_validator():
    now = _at(0)
    ov = Override(BatteryIntent.GRID_CHARGE_TO_TARGET, _at(60))
    plan = as_plan(ov, now, target_soc=100.0, power_w=4000.0, floor_soc=10.0)
    assert plan.strategy == "manual"
    assert plan.planner_mode == "manual_override"
    assert len(plan.slots) == 1
    slot = plan.slots[0]
    assert slot.intent is BatteryIntent.GRID_CHARGE_TO_TARGET
    assert slot.target_soc == 100.0
    assert slot.power_w == 4000.0
    assert slot.start == now
    assert slot.slot_end == ov.expires_at
    # A clean charge override must be admissible through the same §8.11 gate as auto plans.
    v = validate_plan(plan, soc_pct=40.0, data_quality="complete", min_reserve_soc=10.0)
    assert v.ok


def test_as_plan_with_target_below_reserve_is_unsafe():
    # #135 AC3: an unsafe override energy contract is rejected by the validator.
    now = _at(0)
    ov = Override(BatteryIntent.GRID_CHARGE_TO_TARGET, _at(30))
    plan = as_plan(ov, now, target_soc=5.0, power_w=4000.0, floor_soc=10.0)
    v = validate_plan(plan, soc_pct=40.0, data_quality="complete", min_reserve_soc=10.0)
    assert not v.ok
    assert any(f.code == "target_below_reserve" for f in v.findings)
