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
    local hour of `dt`."""

    by_hour: dict[int, float]  # local hour -> mean house_load_w (only well-sampled hours)
    fallback_w: float  # overall mean, or the caller constant when there is no history
    tz: ZoneInfo

    def expected_w(self, when: datetime) -> float:
        local_hour = when.astimezone(self.tz).hour
        return self.by_hour.get(local_hour, self.fallback_w)


def build_load_profile(
    rows: list[dict], tz: ZoneInfo, *, fallback_w: float, min_samples: int = 3
) -> LoadProfile:
    """Learn an hourly load profile from history rows ({"ts": ISO, "house_load_w": float}).

    Rows that don't parse or lack a load are skipped. An hour with fewer than `min_samples` valid
    readings falls back to the overall mean (still data-driven); `fallback_w` is used only when
    there is no usable history at all."""
    buckets: dict[int, list[float]] = defaultdict(list)
    all_loads: list[float] = []
    for row in rows:
        ts, load = row.get("ts"), row.get("house_load_w")
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
        all_loads.append(value)

    overall = sum(all_loads) / len(all_loads) if all_loads else fallback_w
    by_hour = {h: sum(v) / len(v) for h, v in buckets.items() if len(v) >= min_samples}
    return LoadProfile(by_hour=by_hour, fallback_w=overall, tz=tz)
