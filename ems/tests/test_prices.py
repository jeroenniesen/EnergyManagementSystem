from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from ems.sources.prices import (
    SLOTS_PER_DAY,
    MockPriceSource,
    current_price,
    price_for_hour,
)

AMS = ZoneInfo("Europe/Amsterdam")


def _clock_at(dt):
    return lambda: dt


def test_price_curve_night_cheaper_than_evening_peak():
    assert price_for_hour(3) < price_for_hour(18)
    assert price_for_hour(8) > price_for_hour(3)  # morning peak > night


def test_mock_source_returns_two_days_of_quarter_hours():
    src = MockPriceSource(AMS, clock=_clock_at(datetime(2026, 6, 27, 10, 0, tzinfo=UTC)))
    slots = src.slots()
    assert len(slots) == 2 * SLOTS_PER_DAY
    assert all(s.start.tzinfo is not None for s in slots)
    # strictly increasing, 15-min apart
    assert slots[1].start - slots[0].start == timedelta(minutes=15)


def test_current_price_picks_slot_covering_now():
    now = datetime(2026, 6, 27, 18, 7, tzinfo=AMS)  # evening peak
    src = MockPriceSource(AMS, clock=_clock_at(now))
    assert current_price(src.slots(), now) == price_for_hour(18)


def test_current_price_none_outside_horizon():
    now = datetime(2026, 6, 27, 12, 0, tzinfo=AMS)
    src = MockPriceSource(AMS, clock=_clock_at(now))
    far_future = datetime(2030, 1, 1, tzinfo=AMS)
    assert current_price(src.slots(), far_future) is None
