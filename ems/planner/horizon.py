"""Planning horizon beyond 24 hours (#329 / audit F-09).

The window runs through the end of the next local day when that day's prices are
complete and contiguous. Otherwise it is the configured fallback (96 slots, the
same slice the planners used before). Sizing stays the planners' existing
per-peak deadline math; this module only chooses how many price slots they see.
"""
from __future__ import annotations

from datetime import UTC, datetime, time, timedelta

SLOT = timedelta(minutes=15)
DEFAULT_HORIZON_SLOTS = 96
MAX_HORIZON_SLOTS = 192
_ABSURD_SLOT_COUNT = 400


def end_of_next_local_day(now: datetime, site_tz) -> datetime | None:
    """Exclusive end of the local day after ``now`` (DST-aware midnight)."""
    if now.tzinfo is None or site_tz is None:
        return None
    try:
        local_date = now.astimezone(site_tz).date()
        return datetime.combine(local_date + timedelta(days=2), time.min, site_tz)
    except (OverflowError, TypeError, ValueError):
        return None


def _utc(moment: datetime) -> datetime:
    return moment.astimezone(UTC)


def _quarter_floor_utc(moment: datetime) -> datetime:
    utc = _utc(moment).replace(second=0, microsecond=0)
    return utc.replace(minute=(utc.minute // 15) * 15)


def _expected_starts(now: datetime, end: datetime) -> list[datetime] | None:
    """Quarter-hour UTC starts from the still-open slot through ``end``."""
    now_utc = _utc(now)
    first = _quarter_floor_utc(now)
    if first + SLOT <= now_utc:
        first += SLOT
    end_utc = _utc(end)
    if first >= end_utc:
        return []
    starts: list[datetime] = []
    cursor = first
    while cursor < end_utc:
        starts.append(cursor)
        cursor += SLOT
        if len(starts) > _ABSURD_SLOT_COUNT:
            return None
    return starts


def _future_prices(prices, now: datetime) -> list:
    now_utc = _utc(now) if now.tzinfo is not None else now
    out = []
    for price in prices:
        start = price.start
        if start.tzinfo is None or now.tzinfo is None:
            if start + SLOT > now:
                out.append(price)
            continue
        if _utc(start) + SLOT > now_utc:
            out.append(price)
    return out


def next_local_day_prices_complete(prices, now: datetime, site_tz) -> bool:
    """True when every quarter-hour through the end of the next local day is present.

    The slots in that window must be contiguous and in order. A gap, a duplicate,
    or a day that stops short of the next local midnight is incomplete.
    """
    end = end_of_next_local_day(now, site_tz)
    if end is None:
        return False
    expected = _expected_starts(now, end)
    if not expected:
        return False
    end_utc = _utc(end)
    now_utc = _utc(now)
    window: list[datetime] = []
    for price in prices:
        start = getattr(price, "start", None)
        if not isinstance(start, datetime) or start.tzinfo is None:
            return False
        start_utc = _utc(start).replace(microsecond=0)
        if start_utc + SLOT > now_utc and start_utc < end_utc:
            window.append(start_utc)
    if len(window) != len(expected):
        return False
    return all(got == want for got, want in zip(window, expected, strict=True))


def slice_price_horizon(
    prices, now: datetime, site_tz, *, fallback_slots: int = DEFAULT_HORIZON_SLOTS,
):
    """Future price slots the planners keep.

    Complete next-local-day prices → that window, capped at 192 slots. Otherwise
    exactly ``future[:fallback_slots]``, which is today's 96-slot behaviour when
    the caller passes the configured horizon.
    """
    future = _future_prices(prices, now)
    if now.tzinfo is None or not next_local_day_prices_complete(prices, now, site_tz):
        return future[:fallback_slots]
    end = end_of_next_local_day(now, site_tz)
    expected = _expected_starts(now, end) if end is not None else None
    if not expected:
        return future[:fallback_slots]
    return future[: min(MAX_HORIZON_SLOTS, len(expected))]
