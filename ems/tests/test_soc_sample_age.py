"""Issue #134 — never fabricate SoC 0.0; last-good sample has a max age.

Klaar-als:
1. Without a sample, SoC is unknown (`None`) and EMS is not SoC-ready.
2. A sample older than the max age is stale (SoC `None`, not ready).
3. Both cases covered with an injected clock (`now` argument).
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from ems.control.mode_controller import ModeController
from ems.control.service import (
    _SAMPLE_MAX_AGE_SECONDS,
    ControlContext,
    ControlService,
)
from ems.domain import RawSample
from ems.lifecycle import Lifecycle
from ems.settings import effective_settings
from ems.sources.battery import MockBatteryDriver

NOW = datetime(2026, 9, 27, 12, 0, tzinfo=UTC)


def _sample(soc: float = 55.0) -> RawSample:
    return RawSample(
        grid_power_w=200.0, solar_power_w=0.0, battery_power_w=0.0,
        ev_power_w=0.0, soc_pct=soc, total_gas_m3=None,
    )


class _FixedSource:
    def __init__(self, sample: RawSample | None = None, *, fail: bool = False):
        self.sample = sample
        self.fail = fail
        self.calls = 0

    def read(self):
        self.calls += 1
        if self.fail:
            raise TimeoutError("battery unreachable")
        assert self.sample is not None
        return self.sample


def _service(source) -> ControlService:
    lc = Lifecycle(dry_run=True, startup_grace_seconds=0.0)
    controller = ModeController(MockBatteryDriver(), lc, dry_run=True)
    return ControlService(
        ctx=ControlContext(),
        settings=effective_settings({}),
        controller=controller,
        store=None,
        audit_store=None,
        price_source=None,
        solar_forecast=None,
        site_tz=ZoneInfo("Europe/Amsterdam"),
        dry_run=True,
        source=source,
        data_quality=lambda now: "complete",
        validate_plan_obj=lambda plan, now: (_ for _ in ()).throw(AssertionError("unused")),
        current_soc=None,  # use real current_soc → current_sample path
        current_mode=lambda now: None,
        current_towers=lambda now: None,
        car_charging=lambda now: False,
        load_by=lambda starts: {s: 0.0 for s in starts},
        active_strategy=lambda now: "winter",
        planner_cfg=lambda: None,
        summer_cfg=lambda soc: None,
        adaptive_cfg=lambda: None,
    )


def test_no_sample_soc_is_none_and_not_ready():
    """Klaar-als 1: without a sample, SoC is None (not 0.0) and EMS is not SoC-ready."""
    source = _FixedSource(fail=True)
    svc = _service(source)

    assert svc.current_soc(NOW) is None
    assert svc.current_soc(NOW) != 0.0
    assert svc.soc_ready(NOW) is False
    assert svc.current_sample(NOW) is None
    assert source.calls >= 1


def test_stale_sample_soc_is_none_with_injected_clock():
    """Klaar-als 2+3: a last-good sample older than max age is stale; clock via `now`."""
    source = _FixedSource(_sample(72.0))
    svc = _service(source)

    # Seed a good sample at NOW.
    assert svc.current_soc(NOW) == 72.0
    assert svc.soc_ready(NOW) is True
    assert source.calls == 1

    # Later reads fail — within max age the last-good sample is still usable.
    source.fail = True
    still_ok = NOW + timedelta(seconds=_SAMPLE_MAX_AGE_SECONDS / 2)
    assert svc.current_soc(still_ok) == 72.0
    assert svc.soc_ready(still_ok) is True

    # Past max age the same cached sample is stale → unknown SoC, not ready (#134).
    stale_at = NOW + timedelta(seconds=_SAMPLE_MAX_AGE_SECONDS + 1)
    assert svc.current_soc(stale_at) is None
    assert svc.soc_ready(stale_at) is False
    assert svc.current_sample(stale_at) is None


def test_fresh_sample_after_stale_window_restores_soc():
    """A successful re-read after the max-age window restores a known SoC."""
    source = _FixedSource(_sample(40.0))
    svc = _service(source)
    assert svc.current_soc(NOW) == 40.0

    source.fail = True
    stale_at = NOW + timedelta(seconds=_SAMPLE_MAX_AGE_SECONDS + 1)
    assert svc.current_soc(stale_at) is None

    source.fail = False
    source.sample = _sample(61.0)
    recovered_at = stale_at + timedelta(seconds=1)
    assert svc.current_soc(recovered_at) == 61.0
    assert svc.soc_ready(recovered_at) is True


def test_unknown_soc_grace_action_is_fall_through():
    """Unknown SoC must not invent 0% and trip the reserve-floor hold."""
    from ems.control.service import _decide_grace_action

    assert _decide_grace_action(
        override_active=False, failsafe=False, soc_pct=None, min_reserve_soc=10.0,
    ) == "fall_through"
