"""Sunrise/sunset for the time-of-day sky backdrop — the NOAA sunrise equation, no dependency.

Pure math (accurate to a few minutes at mid-latitudes — plenty for a gradient that fades over a
~90-min twilight window). Returns tz-aware datetimes, or (None, None) at polar day/night. Keeps the
sky location- and season-aware without pulling `astral` into the Pi image.
"""
from __future__ import annotations

import math
from datetime import date as date_cls
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

_UTC = ZoneInfo("UTC")
_ZENITH = 90.833  # official sunrise/sunset zenith (atmospheric refraction + solar radius)


def sun_times(
    lat: float, lon: float, on_date: date_cls, tz: ZoneInfo
) -> tuple[datetime | None, datetime | None]:
    """Sunrise and sunset (tz-aware, in `tz`) for `on_date` at `lat`/`lon` (° N / ° E). (None, None)
    when the sun neither rises nor sets that day (polar). NOAA solar-position approximation."""
    n = on_date.timetuple().tm_yday
    gamma = 2 * math.pi / 365 * (n - 1 + 0.5)  # fractional year at ~solar noon
    eqtime = 229.18 * (
        0.000075 + 0.001868 * math.cos(gamma) - 0.032077 * math.sin(gamma)
        - 0.014615 * math.cos(2 * gamma) - 0.040849 * math.sin(2 * gamma)
    )  # minutes
    decl = (
        0.006918 - 0.399912 * math.cos(gamma) + 0.070257 * math.sin(gamma)
        - 0.006758 * math.cos(2 * gamma) + 0.000907 * math.sin(2 * gamma)
        - 0.002697 * math.cos(3 * gamma) + 0.00148 * math.sin(3 * gamma)
    )  # radians
    lat_r = math.radians(lat)
    cos_ha = (
        math.cos(math.radians(_ZENITH)) / (math.cos(lat_r) * math.cos(decl))
        - math.tan(lat_r) * math.tan(decl)
    )
    if not -1.0 <= cos_ha <= 1.0:
        return None, None  # polar day or night — the sun doesn't cross the horizon
    ha = math.degrees(math.acos(cos_ha))
    sunrise_min = 720 - 4 * (lon + ha) - eqtime  # minutes after UTC midnight
    sunset_min = 720 - 4 * (lon - ha) - eqtime
    midnight = datetime(on_date.year, on_date.month, on_date.day, tzinfo=_UTC)
    sunrise = (midnight + timedelta(minutes=sunrise_min)).astimezone(tz)
    sunset = (midnight + timedelta(minutes=sunset_min)).astimezone(tz)
    return sunrise, sunset
