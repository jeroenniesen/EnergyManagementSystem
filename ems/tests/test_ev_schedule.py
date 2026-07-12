"""Tests for ems.ev_schedule: the weekly minimum-charge schedule model — tolerant parsing and
deadline materialization (design doc: docs/superpowers/specs/2026-07-12-ev-charging-design.md,
"Weekly schedule" + DST note). Pure module; no I/O, no clock reads besides the injected `now`."""
from __future__ import annotations

import json
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from ems.ev_schedule import DAYS, default_schedule, materialize_deadlines, parse_schedule

TZ = ZoneInfo("Europe/Amsterdam")


# --- default_schedule -------------------------------------------------------------------------

def test_default_schedule_shape():
    d = default_schedule()
    assert set(d) == set(DAYS)
    assert len(d) == 7
    for day in DAYS:
        assert d[day] == {"enabled": False, "min_pct": 80, "ready_by": "07:30"}


def test_default_schedule_is_opt_in():
    assert all(not day["enabled"] for day in default_schedule().values())


# --- parse_schedule: tolerance (never raises) -------------------------------------------------

def test_parse_none_returns_default():
    assert parse_schedule(None) == default_schedule()


def test_parse_garbage_string_returns_default():
    assert parse_schedule("not json{{{") == default_schedule()


def test_parse_non_dict_json_returns_default():
    assert parse_schedule("[1,2,3]") == default_schedule()
    assert parse_schedule("42") == default_schedule()
    assert parse_schedule('"hello"') == default_schedule()


def test_parse_empty_dict_returns_default_shape():
    assert parse_schedule({}) == default_schedule()
    assert parse_schedule("{}") == default_schedule()


def test_parse_accepts_dict_directly():
    raw = {"mon": {"enabled": True, "min_pct": 90, "ready_by": "06:00"}}
    out = parse_schedule(raw)
    assert out["mon"] == {"enabled": True, "min_pct": 90, "ready_by": "06:00"}
    assert out["tue"] == {"enabled": False, "min_pct": 80, "ready_by": "07:30"}  # filled default


def test_parse_accepts_json_string():
    raw = json.dumps({"fri": {"enabled": True, "min_pct": 70, "ready_by": "08:00"}})
    out = parse_schedule(raw)
    assert out["fri"] == {"enabled": True, "min_pct": 70, "ready_by": "08:00"}


def test_parse_fills_missing_days():
    out = parse_schedule({"wed": {"enabled": True, "min_pct": 60, "ready_by": "05:45"}})
    assert set(out) == set(DAYS)
    assert out["mon"]["enabled"] is False
    assert out["wed"] == {"enabled": True, "min_pct": 60, "ready_by": "05:45"}


def test_parse_drops_unknown_keys():
    out = parse_schedule(
        {"mon": {"enabled": True, "min_pct": 50, "ready_by": "06:00"}, "xyz": {"enabled": True}}
    )
    assert "xyz" not in out
    assert set(out) == set(DAYS)


@pytest.mark.parametrize(
    "bad_pct,expected",
    [(-10, 0), (150, 100), ("abc", 80), (None, 80), (55.6, 56), (0, 0), (100, 100)],
)
def test_parse_clamps_min_pct(bad_pct, expected):
    out = parse_schedule({"mon": {"enabled": True, "min_pct": bad_pct, "ready_by": "07:00"}})
    assert out["mon"]["min_pct"] == expected
    assert isinstance(out["mon"]["min_pct"], int)


@pytest.mark.parametrize("bad_time", ["25:00", "07:60", "7:30", "", None, 1230, "noon", "07-30"])
def test_parse_bad_ready_by_falls_back(bad_time):
    out = parse_schedule({"mon": {"enabled": True, "min_pct": 80, "ready_by": bad_time}})
    assert out["mon"]["ready_by"] == "07:30"


def test_parse_valid_ready_by_kept():
    out = parse_schedule({"mon": {"enabled": True, "min_pct": 80, "ready_by": "23:59"}})
    assert out["mon"]["ready_by"] == "23:59"
    out2 = parse_schedule({"mon": {"enabled": True, "min_pct": 80, "ready_by": "00:00"}})
    assert out2["mon"]["ready_by"] == "00:00"


def test_parse_day_not_a_dict_falls_back_entirely():
    out = parse_schedule({"mon": "banana"})
    assert out["mon"] == {"enabled": False, "min_pct": 80, "ready_by": "07:30"}


def test_parse_enabled_coerced_to_bool():
    out = parse_schedule({"mon": {"enabled": "yes", "min_pct": 80, "ready_by": "07:30"}})
    assert out["mon"]["enabled"] is True
    out2 = parse_schedule({"mon": {"enabled": 0, "min_pct": 80, "ready_by": "07:30"}})
    assert out2["mon"]["enabled"] is False


def test_parse_never_raises_on_wild_input():
    for garbage in [123, 12.5, True, ["a", "b"], {"mon": None}, {"mon": []}, object()]:
        out = parse_schedule(garbage)  # type: ignore[arg-type]
        assert set(out) == set(DAYS)


