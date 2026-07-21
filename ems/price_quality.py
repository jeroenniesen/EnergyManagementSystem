"""Completeness checks for price horizons before they may drive control (SPEC §6.2)."""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from ems.sources.prices import SLOT, PriceSlot


@dataclass(frozen=True)
class PriceHorizonStatus:
    ok: bool
    reason: str | None
    slot_count: int
    horizon_end: datetime | None


def _invalid(reason: str, slots: list[PriceSlot]) -> PriceHorizonStatus:
    end = slots[-1].start + SLOT if slots and slots[-1].start.tzinfo is not None else None
    return PriceHorizonStatus(False, reason, len(slots), end)


def validate_price_horizon(
    slots: list[PriceSlot], *, now: datetime, site_tz: ZoneInfo,
) -> PriceHorizonStatus:
    """Require complete, ordered 15-minute local days that include the current instant.

    Expected day length is derived from local-midnight UTC boundaries, naturally yielding
    92/96/100 quarter-hours across Amsterdam DST transitions.
    """
    if not slots:
        return _invalid("price horizon is empty", slots)
    if now.tzinfo is None:
        return _invalid("current time is timezone-naive", slots)

    starts: list[datetime] = []
    previous: datetime | None = None
    for slot in slots:
        start = slot.start
        if start.tzinfo is None:
            return _invalid("price slot is timezone-naive", slots)
        if not math.isfinite(float(slot.eur_per_kwh)):
            return _invalid("price slot is non-finite", slots)
        start_utc = start.astimezone(UTC)
        if start_utc.second or start_utc.microsecond or start_utc.minute % 15:
            return _invalid("price slot is not aligned to a quarter-hour", slots)
        if previous is not None and start_utc <= previous:
            return _invalid("price slots are duplicate or out of order", slots)
        if previous is not None and start_utc - previous != SLOT:
            return _invalid("price horizon contains a gap", slots)
        starts.append(start_utc)
        previous = start_utc

    present = set(starts)
    local_days = {start.astimezone(site_tz).date() for start in starts}
    for day in sorted(local_days):
        day_start = datetime.combine(day, time.min, site_tz).astimezone(UTC)
        day_end = datetime.combine(day + timedelta(days=1), time.min, site_tz).astimezone(UTC)
        expected: set[datetime] = set()
        cursor = day_start
        while cursor < day_end:
            expected.add(cursor)
            cursor += SLOT
        if not expected <= present:
            return _invalid(
                f"price day {day.isoformat()} is incomplete "
                f"({len(expected & present)}/{len(expected)} slots)", slots,
            )

    now_utc = now.astimezone(UTC)
    horizon_end = starts[-1] + SLOT
    if not starts[0] <= now_utc < horizon_end:
        return _invalid("price horizon does not cover the current time", slots)
    return PriceHorizonStatus(True, None, len(slots), horizon_end)
