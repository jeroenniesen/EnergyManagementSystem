from datetime import UTC, datetime, timedelta

import pytest

from ems.lifecycle import Lifecycle, OwnershipState

T0 = datetime(2026, 6, 27, 10, 0, tzinfo=UTC)


def _ready(lc: Lifecycle):
    lc.mark_sensors_validated()
    lc.mark_probe_ok()
    lc.mark_plan_loaded()


def test_starts_inactive_then_observing():
    lc = Lifecycle(dry_run=True)
    assert lc.state is OwnershipState.INACTIVE
    lc.start(T0)
    assert lc.state is OwnershipState.OBSERVING


def test_stays_observing_during_grace_even_when_ready():
    lc = Lifecycle(dry_run=False, startup_grace_seconds=120)
    lc.start(T0)
    _ready(lc)
    lc.tick(T0 + timedelta(seconds=60))  # still inside grace
    assert lc.state is OwnershipState.OBSERVING
    assert lc.can_command(T0 + timedelta(seconds=60)) is False


def test_dry_run_advances_to_dry_run_never_commands():
    lc = Lifecycle(dry_run=True, startup_grace_seconds=120)
    lc.start(T0)
    _ready(lc)
    lc.tick(T0 + timedelta(seconds=121))
    assert lc.state is OwnershipState.DRY_RUN
    assert lc.can_command(T0 + timedelta(seconds=121)) is False


def test_controlling_can_command_after_grace_and_checks():
    lc = Lifecycle(dry_run=False, startup_grace_seconds=120)
    lc.start(T0)
    _ready(lc)
    lc.tick(T0 + timedelta(seconds=121))
    assert lc.state is OwnershipState.CONTROLLING
    assert lc.can_command(T0 + timedelta(seconds=121)) is True


def test_manual_override_blocks_commands_then_expires():
    lc = Lifecycle(dry_run=False, startup_grace_seconds=120)
    lc.start(T0)
    _ready(lc)
    lc.tick(T0 + timedelta(seconds=121))  # CONTROLLING
    lc.manual_override(T0 + timedelta(seconds=130), duration_s=300)
    assert lc.state is OwnershipState.MANUAL_OVERRIDE
    assert lc.can_command(T0 + timedelta(seconds=200)) is False
    # after expiry, tick resumes control
    lc.tick(T0 + timedelta(seconds=500))
    assert lc.state is OwnershipState.CONTROLLING
    assert lc.can_command(T0 + timedelta(seconds=500)) is True


def test_return_to_default_goes_observing_and_clears_override():
    lc = Lifecycle(dry_run=False, startup_grace_seconds=120)
    lc.start(T0)
    _ready(lc)
    lc.tick(T0 + timedelta(seconds=121))
    lc.manual_override(T0 + timedelta(seconds=130), duration_s=300)
    lc.return_to_default()
    assert lc.state is OwnershipState.OBSERVING
    assert lc.override_active(T0 + timedelta(seconds=200)) is False


def test_return_to_default_stays_observing_after_tick():
    # Emergency stop must NOT auto-resume commanding on the next tick (SPEC §9.1).
    lc = Lifecycle(dry_run=False, startup_grace_seconds=120)
    lc.start(T0)
    _ready(lc)
    lc.tick(T0 + timedelta(seconds=121))  # CONTROLLING
    lc.return_to_default()
    lc.tick(T0 + timedelta(seconds=400))  # readiness was reset -> must stay OBSERVING
    assert lc.state is OwnershipState.OBSERVING
    assert lc.can_command(T0 + timedelta(seconds=400)) is False


def test_restart_requires_revalidation():
    # A second start() resets readiness; the EMS may not re-advance without re-validating.
    lc = Lifecycle(dry_run=False, startup_grace_seconds=120)
    lc.start(T0)
    _ready(lc)
    lc.tick(T0 + timedelta(seconds=121))  # CONTROLLING
    lc.start(T0 + timedelta(seconds=300))  # restart
    lc.tick(T0 + timedelta(seconds=500))  # grace elapsed, but checks reset -> OBSERVING
    assert lc.state is OwnershipState.OBSERVING
    assert lc.can_command(T0 + timedelta(seconds=500)) is False


def test_naive_now_is_rejected():
    lc = Lifecycle(dry_run=False)
    with pytest.raises(ValueError):
        lc.start(datetime(2026, 6, 27, 10, 0))  # naive datetime (SPEC §13.1)
