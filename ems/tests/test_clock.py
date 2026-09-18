from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from ems.application.context import ApplicationContext
from ems.clock import FrozenClock, SystemClock


def test_system_clock_returns_aware_utc() -> None:
    now = SystemClock().now_utc()
    assert now.tzinfo is UTC
    assert now.utcoffset() is not None


def test_clock_converts_to_local_timezone() -> None:
    clock = FrozenClock(datetime(2026, 1, 15, 12, tzinfo=UTC))
    assert clock.now_local(ZoneInfo("Europe/Amsterdam")).hour == 13


def test_frozen_clock_preserves_exact_instant_as_utc() -> None:
    instant = datetime(2026, 7, 28, 18, 30, tzinfo=ZoneInfo("Europe/Amsterdam"))
    clock = FrozenClock(instant)
    assert clock.now_utc() == instant.astimezone(UTC)
    assert clock.now_utc() is clock.now_utc()


def test_frozen_clock_rejects_naive_datetime() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        FrozenClock(datetime(2026, 7, 28, 18, 30))


def test_application_context_defaults_to_system_clock() -> None:
    context = ApplicationContext(source=object())
    assert isinstance(context.clock, SystemClock)

