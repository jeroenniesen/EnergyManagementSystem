import asyncio
from datetime import UTC, datetime, timedelta

from ems.application.context import ApplicationContext
from ems.application.services import (
    DiagnosticsService,
    PlanService,
    ReportService,
    VerificationService,
)
from ems.clock import FrozenClock
from ems.domain import BatteryIntent, RawSample
from ems.planner.schedule import Plan, PlanSlot
from ems.planner.validator import PlanValidation

NOW = datetime(2026, 7, 28, 12, tzinfo=UTC)


def test_plan_provider_and_validation_share_explicit_now():
    seen = []
    plan = Plan(created_at=NOW, slots=(PlanSlot(NOW, BatteryIntent.HOLD_RESERVE, "hold"),))

    def current(now=None):
        seen.append(("plan", now))
        return now, [], plan

    def validate(plan, now):
        seen.append(("validate", now))
        return PlanValidation("valid")

    ctx = ApplicationContext(source=None, clock=FrozenClock(NOW + timedelta(days=1)),
                             current_plan=current, validate_plan=validate,
                             policy=lambda settings: {}, tariff_warnings=lambda settings: [])
    result = PlanService(ctx).get_plan(now=NOW)
    assert result["current_intent"] == BatteryIntent.HOLD_RESERVE
    assert seen == [("plan", NOW), ("validate", NOW)]


def test_verification_provider_and_sample_share_clock_instant():
    seen = []
    plan = Plan(created_at=NOW, slots=(PlanSlot(NOW, BatteryIntent.DISCHARGE_FOR_LOAD, "serve"),))

    def current(now=None):
        seen.append(now)
        return now, [], plan

    def sample(now):
        seen.append(now)
        return RawSample(0, 0, 1000, 0, 50)

    ctx = ApplicationContext(source=None, clock=FrozenClock(NOW),
                             current_plan=current, current_sample=sample)
    result = VerificationService(ctx).verify()
    assert seen == [NOW, NOW]
    assert result["status"] == "observed"
    assert result["checked_at"] == NOW.isoformat()


def test_diagnostics_and_savings_callbacks_receive_context_clock():
    seen = []

    async def diagnostics(now):
        seen.append(now)
        return {"overall": "ok"}

    def savings(now):
        seen.append(now)
        return {"today_eur": .2}

    ctx = ApplicationContext(source=None, clock=FrozenClock(NOW),
                             diagnostics_snapshot=diagnostics, savings_snapshot=savings)
    assert asyncio.run(DiagnosticsService(ctx).get_snapshot()) == {"overall": "ok"}
    assert ReportService(ctx).savings() == {"today_eur": .2}
    assert seen == [NOW, NOW]
