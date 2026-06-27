"""Data-quality fail-safe gate (SPEC §8.11; CLAUDE.md "fail safe").

When data quality is `unsafe` — a critical signal (grid/SoC) is stale or missing, so we can't
safely reconstruct load or trust SoC — the EMS must fall back to the battery's own
self-consumption (AUTO / ALLOW_SELF_CONSUMPTION) rather than acting on a plan built from bad data.
"The system must never be worse than no EMS."

This gates the PLANNER-derived intent. An explicit, time-boxed manual override is a deliberate
operator action (they can see the data-quality badge) and is handled separately by the caller.
Pure + unit-testable.
"""
from __future__ import annotations

from ems.domain import BatteryIntent

_UNSAFE = "unsafe"
_FAILSAFE_REASON = "data quality unsafe — holding self-consumption (fail-safe)"


def failsafe_intent(intent: BatteryIntent, data_quality: str) -> tuple[BatteryIntent, str | None]:
    """Return (intent, reason). When data quality is unsafe and the intent would do anything other
    than self-consumption, force ALLOW_SELF_CONSUMPTION and explain why; otherwise pass through
    with reason None."""
    if data_quality == _UNSAFE and intent is not BatteryIntent.ALLOW_SELF_CONSUMPTION:
        return BatteryIntent.ALLOW_SELF_CONSUMPTION, _FAILSAFE_REASON
    return intent, None
