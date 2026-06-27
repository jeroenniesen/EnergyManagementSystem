"""Per-signal data freshness (SPEC §4.7). Each signal is tracked independently —
there is no single global 'stale' flag. All times are tz-aware datetimes."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from ems.timeutil import require_aware


class Freshness(StrEnum):
    FRESH = "fresh"
    STALE = "stale"
    MISSING = "missing"


def classify(last_update: datetime | None, now: datetime, stale_after_s: float) -> Freshness:
    """MISSING if never seen; STALE if older than `stale_after_s`; else FRESH.

    A reading timestamped slightly in the future (clock skew) is treated as fresh
    (age is clamped to 0).
    """
    require_aware(now, "now")
    if last_update is None:
        return Freshness.MISSING
    require_aware(last_update, "last_update")
    age = max(0.0, (now - last_update).total_seconds())
    return Freshness.STALE if age > stale_after_s else Freshness.FRESH


@dataclass
class FreshnessTracker:
    """Records the last-update time per signal and reports each signal's freshness.

    Signals registered up front (or ever marked) are ALL included in `snapshot()`,
    so a source that has never reported surfaces as MISSING rather than being omitted.
    """

    stale_after_s: float = 600.0
    _expected: set[str] = field(default_factory=set)
    _last: dict[str, datetime] = field(default_factory=dict)

    def register(self, *signals: str) -> None:
        self._expected.update(signals)

    def mark(self, signal: str, ts: datetime) -> None:
        require_aware(ts, "ts")
        self._last[signal] = ts

    def state(self, signal: str, now: datetime) -> Freshness:
        return classify(self._last.get(signal), now, self.stale_after_s)

    def age_seconds(self, signal: str, now: datetime) -> float | None:
        require_aware(now, "now")
        ts = self._last.get(signal)
        return None if ts is None else max(0.0, (now - ts).total_seconds())

    def snapshot(self, now: datetime) -> dict[str, str]:
        signals = self._expected | self._last.keys()
        return {sig: self.state(sig, now).value for sig in sorted(signals)}
