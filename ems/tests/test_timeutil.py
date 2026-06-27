from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import pytest

from ems.timeutil import day_slot_count, slot_start

AMS = ZoneInfo("Europe/Amsterdam")


def test_slot_start_floors_to_quarter_hour():
    dt = datetime(2026, 6, 27, 10, 7, 30, tzinfo=AMS)
    assert slot_start(dt, AMS) == datetime(2026, 6, 27, 10, 0, tzinfo=AMS)
    dt2 = datetime(2026, 6, 27, 10, 49, tzinfo=AMS)
    assert slot_start(dt2, AMS) == datetime(2026, 6, 27, 10, 45, tzinfo=AMS)


def test_slot_start_rejects_naive():
    with pytest.raises(ValueError):
        slot_start(datetime(2026, 6, 27, 10, 0), AMS)


def test_slot_start_converts_utc_to_local_slot():
    dt = datetime(2026, 6, 27, 8, 7, tzinfo=UTC)  # 10:07 CEST
    assert slot_start(dt, AMS) == datetime(2026, 6, 27, 10, 0, tzinfo=AMS)


def test_day_slot_counts_dst_amsterdam():
    assert day_slot_count(date(2026, 6, 27), AMS) == 96  # normal
    assert day_slot_count(date(2026, 3, 29), AMS) == 92  # spring forward (23h)
    assert day_slot_count(date(2026, 10, 25), AMS) == 100  # fall back (25h)