# --- materialize_deadlines ---------------------------------------------------------------------

def test_materialize_orders_next_occurrences():
    schedule = default_schedule()
    schedule["mon"]["enabled"] = True
    schedule["fri"]["enabled"] = True
    now = datetime(2026, 7, 15, 10, 0, tzinfo=TZ)  # Wednesday
    out = materialize_deadlines(schedule, now, TZ)
    assert [d["day"] for d in out] == ["fri", "mon"]
    assert out[0]["ready_by"] == datetime(2026, 7, 17, 7, 30, tzinfo=TZ)  # this Friday
    assert out[1]["ready_by"] == datetime(2026, 7, 20, 7, 30, tzinfo=TZ)  # next Monday
    assert out[0]["min_pct"] == 80
    assert out[0]["ready_by"] < out[1]["ready_by"]


def test_materialize_today_later_stays_today():
    schedule = default_schedule()
    schedule["wed"] = {"enabled": True, "min_pct": 85, "ready_by": "18:00"}
    now = datetime(2026, 7, 15, 10, 0, tzinfo=TZ)  # Wednesday 10:00, deadline 18:00 today
    out = materialize_deadlines(schedule, now, TZ)
    assert len(out) == 1
    assert out[0]["ready_by"] == datetime(2026, 7, 15, 18, 0, tzinfo=TZ)
    assert out[0]["day"] == "wed"


def test_materialize_today_earlier_rolls_to_next_week():
    schedule = default_schedule()
    schedule["wed"] = {"enabled": True, "min_pct": 85, "ready_by": "07:00"}
    now = datetime(2026, 7, 15, 10, 0, tzinfo=TZ)  # Wednesday 10:00, deadline 07:00 already passed
    out = materialize_deadlines(schedule, now, TZ)
    assert len(out) == 1
    assert out[0]["ready_by"] == datetime(2026, 7, 22, 7, 0, tzinfo=TZ)  # +7 days


def test_materialize_no_enabled_days_returns_empty():
    out = materialize_deadlines(default_schedule(), datetime(2026, 7, 15, 10, 0, tzinfo=TZ), TZ)
    assert out == []


def test_materialize_all_days_enabled_covers_next_seven_days_once_each():
    schedule = {day: {"enabled": True, "min_pct": 80, "ready_by": "07:30"} for day in DAYS}
    now = datetime(2026, 7, 15, 10, 0, tzinfo=TZ)  # Wednesday 10:00
    out = materialize_deadlines(schedule, now, TZ)
    assert len(out) == 7
    assert {d["day"] for d in out} == set(DAYS)
    # strictly ascending, every deadline in the future relative to now
    for i in range(len(out) - 1):
        assert out[i]["ready_by"] < out[i + 1]["ready_by"]
    assert all(d["ready_by"] > now for d in out)


def test_materialize_requires_aware_now():
    with pytest.raises(ValueError):
        materialize_deadlines(default_schedule(), datetime(2026, 7, 15, 10, 0), TZ)


def test_materialize_dst_fallback_day_wall_clock_correct():
    # 2026-10-25 is the EU fall-back Sunday (clocks 03:00 -> 02:00 local, CEST -> CET).
    schedule = default_schedule()
    schedule["sun"] = {"enabled": True, "min_pct": 80, "ready_by": "07:30"}
    # `now` sits earlier that same Sunday, still CEST (+02:00, unambiguous — well before the
    # 02:00-03:00 repeated hour), to prove the deadline isn't computed by adding a fixed offset.
    now = datetime(2026, 10, 25, 0, 30, tzinfo=TZ)
    assert now.utcoffset().total_seconds() == 2 * 3600  # sanity: now is still CEST
    out = materialize_deadlines(schedule, now, TZ)
    assert len(out) == 1
    deadline = out[0]["ready_by"]
    assert (deadline.year, deadline.month, deadline.day) == (2026, 10, 25)
    assert (deadline.hour, deadline.minute) == (7, 30)
    # After the 03:00 fall-back, 07:30 local is CET (+01:00) — NOT +02:00, which a naive
    # fixed-offset add from `now` would incorrectly produce.
    assert deadline.utcoffset().total_seconds() == 1 * 3600


def test_materialize_dst_rolled_next_week_uses_new_offset():
    # A deadline that has already passed today, rolling across the DST fall-back into next week,
    # must land on the correct (new) offset for that future date.
    schedule = default_schedule()
    schedule["sun"] = {"enabled": True, "min_pct": 80, "ready_by": "07:30"}
    now = datetime(2026, 10, 25, 12, 0, tzinfo=TZ)  # Sunday noon, CET already, deadline passed
    out = materialize_deadlines(schedule, now, TZ)
    assert len(out) == 1
    deadline = out[0]["ready_by"]
    assert (deadline.year, deadline.month, deadline.day) == (2026, 11, 1)  # next Sunday
    assert (deadline.hour, deadline.minute) == (7, 30)
    assert deadline.utcoffset().total_seconds() == 1 * 3600  # still CET, no more transitions
