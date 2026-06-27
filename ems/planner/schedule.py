"""The Plan domain object (SPEC §8.6/§13.2): an ordered list of BatteryIntent slots, each with
a human-readable reason, plus 'intent at time t' lookup for the control loop."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from ems.domain import BatteryIntent

SLOT = timedelta(minutes=15)


@dataclass(frozen=True)
class PlanSlot:
    start: datetime  # tz-aware
    intent: BatteryIntent
    reason: str


@dataclass(frozen=True)
class Plan:
    created_at: datetime
    slots: tuple[PlanSlot, ...]

    def intent_at(self, now: datetime) -> PlanSlot | None:
        """The slot covering `now`, or None if `now` is outside the plan horizon."""
        for s in self.slots:
            if s.start <= now < s.start + SLOT:
                return s
        return None
