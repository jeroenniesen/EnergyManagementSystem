"""Unit tests for thin control-loop soak counters (#179)."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
import pytest
from fastapi.testclient import TestClient
from tenacity import wait_none

from ems.control.loop_counters import LOOP_COUNTERS, LoopCounters
from ems.control.mode_controller import ModeController
from ems.control.override import Override
from ems.control.service import ControlContext, ControlService
from ems.domain import BatteryIntent
from ems.lifecycle import Lifecycle
from ems.planner.validator import Finding, PlanValidation
from ems.settings import effective_settings
from ems.sources.battery import MockBatteryDriver
from ems.sources.mock import MockSource
from ems.web.api import create_app

NOW = datetime(2026, 6, 28, 12, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _reset_loop_counters():
    LOOP_COUNTERS.reset()
    yield
    LOOP_COUNTERS.reset()


def test_loop_counters_record_cycle_increments_without_timing_asserts():
    c = LoopCounters()
    c.record_cycle(latency_ms=12.5, outcome="applied")
    c.record_cycle(latency_ms=7.5, outcome="idempotent")
    c.record_cycle(latency_ms=1.0, outcome="dry_run", fail_safe=True, stale_sensors=True)
    c.incr_http_retries(2)
    c.incr_validator_rejects()
    snap = c.snapshot()
    assert snap["cycles"] == 3
    assert snap["mode_applies"] == 1
    assert snap["http_retries"] == 2
    assert snap["validator_rejects"] == 1
    assert snap["stale_sensor_cycles"] == 1
    assert snap["outcomes"] == {
        "idempotent": 1, "dry_run": 1, "fail_safe": 1, "applied": 1,
    }
    assert snap["cycle_latency_ms_last"] == 1.0
    assert snap["cycle_latency_ms_avg"] == pytest.approx(7.0)
    assert snap["last_outcome"] == "dry_run"


def _controlling_controller(driver=None) -> ModeController:
    lc = Lifecycle(dry_run=False, startup_grace_seconds=0.0)
    lc.start(NOW)
    lc.mark_sensors_validated()
    lc.mark_probe_ok()
    lc.mark_plan_loaded()
    lc.tick(NOW)
    return ModeController(driver or MockBatteryDriver(), lc, dry_run=False)


def _service(controller: ModeController, *, validate=None, data_quality=None) -> ControlService:
    ctx = ControlContext()
    settings = effective_settings({})
    return ControlService(
        ctx=ctx, settings=settings, controller=controller, store=None, audit_store=None,
        price_source=None, solar_forecast=None,
        site_tz=ZoneInfo("Europe/Amsterdam"), dry_run=False,
        current_soc=lambda now: 50.0,
        current_mode=lambda now: controller.driver.current_mode(),
        current_towers=lambda now: None,
        data_quality=data_quality or (lambda now: "fresh"),
        car_charging=lambda now: False,
        load_by=lambda starts: {s: 0.0 for s in starts},
        active_strategy=lambda now: "winter",
        validate_plan_obj=validate or (lambda plan, now: PlanValidation(status="valid")),
        planner_cfg=lambda: None,
        summer_cfg=lambda soc: None,
        adaptive_cfg=lambda: None,
    )


def test_hermetic_control_tick_increments_apply_counter():
    controller = _controlling_controller()
    svc = _service(controller)
    svc._ctx.override_box["ov"] = Override(
        intent=BatteryIntent.GRID_CHARGE_TO_TARGET, expires_at=NOW + timedelta(hours=1),
    )
    before = LOOP_COUNTERS.snapshot()
    records = svc.control_tick(NOW)
    after = LOOP_COUNTERS.snapshot()
    assert records and records[0]["detail"]["outcome"] == "applied"
    assert after["cycles"] == before["cycles"] + 1
    assert after["mode_applies"] == before["mode_applies"] + 1
    assert after["outcomes"]["applied"] == before["outcomes"]["applied"] + 1
    assert after["cycle_latency_ms_last"] is not None
    assert after["last_outcome"] == "applied"


def test_hermetic_control_tick_counts_idempotent():
    controller = _controlling_controller()
    # Already AUTO; planning returns None → override to AUTO is idempotent, or no override.
    # Charge once, then another charge override while already charging → idempotent.
    svc = _service(controller)
    svc._ctx.override_box["ov"] = Override(
        intent=BatteryIntent.GRID_CHARGE_TO_TARGET, expires_at=NOW + timedelta(hours=1),
    )
    svc.control_tick(NOW)
    LOOP_COUNTERS.reset()
    records = svc.control_tick(NOW + timedelta(seconds=1))
    snap = LOOP_COUNTERS.snapshot()
    assert snap["cycles"] == 1
    assert snap["mode_applies"] == 0
    assert snap["outcomes"]["idempotent"] == 1
    assert snap["last_outcome"] == "idempotent"
    # Quiet idempotent may return [] (no drift) — that is fine.
    assert isinstance(records, list)


def test_hermetic_validator_reject_increments_counter():
    controller = _controlling_controller()

    def _reject(plan, now):
        return PlanValidation(
            status="unsafe",
            findings=(Finding(severity="unsafe", code="test", message="unsafe for test"),),
        )

    svc = _service(controller, validate=_reject)
    svc._ctx.override_box["ov"] = Override(
        intent=BatteryIntent.GRID_CHARGE_TO_TARGET, expires_at=NOW + timedelta(hours=1),
    )
    LOOP_COUNTERS.reset()
    svc.control_tick(NOW)
    snap = LOOP_COUNTERS.snapshot()
    assert snap["validator_rejects"] == 1
    assert snap["outcomes"]["fail_safe"] >= 1
    assert snap["cycles"] == 1


def test_effective_intent_alone_does_not_inflate_validator_rejects():
    """UI/recorder/advisory effective_intent must not bump soak counters (#179)."""
    controller = _controlling_controller()

    def _reject(plan, now):
        return PlanValidation(
            status="unsafe",
            findings=(Finding(severity="unsafe", code="test", message="unsafe for test"),),
        )

    svc = _service(controller, validate=_reject)
    svc._ctx.override_box["ov"] = Override(
        intent=BatteryIntent.GRID_CHARGE_TO_TARGET, expires_at=NOW + timedelta(hours=1),
    )
    LOOP_COUNTERS.reset()
    for _ in range(5):
        intent, *_ = svc.effective_intent(NOW)
        assert intent is BatteryIntent.ALLOW_SELF_CONSUMPTION
        assert svc._decision_engine.last_validator_rejected is True
    snap = LOOP_COUNTERS.snapshot()
    assert snap["validator_rejects"] == 0
    assert snap["cycles"] == 0
    assert snap["outcomes"]["fail_safe"] == 0


def test_diagnostics_exposes_control_loop_counters():
    with TestClient(create_app(MockSource(), dry_run=True, dev_mode="mock")) as c:
        b = c.get("/api/diagnostics").json()
    assert "control_loop" in b
    cl = b["control_loop"]
    assert {
        "cycles", "cycle_latency_ms_last", "cycle_latency_ms_avg",
        "stale_sensor_cycles", "http_retries", "mode_applies", "validator_rejects",
        "outcomes", "last_outcome",
    } <= set(cl)
    assert set(cl["outcomes"]) == {"idempotent", "dry_run", "fail_safe", "applied"}
    # Fresh process / dry-run: zeros until an operational tick runs.
    assert cl["cycles"] == 0
    assert cl["mode_applies"] == 0
    assert cl["http_retries"] == 0


def test_http_read_retry_increments_http_retries(monkeypatch):
    import ems.http_client as http_client_mod
    from ems.http_client import HttpRuntime, request

    monkeypatch.setattr(http_client_mod, "READ_RETRY_WAIT", wait_none())
    LOOP_COUNTERS.reset()

    class _Flaky:
        def __init__(self):
            self.calls = 0

        def request(self, *a, **k):
            self.calls += 1
            if self.calls < 3:
                raise httpx.ConnectError("boom")
            return httpx.Response(200, request=httpx.Request("GET", "http://x"))

    client = _Flaky()
    rt = HttpRuntime(client=client)  # type: ignore[arg-type]
    request("GET", "http://example.test/", profile="lan_read", runtime=rt, client=client)  # type: ignore[arg-type]
    # Two retries after the first failure (attempts 2 and 3) → 2 increments.
    assert LOOP_COUNTERS.http_retries == 2
    assert client.calls == 3
