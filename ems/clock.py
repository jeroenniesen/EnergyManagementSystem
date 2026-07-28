"""Small, injectable boundary around wall-clock time.

All values returned by this module are timezone-aware.  Keeping this boundary narrow makes
services deterministic in tests without changing the application's existing time semantics.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import UTC, datetime, tzinfo


def _require_aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("clock datetimes must be timezone-aware")
    return value


class Clock(ABC):
    """Source of the current instant, expressed in UTC."""

    @abstractmethod
    def now_utc(self) -> datetime:
        """Return the current timezone-aware instant in UTC."""

    def now_local(self, tz: tzinfo) -> datetime:
        """Return the current instant converted to ``tz``."""
        return self.now_utc().astimezone(tz)


class SystemClock(Clock):
    """Production clock backed by the system wall clock."""

    def now_utc(self) -> datetime:
        return datetime.now(UTC)


class FrozenClock(Clock):
    """Deterministic clock for tests and replay.

    The supplied instant must be timezone-aware; it is normalized to UTC once at
    construction so repeated reads return the exact same value.
    """

    def __init__(self, instant: datetime) -> None:
        self._instant = _require_aware(instant).astimezone(UTC)

    def now_utc(self) -> datetime:
        return self._instant
