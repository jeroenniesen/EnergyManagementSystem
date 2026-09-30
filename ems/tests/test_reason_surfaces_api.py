"""B-74 / #84 slice 2: structured decision reason on web/logs/diagnostics surfaces.

Slice 1 (schema + /api/battery-plan) is covered in test_battery_plan_api.py /
test_decision_reason.py. This module asserts the SAME reason object is exposed on
diagnostics, decision, export package, and that logs/audit can carry it — no new schema.
"""
from __future__ import annotations

import io
import json
import zipfile
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from ems.control.mode_controller import ModeController
from ems.control.override import Override
from ems.control.service import ControlContext, ControlService
from ems.domain import BatteryIntent, PhysicalMode
from ems.freshness import FreshnessTracker
from ems.lifecycle import Lifecycle
from ems.planner.reason import GATE_DICT_KEYS, REASON_DICT_KEYS
from ems.planner.validator import PlanValidation
from ems.sense import SIGNALS
from ems.settings import effective_settings
from ems.sources.battery import MockBatteryDriver
from ems.sources.forecast import MockSolarForecastSource
from ems.sources.live import HomeWizardMeter, LiveSource
from ems.sources.mock import MockSource
from ems.sources.prices import MockPriceSource
from ems.storage.history import HistoryStore
from ems.storage.settings import SettingsStore
from ems.web.api import create_app

AMS = ZoneInfo("Europe/Amsterdam")
NOW = datetime(2026, 6, 28, 12, 0, tzinfo=UTC)


def _app(tmp_path, *, with_forecast: bool = True, freshness: FreshnessTracker | None = None,
         source=None):
    db = str(tmp_path / "ems.sqlite")
    return create_app(
        source or MockSource(),
        dry_run=True,
        dev_mode="mock",
        tz=AMS,
        store=HistoryStore(db),
        freshness=freshness,
        price_source=MockPriceSource(AMS),
        solar_forecast=MockSolarForecastSource(AMS) if with_forecast else None,
        settings_store=SettingsStore(db),
    )


def _assert_reason_shape(reason: dict) -> None:
    assert set(reason) == REASON_DICT_KEYS
    assert set(reason["gates"]) == GATE_DICT_KEYS
    assert reason["safety_constraint"]["action"] in {"paused", "proceed"}
    assert isinstance(reason["summary"], str)


def _assert_same_reason(a: dict, b: dict) -> None:
    """Object equality (or deep equality of key fields) — not just key-set overlap."""
    _assert_reason_shape(a)
    _assert_reason_shape(b)
    assert a == b


def test_diagnostics_carries_structured_decision_reason(tmp_path):
    with TestClient(_app(tmp_path)) as c:
        diag = c.get("/api/diagnostics").json()
        plan = c.get("/api/battery-plan").json()

    reason = diag["decision_reason"]
    _assert_same_reason(reason, plan["reason"])


def test_decision_endpoint_carries_structured_decision_reason(tmp_path):
    """Unconfigured controller still returns the canonical plan reason (same as battery-plan)."""
    with TestClient(_app(tmp_path)) as c:
        body = c.get("/api/decision").json()
        plan = c.get("/api/battery-plan").json()

    reason = body["decision_reason"]
    _assert_same_reason(reason, plan["reason"])


def test_export_package_manifest_carries_decision_reason(tmp_path):
    with TestClient(_app(tmp_path)) as c:
        r = c.get("/api/export/package?days=7")
        plan = c.get("/api/battery-plan").json()

    assert r.status_code == 200
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        manifest = json.loads(z.read("manifest.json"))
        summary = z.read("validation_summary.txt").decode()

    reason = manifest["decision_reason"]
    _assert_same_reason(reason, plan["reason"])
    assert "Decision reason" in summary
    assert "Rejected alt:" in summary
    assert "Risk:" in summary
    assert "Gates:" in summary


