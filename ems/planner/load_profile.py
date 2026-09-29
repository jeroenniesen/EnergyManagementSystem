"""Expected house-load profile (SPEC §8.5 inputs).

To predict how the battery will behave over the next 24h we need an expected house load per slot.
We learn it from recent history: the mean reconstructed `house_load_w` bucketed by hour-of-day in
the site timezone. A well-sampled hour uses its own mean (e.g. "your 19:00 load averages 850 W");
a sparse hour falls back to the overall mean; with no history at all we use a caller-supplied
constant. Pure + unit-tested — the API passes in recorded rows.

Enhanced profiles (B-64 / #81, adapted from draft PR #65) add weekday/weekend, meteorological
season, and a daytime-solar weather proxy with recency weighting — and never train on future rows.
"""
from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from dataclasses import field as data_field
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from ems.load_model import MAX_LEARNABLE_LOAD_W

# Daytime window used only to bin "weather" from recorded solar (clear vs dull days). Pure proxy —
# we do not fetch Open-Meteo history; solar already sits next to load in raw samples.
_WEATHER_DAYTIME_HOURS = range(8, 17)
_WEATHER_BINS = ("dim", "mixed", "bright")


def meteorological_season(month: int) -> str:
    """Meteorological season for a calendar month (1–12)."""
    if month in (12, 1, 2):
        return "winter"
    if month in (3, 4, 5):
        return "spring"
    if month in (6, 7, 8):
        return "summer"
    return "autumn"


def weather_bin_from_solar(daytime_mean_w: float, thresholds: tuple[float, float]) -> str:
    """Map a daytime solar mean onto dim/mixed/bright using training-window tercile cuts."""
    low, high = thresholds
    if daytime_mean_w <= low:
        return "dim"
    if daytime_mean_w >= high:
        return "bright"
    return "mixed"


def _typical_w(hour: int) -> float:
    """A realistic NL non-EV household shape (~11 kWh/day) for hours we haven't learned yet — low
    overnight, moderate by day, an evening peak. Keeps the daytime baseline BELOW typical solar so
    a sunny midday shows a surplus that charges the battery (rather than a flat high mean blocking
    it). Learned hourly means override this as real history accrues."""
    if 17 <= hour < 22:
        return 900.0  # evening peak
    if 7 <= hour < 9:
        return 600.0  # morning
    if 9 <= hour < 17:
        return 400.0  # daytime base
    return 250.0  # overnight


@dataclass(frozen=True)
class LoadProfile:
    """A learned hour-of-day load profile. `expected_w(dt)` is the predicted house load (W) for the
    local hour of `dt`: the learned mean for a well-sampled hour, else a realistic typical-day
    shape (NOT a flat constant — a momentary high draw must not be projected across 24h).

    Enhanced fields (optional, empty on the legacy hour-only path) cascade:
    weather+season+weekend → season+weekend → weekend → hour → typical shape.
    """

    by_hour: dict[int, float]  # local hour -> mean load (only well-sampled hours)
    tz: ZoneInfo
    by_day_type: dict[tuple[bool, int], float] = data_field(default_factory=dict)
    by_season_day: dict[tuple[str, bool, int], float] = data_field(default_factory=dict)
    by_weather: dict[tuple[str, str, bool, int], float] = data_field(default_factory=dict)
    uncertainty: dict[tuple[bool, int], float] = data_field(default_factory=dict)
    weather_thresholds: tuple[float, float] | None = None
    features: tuple[str, ...] = ("hour",)

    def expected_w(
        self,
        when: datetime,
        *,
        weather_bin: str | None = None,
        daytime_solar_w: float | None = None,
    ) -> float:
        local = when.astimezone(self.tz)
        weekend = local.weekday() >= 5
        hour = local.hour
        season = meteorological_season(local.month)
        bin_ = weather_bin
        if bin_ is None and daytime_solar_w is not None and self.weather_thresholds is not None:
            bin_ = weather_bin_from_solar(daytime_solar_w, self.weather_thresholds)
        if bin_ is not None and bin_ in _WEATHER_BINS:
            hit = self.by_weather.get((season, bin_, weekend, hour))
            if hit is not None:
                return hit
        hit = self.by_season_day.get((season, weekend, hour))
        if hit is not None:
            return hit
        return self.by_day_type.get(
            (weekend, hour),
            self.by_hour.get(hour, _typical_w(hour)),
        )

    def band_w(
        self,
        when: datetime,
        *,
        weather_bin: str | None = None,
        daytime_solar_w: float | None = None,
    ) -> tuple[float, float]:
        """Empirical spread around the expected load — not a calibrated coverage probability."""
        local = when.astimezone(self.tz)
        expected = self.expected_w(
            when, weather_bin=weather_bin, daytime_solar_w=daytime_solar_w,
        )
        spread = self.uncertainty.get((local.weekday() >= 5, local.hour), expected * 0.5)
        return max(0.0, expected - spread), expected + spread


def _parse_row(
    row: dict, field: str, *, as_of: datetime | None, tz: ZoneInfo,
) -> tuple[datetime, float, float | None] | None:
    ts, load = row.get("ts"), row.get(field)
    if not isinstance(ts, str) or load is None:
        return None
    try:
        dt = datetime.fromisoformat(ts)
        value = float(load)
    except (ValueError, TypeError):
        return None
    if not math.isfinite(value) or value < 0.0 or value > MAX_LEARNABLE_LOAD_W:
        return None
    if dt.tzinfo is None:  # naive timestamps are UTC (the recorder writes aware-UTC)
        dt = dt.replace(tzinfo=UTC)
    if as_of is not None and dt >= as_of:
        return None
    solar = row.get("solar_power_w")
    solar_w: float | None
    try:
        solar_w = float(solar) if solar is not None else None
        if solar_w is not None and (not math.isfinite(solar_w) or solar_w < 0.0):
            solar_w = None
    except (TypeError, ValueError):
        solar_w = None
    return dt, value, solar_w


