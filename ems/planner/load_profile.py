"""Expected house-load profile (SPEC §8.5 inputs).

To predict how the battery will behave over the next 24h we need an expected house load per slot.
We learn it from recent history: the mean reconstructed `house_load_w` bucketed by hour-of-day in
the site timezone. A well-sampled hour uses its own mean (e.g. "your 19:00 load averages 850 W");
a sparse hour falls back to the overall mean; with no history at all we use a caller-supplied
constant. Pure + unit-tested — the API passes in recorded rows.
"""
from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from dataclasses import field as data_field
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from ems.load_model import MAX_LEARNABLE_LOAD_W


@dataclass(frozen=True)
class LoadProfile:
    """A learned hour-of-day load profile. `expected_w(dt)` is the predicted house load (W) for the
    local hour of `dt`: the learned mean for a well-sampled hour, else a realistic typical-day
    shape (NOT a flat constant — a momentary high draw must not be projected across 24h)."""

    by_hour: dict[int, float]  # local hour -> mean load (only well-sampled hours)
    tz: ZoneInfo
    by_day_type: dict[tuple[bool, int], float] = data_field(default_factory=dict)
    uncertainty: dict[tuple[bool, int], float] = data_field(default_factory=dict)

    def expected_w(self, when: datetime) -> float:
        local = when.astimezone(self.tz)
        return self.by_day_type.get(
            (local.weekday() >= 5, local.hour),
            self.by_hour.get(local.hour, _typical_w(local.hour)),
        )

    def band_w(self, when: datetime) -> tuple[float, float]:
        """Empirical spread, not a calibrated probability of coverage."""
        local = when.astimezone(self.tz)
        expected = self.expected_w(when)
        spread = self.uncertainty.get((local.weekday() >= 5, local.hour), expected * 0.5)
        return max(0.0, expected - spread), expected + spread


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
    (`fallback_w` is accepted for backward compatibility but superseded by the shaped default.)"""
    if enhanced and as_of is None:
        raise ValueError("enhanced load profiles need an explicit as_of time")
    buckets: dict[int, list[float]] = defaultdict(list)
    daily: dict[tuple, list[float]] = defaultdict(list)
    for row in rows:
        ts, load = row.get("ts"), row.get(field)
        if not isinstance(ts, str) or load is None:
            continue
        try:
            dt = datetime.fromisoformat(ts)
            value = float(load)
        except (ValueError, TypeError):
            continue
        if not math.isfinite(value) or value < 0.0 or value > MAX_LEARNABLE_LOAD_W:
            continue
        if dt.tzinfo is None:  # naive timestamps are UTC (the recorder writes aware-UTC)
            dt = dt.replace(tzinfo=UTC)
        if as_of is not None and dt >= as_of:
            continue
        if enhanced and dt < as_of - timedelta(days=42):
            continue
        buckets[dt.astimezone(tz).hour].append(value)
        local = dt.astimezone(tz)
        daily[(local.date(), local.hour)].append(value)

    by_hour = {h: sum(v) / len(v) for h, v in buckets.items() if len(v) >= min_samples}
    if not enhanced:
        return LoadProfile(by_hour=by_hour, tz=tz)
    groups: dict[tuple[bool, int], list[tuple[float, float]]] = defaultdict(list)
    hours: dict[int, list[tuple[float, float]]] = defaultdict(list)
    for (day, hour), values in daily.items():
        age = (as_of.astimezone(tz).date() - day).days
        item = (sum(values) / len(values), 2 ** (-age / 14.0))
        groups[(day.weekday() >= 5, hour)].append(item)
        hours[hour].append(item)

    def mean(items: list[tuple[float, float]]) -> float:
        return sum(v * w for v, w in items) / sum(w for _, w in items)

    by_hour = {hour: mean(items) for hour, items in hours.items() if len(items) >= 3}
    by_type = {key: mean(items) for key, items in groups.items() if len(items) >= 3}
    uncertainty = {
        key: max(50.0, math.sqrt(sum(w * (v - by_type[key]) ** 2 for v, w in items)
                                / sum(w for _, w in items)))
        for key, items in groups.items() if key in by_type
    }
    return LoadProfile(by_hour, tz, by_type, uncertainty)
