"""Pure timestamp-aware integration primitives for recorded power samples."""
from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta


@dataclass(frozen=True)
class ObservedSegment:
    observed_at: datetime
    start: datetime
    end: datetime
    values: dict[str, float]

    @property
    def duration_seconds(self) -> float:
        return (self.end - self.start).total_seconds()


def _parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _next_quarter(when: datetime) -> datetime:
    minute = (when.minute // 15 + 1) * 15
    return when.replace(minute=0, second=0, microsecond=0) + timedelta(minutes=minute)


def observed_segments(
    rows: list[dict],
    *,
    start: datetime,
    end: datetime,
    fields: tuple[str, ...],
    timestamp_field: str = "ts",
    nominal_interval_seconds: float = 300.0,
    max_hold_seconds: float | None = None,
) -> list[ObservedSegment]:
    """Return bounded zero-order-hold segments split at UTC quarter-hour boundaries.

    Duplicate timestamps are averaged and input order is irrelevant. A sample owns time until the
    next distinct sample, bounded by ``max_hold_seconds``; the final sample owns one nominal
    interval. No time before the first valid sample is invented.
    """
    start_utc, end_utc = start.astimezone(UTC), end.astimezone(UTC)
    if end_utc <= start_utc or nominal_interval_seconds <= 0:
        return []
    max_hold = nominal_interval_seconds if max_hold_seconds is None else max_hold_seconds
    if max_hold <= 0:
        return []

    grouped: dict[datetime, dict[str, list[float]]] = defaultdict(
        lambda: {field: [] for field in fields}
    )
    for row in rows:
        ts = _parse_timestamp(row.get(timestamp_field))
        if ts is None or ts < start_utc or ts >= end_utc:
            continue
        values: dict[str, float] = {}
        try:
            for field in fields:
                value = float(row[field])
                if not math.isfinite(value):
                    raise ValueError
                values[field] = value
        except (KeyError, TypeError, ValueError):
            continue
        for field, value in values.items():
            grouped[ts][field].append(value)

    timestamps = sorted(grouped)
    out: list[ObservedSegment] = []
    for index, ts in enumerate(timestamps):
        natural_end = (
            timestamps[index + 1]
            if index + 1 < len(timestamps)
            else ts + timedelta(seconds=nominal_interval_seconds)
        )
        interval_end = min(natural_end, ts + timedelta(seconds=max_hold), end_utc)
        if interval_end <= ts:
            continue
        values = {field: sum(items) / len(items) for field, items in grouped[ts].items()}
        cursor = ts
        while cursor < interval_end:
            segment_end = min(interval_end, _next_quarter(cursor))
            out.append(ObservedSegment(ts, cursor, segment_end, values))
            cursor = segment_end
    return out
