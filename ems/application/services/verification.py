from __future__ import annotations

from datetime import UTC, datetime
from ems.application.context import ApplicationContext


class VerificationService:
    """Compare planned intent with the latest measurement (observational only)."""

    def __init__(self, context: ApplicationContext):
        self.context = context

    def verify(self, now: datetime | None = None) -> dict[str, object]:
        state = self.context.runtime_state
        now = now or datetime.now(UTC)
        pp = state["current_plan"]()
        sample = state["current_sample"](now)
        if pp is None:
            return {"status": "no_plan", "planned": None, "actual": None}
        _plan_now, _prices, plan = pp
        slot = plan.intent_at(now)
        actual = None if sample is None else {
            "soc_pct": sample.soc_pct,
            "battery_power_w": sample.battery_power_w,
            "grid_power_w": sample.grid_power_w,
            "observed_at": sample.ts.isoformat() if hasattr(sample, "ts") else now.isoformat(),
        }
        planned = None if slot is None else {
            "intent": slot.intent.value,
            "target_soc": slot.target_soc,
            "deadline": slot.deadline.isoformat() if slot.deadline else None,
            "reason": slot.reason,
        }
        status = "awaiting_measurement"
        if actual is not None:
            status = "observed"
            if (planned and planned["intent"] == "grid_charge_to_target"
                    and actual["battery_power_w"] > 50):
                status = "unexpected_discharge"
            elif (planned and planned["intent"] == "discharge_for_load"
                  and actual["battery_power_w"] < -50):
                status = "unexpected_charge"
        return {"status": status, "planned": planned, "actual": actual,
                "checked_at": now.isoformat()}
