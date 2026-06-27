"""Timezone-aware 15-minute slot utilities (SPEC §13.1). Naive datetimes are rejected."""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

SLOT_MINUTES = 15


def require_aware(dt: datetime, name: str = "datetime") -> datetime:
    """Reject naive datetimes — they must never enter EMS logic (SPEC §13.1/§4.7)."""
    if dt.tzinfo is None or dt.tzinfo.utcoffset(dt) is None:
        raise ValueError(f"{name} must be tz-aware (SPEC §13.1)")
    return dt


def slot_start(dt: datetime, tz: ZoneInfo) -> datetime:
    """Floor a tz-aware datetime to the start of its 15-minute slot, expressed in `tz`."""
    require_aware(dt)
    local = dt.astimezone(tz)
    floored = (local.minute // SLOT_MINUTES) * SLOT_MINUTES
    return local.replace(minute=floored, second=0, microsecond=0)


def day_slot_count(day: date, tz: ZoneInfo) -> int:
    """Number of 15-minute slots in the local calendar day `day` (DST-aware: 96/92/100)."""
    # Convert to UTC before subtracting: datetime subtraction between two aware datetimes
    # that share the same tzinfo ignores DST and returns the naive wall-clock difference.
    start = datetime(day.year, day.month, day.day, tzinfo=tz).astimezone(UTC)
    nxt = day + timedelta(days=1)
    end = datetime(nxt.year, nxt.month, nxt.day, tzinfo=tz).astimezone(UTC)
    seconds = (end - start).total_seconds()
    return int(seconds // (SLOT_MINUTES * 60))
