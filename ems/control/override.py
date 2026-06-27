"""Manual operator override (SPEC §8.5/§13: "mode override" is a UI-editable runtime value).

An override forces ONE BatteryIntent for a bounded time, overruling the planner. When it expires
or is cleared, the system returns to following the plan (fail-safe: time-boxed so a forgotten
override can't strand the battery). Forcing ALLOW_SELF_CONSUMPTION is the "pause the EMS" action —
it hands control back to the battery's own vendor mode.

Pure data + (de)serialisation here; persistence lives in the runtime-state KV store and the
controller still owns the single battery write.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from ems.domain import BatteryIntent

# How long an override may last, in minutes (1 min .. 24 h). Server-clamped at the API.
MIN_MINUTES = 1
MAX_MINUTES = 24 * 60


@dataclass(frozen=True)
class Override:
    intent: BatteryIntent | None  # None => no override (follow the plan)
    expires_at: datetime | None  # tz-aware UTC expiry; None => no override

    @property
    def is_set(self) -> bool:
        return self.intent is not None and self.expires_at is not None

    def active(self, now: datetime) -> bool:
        """True only while set AND not yet expired (expiry is evaluated per call)."""
        return self.is_set and self.expires_at is not None and now < self.expires_at

    def seconds_remaining(self, now: datetime) -> int:
        # Real guard, not an `assert`: asserts are stripped under `python -O`/PYTHONOPTIMIZE
        # (common in prod Docker), and the fail-safe contract is "never crash the decision path".
        if not self.active(now) or self.expires_at is None:
            return 0
        return max(0, int((self.expires_at - now).total_seconds()))

    def to_dict(self, now: datetime) -> dict:
        return {
            "intent": self.intent.value if self.intent else None,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "active": self.active(now),
            "seconds_remaining": self.seconds_remaining(now),
        }


NONE = Override(intent=None, expires_at=None)


def from_stored(intent: str | None, expires_at: str | None) -> Override:
    """Rebuild an Override from persisted strings, tolerating bad/legacy values (-> NONE).

    A naive (tz-less) expiry is rejected: comparing it to a tz-aware `now` would raise, so a
    corrupt row must degrade to "no override" rather than crash the decision path (fail-safe).
    """
    if not intent or not expires_at:
        return NONE
    try:
        parsed_intent = BatteryIntent(intent)
        parsed_exp = datetime.fromisoformat(expires_at)
    except (ValueError, TypeError):
        return NONE
    if parsed_exp.tzinfo is None:
        return NONE
    return Override(intent=parsed_intent, expires_at=parsed_exp)
