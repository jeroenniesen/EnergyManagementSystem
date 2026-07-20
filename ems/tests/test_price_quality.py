import math
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from ems.price_quality import validate_price_horizon
from ems.sources.prices import MockPriceSource, PriceSlot

AMS = ZoneInfo("Europe/Amsterdam")
SLOT = timedelta(minutes=15)


def _day(day: date) -> list[PriceSlot]:
    start = datetime.combine(day, time.min, AMS).astimezone(UTC)
    end = datetime.combine(day + timedelta(days=1), time.min, AMS).astimezone(UTC)
    slots = []
    cursor = start
    while cursor < end:
        slots.append(PriceSlot(cursor, 0.20))
        cursor += SLOT
    return slots


@pytest.mark.parametrize(
    ("day", "count"),
    [(date(2026, 2, 1), 96), (date(2026, 3, 29), 92), (date(2026, 10, 25), 100)],
)
def test_complete_amsterdam_days_are_valid_across_dst(day, count):
    slots = _day(day)
    assert len(slots) == count
    now = slots[len(slots) // 2].start
    assert validate_price_horizon(slots, now=now, site_tz=AMS).ok is True


@pytest.mark.parametrize("mutation", ["gap", "duplicate", "unsorted"])
def test_missing_duplicate_or_unsorted_slots_are_rejected(mutation):
    slots = _day(date(2026, 7, 19))
    if mutation == "gap":
        del slots[20]
    elif mutation == "duplicate":
        slots.insert(20, slots[20])
    else:
        slots[20], slots[21] = slots[21], slots[20]
    status = validate_price_horizon(slots, now=slots[40].start, site_tz=AMS)
    assert status.ok is False
    assert status.reason


def test_naive_off_grid_non_finite_and_expired_horizons_are_rejected():
    valid = _day(date(2026, 7, 19))
    now = valid[40].start
    variants = [
        [PriceSlot(s.start.replace(tzinfo=None) if i == 10 else s.start, s.eur_per_kwh)
         for i, s in enumerate(valid)],
        [PriceSlot(s.start + timedelta(minutes=1) if i == 10 else s.start, s.eur_per_kwh)
         for i, s in enumerate(valid)],
        [PriceSlot(s.start, math.nan if i == 10 else s.eur_per_kwh)
         for i, s in enumerate(valid)],
    ]
    for slots in variants:
        assert validate_price_horizon(slots, now=now, site_tz=AMS).ok is False
    assert validate_price_horizon(valid, now=now + timedelta(days=2), site_tz=AMS).ok is False


@pytest.mark.parametrize(
    ("now", "count"),
    [
        (datetime(2026, 3, 29, 12, tzinfo=AMS), 92 + 96),
        (datetime(2026, 10, 25, 12, tzinfo=AMS), 100 + 96),
    ],
)
def test_default_mock_source_emits_two_complete_local_days_across_dst(now, count):
    slots = MockPriceSource(AMS, clock=lambda: now).slots()
    assert len(slots) == count
    assert validate_price_horizon(slots, now=now, site_tz=AMS).ok is True
