"""Per-signal data freshness (SPEC §4.7). Each signal is tracked independently —
there is no single global 'stale' flag. All times are tz-aware datetimes."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


class Freshness(StrEnum):
    FRESH = "fresh"
    STALE = "stale"
    MISSING = "missing"


def classify(last_update: datetime | None, now: datetime, stale_after_s: float) -> Freshness:
    """MISSING if never seen; STALE if older than `stale_after_s`; else FRESH.

    A reading timestamped slightly in the future (clock skew) is treated as fresh
    (age is clamped to 0).
    """
    if last_update is None:
        return Freshness.MISSING
    age = max(0.0, (now - last_update).total_seconds())
    return Freshness.STALE if age > stale_after_s else Freshness.FRESH


@dataclass
class FreshnessTracker:
    """Records the last-update time per signal and reports each signal's freshness."""

    stale_after_s: float = 600.0
    _last: dict[str, datetime] = field(default_factory=dict)

    def mark(self, signal: str, ts: datetime) -> None:
        self._last[signal] = ts

    def state(self, signal: str, now: datetime) -> Freshness:
        return classify(self._last.get(signal), now, self.stale_after_s)

    def age_seconds(self, signal: str, now: datetime) -> float | None:
        ts = self._last.get(signal)
        return None if ts is None else max(0.0, (now - ts).total_seconds())

    def snapshot(self, now: datetime) -> dict[str, str]:
        return {sig: self.state(sig, now).value for sig in self._last}
