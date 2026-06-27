from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from ems.sources.forecast import (
    SLOTS_PER_DAY,
    MockSolarForecastSource,
    day_kwh_p50,
    orientation_factor,
    p50_watts,
)

AMS = ZoneInfo("Europe/Amsterdam")
NOON = datetime(2026, 6, 27, 10, 0, tzinfo=UTC)


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


def test_orientation_factor_best_facing_south_at_35_deg():
    # Optimal (south, 35°) is the reference 1.0; tilted/rotated away derates; never below the floor.
    assert orientation_factor(35.0, 0.0) == 1.0
    assert orientation_factor(0.0, 0.0) < 1.0  # flat
    assert orientation_factor(35.0, 90.0) < orientation_factor(35.0, 0.0)  # west vs south
    assert orientation_factor(90.0, 180.0) >= 0.3 * 0.3  # floored, never zero


def test_kwp_scales_forecast_energy():
    small = MockSolarForecastSource(AMS, kwp=2.0, clock=_clock_at(NOON))
    big = MockSolarForecastSource(AMS, kwp=6.0, clock=_clock_at(NOON))
    assert day_kwh_p50(big.slots()) > day_kwh_p50(small.slots())


def test_orientation_reduces_forecast_energy():
    south = MockSolarForecastSource(AMS, clock=_clock_at(NOON), tilt=35.0, azimuth=0.0)
    west_flat = MockSolarForecastSource(AMS, clock=_clock_at(NOON), tilt=10.0, azimuth=120.0)
    assert day_kwh_p50(west_flat.slots()) < day_kwh_p50(south.slots())
