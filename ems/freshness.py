"""Per-signal data freshness (SPEC §4.7). Each signal is tracked independently —
there is no single global 'stale' flag. All times are tz-aware datetimes."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from ems.timeutil import require_aware

# Default meter window (SPEC sense cycle ~5 min → 10 min stale). Prices/forecast update far less
# often: a days-old Solcast cache must not read as live (issue #79).
DEFAULT_STALE_AFTER_S = 600.0
SIGNAL_STALE_AFTER_S: dict[str, float] = {
    "grid": 600.0,
    "solar": 600.0,
    "ev": 600.0,
    "battery": 600.0,
    "soc": 600.0,
    "prices": 6 * 3600.0,       # day-ahead; last-good after Tibber outage ages out in hours
    "forecast": 24 * 3600.0,    # Solcast daylight refreshes; >1 day = not live
}


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

    stale_after_s: float = DEFAULT_STALE_AFTER_S
    _expected: set[str] = field(default_factory=set)
    _last: dict[str, datetime] = field(default_factory=dict)
    _stale_after: dict[str, float] = field(default_factory=dict)

    def register(self, *signals: str) -> None:
        self._expected.update(signals)
        for sig in signals:
            if sig in SIGNAL_STALE_AFTER_S and sig not in self._stale_after:
                self._stale_after[sig] = SIGNAL_STALE_AFTER_S[sig]

    def set_stale_after(self, signal: str, stale_after_s: float) -> None:
        self._stale_after[signal] = float(stale_after_s)

    def _threshold(self, signal: str) -> float:
        return self._stale_after.get(signal, SIGNAL_STALE_AFTER_S.get(signal, self.stale_after_s))

    def mark(self, signal: str, ts: datetime) -> None:
        require_aware(ts, "ts")
        self._last[signal] = ts

    def last_update(self, signal: str) -> datetime | None:
        return self._last.get(signal)

    def state(self, signal: str, now: datetime) -> Freshness:
        return classify(self._last.get(signal), now, self._threshold(signal))

    def age_seconds(self, signal: str, now: datetime) -> float | None:
        require_aware(now, "now")
        ts = self._last.get(signal)
        return None if ts is None else max(0.0, (now - ts).total_seconds())

    def snapshot(self, now: datetime) -> dict[str, str]:
        signals = self._expected | self._last.keys()
        return {sig: self.state(sig, now).value for sig in sorted(signals)}

    def detail_snapshot(self, now: datetime) -> dict[str, dict]:
        """Per-signal state + age + last-update timestamp (for device-health / UI hh:mm)."""
        signals = self._expected | self._last.keys()
        out: dict[str, dict] = {}
        for sig in sorted(signals):
            ts = self._last.get(sig)
            out[sig] = {
                "state": self.state(sig, now).value,
                "updated_at": ts,
                "age_seconds": None if ts is None else max(0.0, (now - ts).total_seconds()),
            }
        return out
