"""The Plan domain object (SPEC §8.6/§13.2): an ordered list of BatteryIntent slots, each with
a human-readable reason, plus 'intent at time t' lookup for the control loop.

The plan is the **control payload**, so a slot carries not just the *mode* (intent) but the energy
contract the controller/driver needs: how full to get (`target_soc`), how much energy that means
(`target_kwh`), at what power (`power_w`), the reserve it must not cross (`floor_soc`), and by when
(`deadline`). These are optional — a planner populates what it knows; the validator (SPEC §8.11)
rejects a charge/discharge slot that lacks the target it needs before any live write. This is the
abstraction the energy-expert review (#1/#2) called the central gap: "mode plus exact target,
amount, deadline, and validation", not mode alone.
"""
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
    # --- energy contract (optional; populated by the planner where known) ---
    target_soc: float | None = None   # % SoC to reach by `deadline` — the AUTHORITATIVE stop for a
    #                                   charge/discharge slot (the driver charges toward this, never
    #                                   to a default 100). Set on non-charge slots too as the plan's
    #                                   informational goal — NOT a command on those.
    target_kwh: float | None = None   # informational: nominal energy this slot moves at power_w
    #                                   (per-slot capacity, not the remaining shortfall)
    power_w: float | None = None      # requested charge/discharge power for this slot
    floor_soc: float | None = None    # reserve floor the plan must not discharge below
    deadline: datetime | None = None  # by when `target_soc` must be met (sunset / first peak)
    end: datetime | None = None       # explicit slot end; defaults to start + SLOT

    @property
    def slot_end(self) -> datetime:
        return self.end if self.end is not None else self.start + SLOT


@dataclass(frozen=True)
class Plan:
    created_at: datetime
    slots: tuple[PlanSlot, ...]
    strategy: str | None = None       # 'summer' | 'winter' — which planner produced it
    target_soc: float | None = None   # plan-level night-carry / arbitrage target SoC, if known
    deadline: datetime | None = None  # plan-level charge deadline (sunset / first peak), if known

    def intent_at(self, now: datetime) -> PlanSlot | None:
        """The slot covering `now`, or None if `now` is outside the plan horizon. Uses each slot's
        own end (`slot_end`) so variable-length slots resolve correctly, not a fixed 15-min step."""
        for s in self.slots:
            if s.start <= now < s.slot_end:
                return s
        return None
