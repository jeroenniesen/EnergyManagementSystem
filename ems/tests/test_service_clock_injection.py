from datetime import UTC, datetime

from ems.application.context import ApplicationContext
from ems.application.services.verification import VerificationService
from ems.clock import FrozenClock


def test_verification_uses_context_clock_when_now_omitted():
    frozen = datetime(2026, 7, 28, 12, 34, 56, tzinfo=UTC)
    seen = []

    def sample(now):
        seen.append(now)
        return None

    ctx = ApplicationContext(
        source=None,
        clock=FrozenClock(frozen),
        runtime_state={"current_plan": lambda: None, "current_sample": sample},
    )

    result = VerificationService(ctx).verify()

    assert result["status"] == "no_plan"
    assert seen == [frozen]
