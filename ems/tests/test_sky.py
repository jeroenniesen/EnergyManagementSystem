"""Sunrise/sunset (NOAA approximation) for the time-of-day sky. Loose bounds — a gradient needs
minutes-level accuracy, not seconds — plus the polar edge case."""
from datetime import date
from zoneinfo import ZoneInfo

from ems.sky import sun_times

AMS = ZoneInfo("Europe/Amsterdam")


def test_summer_sun_times_nl():
    sr, ss = sun_times(52.13, 5.29, date(2026, 7, 1), AMS)
    assert sr is not None and ss is not None
    assert sr.hour in (4, 5, 6)   # ~05:21 CEST
    assert ss.hour in (21, 22)    # ~22:02 CEST
    assert (ss - sr).total_seconds() > 15 * 3600  # a long summer day


def test_winter_sun_times_nl():
    sr, ss = sun_times(52.13, 5.29, date(2026, 12, 21), AMS)
    assert sr.hour in (8, 9)      # ~08:45 CET
    assert ss.hour in (16, 17)    # ~16:28 CET
    assert (ss - sr).total_seconds() < 9 * 3600  # a short winter day


def test_polar_day_returns_none():
    # Svalbard at midsummer — the sun never sets, so there is no sunrise/sunset.
    sr, ss = sun_times(78.2, 15.6, date(2026, 6, 21), ZoneInfo("UTC"))
    assert sr is None and ss is None
