from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from ems.sources.forecast import (
    SLOTS_PER_DAY,
    MockSolarForecastSource,
    day_kwh_p50,
    p50_watts,
)

AMS = ZoneInfo("Europe/Amsterdam")


def _clock_at(dt):
    return lambda: dt


def test_p50_zero_at_night_peak_midday():
    assert p50_watts(datetime(2026, 6, 27, 2, 0, tzinfo=AMS), 3.0) == 0.0
    assert p50_watts(datetime(2026, 6, 27, 23, 0, tzinfo=AMS), 3.0) == 0.0
    midday = p50_watts(datetime(2026, 6, 27, 13, 30, tzinfo=AMS), 3.0)
    assert midday > 1500  # ~0.85 * 3000


def test_p50_zero_at_exact_daylight_boundaries():
    assert p50_watts(datetime(2026, 6, 27, 5, 0, tzinfo=AMS), 3.0) == 0.0
    assert p50_watts(datetime(2026, 6, 27, 21, 0, tzinfo=AMS), 3.0) == 0.0


def test_percentile_band_ordered():
    src = MockSolarForecastSource(AMS, clock=_clock_at(datetime(2026, 6, 27, 13, 0, tzinfo=UTC)))
    midday = next(s for s in src.slots() if s.p50_w > 0)
    assert midday.p10_w <= midday.p50_w <= midday.p90_w


def test_horizon_is_two_days_quarter_hourly():
    src = MockSolarForecastSource(AMS, clock=_clock_at(datetime(2026, 6, 27, 10, 0, tzinfo=UTC)))
    assert len(src.slots()) == 2 * SLOTS_PER_DAY


def test_day_kwh_positive_in_summer():
    src = MockSolarForecastSource(AMS, clock=_clock_at(datetime(2026, 6, 27, 10, 0, tzinfo=UTC)))
    kwh = day_kwh_p50(src.slots())
    assert 5.0 < kwh < 30.0  # plausible daily kWh for 3 kWp