def test_diagnostics_reason_paused_when_inputs_stale(tmp_path):
    """Gate outcomes on diagnostics match the battery-plan reason (validator unsafe → paused)."""
    fresh = FreshnessTracker()
    fresh.register(*SIGNALS)
    with TestClient(_app(tmp_path, freshness=fresh)) as c:
        diag = c.get("/api/diagnostics").json()
        plan = c.get("/api/battery-plan").json()

    reason = diag["decision_reason"]
    _assert_same_reason(reason, plan["reason"])
    assert reason["safety_constraint"]["action"] == "paused"
    assert reason["safety_constraint"]["code"] == plan["reason"]["safety_constraint"]["code"]
    assert reason["gates"]["validator_code"] == reason["safety_constraint"]["code"]


class _FailingBattery:
    def read_power_soc(self):
        raise TimeoutError("Indevolt unreachable")


def test_unknown_soc_reason_matches_battery_plan_empty(tmp_path):
    """#134: unknown SoC → same empty/paused reason on diagnostics and battery-plan."""
    src = LiveSource(
        p1=HomeWizardMeter("x", http_get=lambda _u: {"active_power_w": 0}),
        battery=_FailingBattery(),
    )
    with TestClient(_app(tmp_path, source=src)) as c:
        diag = c.get("/api/diagnostics").json()
        plan = c.get("/api/battery-plan").json()

    _assert_same_reason(diag["decision_reason"], plan["reason"])
    assert plan["reason"]["safety_constraint"]["action"] == "paused"
    assert "No current plan" in plan["reason"]["summary"]


def test_replay_decision_embeds_structured_reason(tmp_path):
    with TestClient(_app(tmp_path)) as c:
        body = c.get("/api/replay").json()
        plan = c.get("/api/battery-plan").json()

    assert body.get("plan") is not None, "mock fixture must produce a replayable plan"
    reason = body["decision"]["decision_reason"]
    _assert_same_reason(reason, plan["reason"])


def test_control_tick_audit_detail_includes_decision_reason():
    """Control-loop battery_decision rows carry the structured reason where appropriate."""
    lc = Lifecycle(dry_run=False, startup_grace_seconds=0.0)
    lc.start(NOW)
    lc.mark_sensors_validated()
    lc.mark_probe_ok()
    lc.mark_plan_loaded()
    lc.tick(NOW)
    controller = ModeController(MockBatteryDriver(), lc, dry_run=False)
    ctx = ControlContext()
    settings = effective_settings({})
    structured = {
        "chosen_window": None,
        "rejected_alternative": None,
        "expected_benefit": {"eur": None, "summary": "n/a"},
        "risk": {"margin_eur_per_kwh": None, "summary": "n/a"},
        "safety_constraint": {"code": None, "message": None, "action": "proceed"},
        "gates": {
            "validator_code": None, "failsafe": False, "dwell": False,
            "cap_reached": False, "unconfirmed": False,
        },
        "summary": "audit-row reason fixture",
    }
    svc = ControlService(
        ctx=ctx, settings=settings, controller=controller, store=None, audit_store=None,
        price_source=None, solar_forecast=None,
        site_tz=AMS, dry_run=False,
        current_soc=lambda now: 50.0,
        current_mode=lambda now: PhysicalMode.AUTO,
        current_towers=lambda now: None,
        data_quality=lambda now: "fresh",
        car_charging=lambda now: False,
        load_by=lambda starts: {s: 0.0 for s in starts},
        active_strategy=lambda now: "winter",
        validate_plan_obj=lambda plan, now: PlanValidation(status="valid"),
        planner_cfg=lambda: None,
        summer_cfg=lambda soc: None,
        adaptive_cfg=lambda: None,
    )
    svc._decision_reason = lambda now: structured
    ctx.override_box["ov"] = Override(
        intent=BatteryIntent.GRID_CHARGE_TO_TARGET, expires_at=NOW + timedelta(hours=1),
    )

    records = svc.control_tick(NOW)

    assert records, "override tick must produce a battery_decision audit record"
    detail = records[0]["detail"]
    assert detail.get("outcome") is not None
    assert detail["decision_reason"] == structured
    assert set(detail["decision_reason"]) == REASON_DICT_KEYS