def _weighted_mean(items: list[tuple[float, float]]) -> float:
    return sum(v * w for v, w in items) / sum(w for _, w in items)


def _weather_thresholds(day_solar: dict[date, list[float]]) -> tuple[float, float] | None:
    means = sorted(
        sum(vals) / len(vals) for vals in day_solar.values() if vals
    )
    if len(means) < 3:
        return None
    n = len(means)
    return means[max(0, n // 3 - 1)], means[min(n - 1, (2 * n) // 3)]


def build_load_profile(
    rows: list[dict], tz: ZoneInfo, *, fallback_w: float | None = None, min_samples: int = 3,
    field: str = "non_ev_load_w",
    enhanced: bool = False,
    as_of: datetime | None = None,
) -> LoadProfile:
    """Learn an hourly load profile from history rows ({"ts": ISO, <field>: float}).

    `field` defaults to `non_ev_load_w` — the house load EXCLUDING EV charging (SPEC §4.5): the
    battery offsets the baseline house, not the intermittent ~10 kW car charge, so the projection
    must not assume the Tesla is plugged in all day.

    Rows that don't parse or lack a load are skipped. Only hours with >= `min_samples` readings are
    learned; every other hour uses the realistic typical-day shape (`_typical_w`) — so a cold start
    (a handful of samples taken during one high-draw burst) is NOT projected as a flat high load all
    day, which would wrongly hide the daytime solar surplus and stop the battery charging.
    (`fallback_w` is accepted for backward compatibility but superseded by the shaped default.)

    When `enhanced=True` (requires `as_of`), also learn weekday/weekend, meteorological season, and
    an optional weather proxy from `solar_power_w` on the same rows (B-64). Training never sees
    rows at or after `as_of`, and enhanced history is capped to the prior 42 days (PR #65).
    """
    if enhanced and as_of is None:
        raise ValueError("enhanced load profiles need an explicit as_of time")
    buckets: dict[int, list[float]] = defaultdict(list)
    daily: dict[tuple[date, int], list[float]] = defaultdict(list)
    day_solar: dict[date, list[float]] = defaultdict(list)
    for row in rows:
        parsed = _parse_row(row, field, as_of=as_of, tz=tz)
        if parsed is None:
            continue
        dt, value, solar_w = parsed
        if enhanced and as_of is not None and dt < as_of - timedelta(days=42):
            continue
        local = dt.astimezone(tz)
        buckets[local.hour].append(value)
        daily[(local.date(), local.hour)].append(value)
        if solar_w is not None and local.hour in _WEATHER_DAYTIME_HOURS:
            day_solar[local.date()].append(solar_w)

    by_hour = {h: sum(v) / len(v) for h, v in buckets.items() if len(v) >= min_samples}
    if not enhanced:
        return LoadProfile(by_hour=by_hour, tz=tz, features=("hour",))

    assert as_of is not None  # guarded above
    as_of_local = as_of.astimezone(tz)
    thresholds = _weather_thresholds(day_solar)
    day_weather: dict[date, str] = {}
    if thresholds is not None:
        for day, vals in day_solar.items():
            day_weather[day] = weather_bin_from_solar(sum(vals) / len(vals), thresholds)

    groups: dict[tuple[bool, int], list[tuple[float, float]]] = defaultdict(list)
    season_groups: dict[tuple[str, bool, int], list[tuple[float, float]]] = defaultdict(list)
    weather_groups: dict[tuple[str, str, bool, int], list[tuple[float, float]]] = defaultdict(list)
    hours: dict[int, list[tuple[float, float]]] = defaultdict(list)
    for (day, hour), values in daily.items():
        age = (as_of_local.date() - day).days
        item = (sum(values) / len(values), 2 ** (-age / 14.0))
        weekend = day.weekday() >= 5
        season = meteorological_season(day.month)
        groups[(weekend, hour)].append(item)
        season_groups[(season, weekend, hour)].append(item)
        hours[hour].append(item)
        weather = day_weather.get(day)
        if weather is not None:
            weather_groups[(season, weather, weekend, hour)].append(item)

    by_hour = {hour: _weighted_mean(items) for hour, items in hours.items() if len(items) >= 3}
    by_type = {key: _weighted_mean(items) for key, items in groups.items() if len(items) >= 3}
    by_season = {
        key: _weighted_mean(items) for key, items in season_groups.items() if len(items) >= 3
    }
    by_weather = {
        key: _weighted_mean(items) for key, items in weather_groups.items() if len(items) >= 3
    }
    uncertainty = {
        key: max(50.0, math.sqrt(
            sum(w * (v - by_type[key]) ** 2 for v, w in items) / sum(w for _, w in items)
        ))
        for key, items in groups.items() if key in by_type
    }
    features = ["hour", "weekday_weekend", "season"]
    if by_weather:
        features.append("weather")
    return LoadProfile(
        by_hour=by_hour,
        tz=tz,
        by_day_type=by_type,
        by_season_day=by_season,
        by_weather=by_weather,
        uncertainty=uncertainty,
        weather_thresholds=thresholds,
        features=tuple(features),
    )
