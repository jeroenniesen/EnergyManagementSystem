"""Expected house-load profile (SPEC §8.5 inputs).

To predict how the battery will behave over the next 24h we need an expected house load per slot.
We learn it from recent history: the mean reconstructed `house_load_w` bucketed by hour-of-day in
the site timezone. A well-sampled hour uses its own mean (e.g. "your 19:00 load averages 850 W");
a sparse hour falls back to the overall mean; with no history at all we use a caller-supplied
constant. Pure + unit-tested — the API passes in recorded rows.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class LoadProfile:
    """A learned hour-of-day load profile. `expected_w(dt)` is the predicted house load (W) for the
    local hour of `dt`: the learned mean for a well-sampled hour, else a realistic typical-day
    shape (NOT a flat constant — a momentary high draw must not be projected across 24h)."""

    by_hour: dict[int, float]  # local hour -> mean load (only well-sampled hours)
    tz: ZoneInfo

    def expected_w(self, when: datetime) -> float:
        local_hour = when.astimezone(self.tz).hour
        return self.by_hour.get(local_hour, _typical_w(local_hour))


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
    buckets: dict[int, list[float]] = defaultdict(list)
    for row in rows:
        ts, load = row.get("ts"), row.get(field)
        if not isinstance(ts, str) or load is None:
            continue
        try:
            dt = datetime.fromisoformat(ts)
            value = float(load)
        except (ValueError, TypeError):
            continue
        if dt.tzinfo is None:  # naive timestamps are UTC (the recorder writes aware-UTC)
            dt = dt.replace(tzinfo=UTC)
        buckets[dt.astimezone(tz).hour].append(value)

    by_hour = {h: sum(v) / len(v) for h, v in buckets.items() if len(v) >= min_samples}
    return LoadProfile(by_hour=by_hour, tz=tz)
