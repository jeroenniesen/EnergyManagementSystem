from datetime import UTC, datetime, timedelta

from ems.control.override import NONE, Override, from_stored
from ems.domain import BatteryIntent


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
