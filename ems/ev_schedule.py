"""Weekly minimum-charge schedule for the car (design doc: docs/superpowers/specs/
2026-07-12-ev-charging-design.md, "Weekly schedule" semantics).

Canonical shape (all 7 keys always present):

    {"mon": {"enabled": bool, "min_pct": int (0-100), "ready_by": "HH:MM"}, ... "sun": {...}}

`parse_schedule` sits on the settings load path (a persisted runtime value can be anything, incl.
hand-edited garbage) so it is maximally tolerant and NEVER raises — a corrupt schedule must fall
back to `default_schedule()` rather than break the dashboard, mirroring `ems/settings.py`'s
tolerant-on-read convention.

`materialize_deadlines` turns the schedule into concrete, tz-aware deadlines for the planner
(design doc's "math core", `ems/ev_planner.py`). It is DST-safe: every deadline is built by
constructing a wall-clock local datetime for the target calendar date
(`datetime(y, m, d, hh, mm, tzinfo=tz)`), never by adding a fixed-hours offset to `now` — so a
07:30 deadline is always 07:30 local time, whatever the UTC offset is on that particular date.

Pure module: no I/O, no clock reads other than the `now` the caller supplies.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from ems.timeutil import require_aware

DAYS: tuple[str, ...] = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
DEFAULT_MIN_PCT = 80
DEFAULT_READY_BY = "07:30"

_TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


def default_schedule() -> dict[str, dict[str, Any]]:
    """All 7 days, opted OUT (this is an opt-in feature) with sensible defaults for whenever a
    user turns a day on."""
    return {
        day: {"enabled": False, "min_pct": DEFAULT_MIN_PCT, "ready_by": DEFAULT_READY_BY}
        for day in DAYS
    }


def _clamp_min_pct(value: Any) -> int:
    try:
        if isinstance(value, bool):  # bool is an int subclass — reject before the numeric coerce
            raise TypeError
        num = round(float(value))
    except (TypeError, ValueError):
        return DEFAULT_MIN_PCT
    return max(0, min(100, int(num)))


def _valid_ready_by(value: Any) -> str:
    if isinstance(value, str) and _TIME_RE.match(value):
        return value
    return DEFAULT_READY_BY


def _parse_day(raw_day: Any) -> dict[str, Any]:
    if not isinstance(raw_day, dict):
        return {"enabled": False, "min_pct": DEFAULT_MIN_PCT, "ready_by": DEFAULT_READY_BY}
    return {
        "enabled": bool(raw_day.get("enabled", False)),
        "min_pct": _clamp_min_pct(raw_day.get("min_pct", DEFAULT_MIN_PCT)),
        "ready_by": _valid_ready_by(raw_day.get("ready_by", DEFAULT_READY_BY)),
    }


def parse_schedule(raw: str | dict | None) -> dict[str, dict[str, Any]]:
    """Parse a JSON string or dict into the canonical 7-day shape. Tolerant: missing days are
    filled from the default, `min_pct` is clamped to an int 0-100, `ready_by` falls back to
    "07:30" unless it is a valid "HH:MM", unknown top-level keys are dropped, and any garbage
    (unparsable JSON, wrong type, non-dict) falls all the way back to `default_schedule()`.
    NEVER raises."""
    data: Any = raw
    if isinstance(raw, str):
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError, ValueError):
            return default_schedule()
    if not isinstance(data, dict):
        return default_schedule()
    return {day: _parse_day(data.get(day)) for day in DAYS}


def materialize_deadlines(
    schedule: dict[str, dict[str, Any]], now: datetime, tz: ZoneInfo,
) -> list[dict]:
    """The next 7 days' worth of enabled deadlines: for each enabled day, its next occurrence of
    `ready_by` in `tz`, as `{"ready_by": <aware datetime>, "min_pct": int, "day": "mon"}`, sorted
    ascending. A day whose `ready_by` has already passed today rolls to next week (its date is
    shifted +7 days and the wall-clock time is reconstructed for that new date — DST-safe)."""
    require_aware(now, "now")
    local_now = now.astimezone(tz)
    today = local_now.date()
    out: list[dict] = []
    for offset in range(7):
        day_date = today + timedelta(days=offset)
        day_key = DAYS[day_date.weekday()]
        cfg = schedule.get(day_key)
        if not cfg or not cfg.get("enabled"):
            continue
        hh, mm = (int(part) for part in cfg["ready_by"].split(":"))
        candidate = datetime(day_date.year, day_date.month, day_date.day, hh, mm, tzinfo=tz)
        if candidate < local_now:
            rolled = day_date + timedelta(days=7)
            candidate = datetime(rolled.year, rolled.month, rolled.day, hh, mm, tzinfo=tz)
        out.append({"ready_by": candidate, "min_pct": cfg["min_pct"], "day": day_key})
    out.sort(key=lambda d: d["ready_by"])
    return out
