from __future__ import annotations

from datetime import datetime

from ems.application.context import ApplicationContext, require_collaborator


class PlanService:
    """Build the read-only plan payload without depending on FastAPI."""

    def __init__(self, context: ApplicationContext):
        self.context = context

    def get_plan(
        self, requested_window: tuple[datetime, datetime] | None = None,
        settings: dict[str, object] | None = None,
        now: datetime | None = None,
    ) -> dict[str, object]:
        effective_now = now or self.context.clock.now_utc()
        current_plan = require_collaborator(self.context.current_plan, "current_plan")
        validate = require_collaborator(self.context.validate_plan, "validate_plan")
        policy = require_collaborator(self.context.policy, "policy")
        warnings = require_collaborator(self.context.tariff_warnings, "tariff_warnings")
        pp = current_plan(effective_now)
        cfg = settings if settings is not None else self.context.settings
        if pp is None:
            return {"created_at": None, "current_intent": None, "current_reason": None, "slots": []}
        _plan_now, _prices, plan = pp
        cur = plan.intent_at(effective_now)
        return {
            "created_at": plan.created_at.isoformat(),
            "strategy": plan.strategy,
            "target_soc": plan.target_soc,
            "deadline": plan.deadline.isoformat() if plan.deadline else None,
            "current_intent": cur.intent if cur else None,
            "current_reason": cur.reason if cur else None,
            "validation": validate(plan, effective_now).to_dict(),
            "tariff_policy": policy(cfg),
            "tariff_warnings": warnings(cfg),
            "slots": [{
                "start": s.start.isoformat(), "intent": s.intent, "reason": s.reason,
                "target_soc": s.target_soc, "target_kwh": s.target_kwh, "power_w": s.power_w,
                "floor_soc": s.floor_soc,
                "deadline": s.deadline.isoformat() if s.deadline else None,
            } for s in plan.slots],
        }
