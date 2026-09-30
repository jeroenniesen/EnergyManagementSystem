"""B-74 / #84 slice 2: structured decision reason on web/logs/diagnostics surfaces.

Slice 1 (schema + /api/battery-plan) is covered in test_battery_plan_api.py /
test_decision_reason.py. This module asserts the SAME reason object is exposed on
diagnostics, decision, export package, and that logs/audit can carry it — no new schema.
"""
from __future__ import annotations

import io
import json
import zipfile
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from ems.freshness import FreshnessTracker
from ems.planner.reason import GATE_DICT_KEYS, REASON_DICT_KEYS
from ems.sense import SIGNALS
from ems.sources.forecast import MockSolarForecastSource
from ems.sources.mock import MockSource
from ems.sources.prices import MockPriceSource
from ems.storage.history import HistoryStore
from ems.storage.settings import SettingsStore
from ems.web.api import create_app

AMS = ZoneInfo("Europe/Amsterdam")


def _app(tmp_path, *, with_forecast: bool = True, freshness: FreshnessTracker | None = None):
    db = str(tmp_path / "ems.sqlite")
    return create_app(
        MockSource(),
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


def test_diagnostics_carries_structured_decision_reason(tmp_path):
    with TestClient(_app(tmp_path)) as c:
        diag = c.get("/api/diagnostics").json()
        plan = c.get("/api/battery-plan").json()

    reason = diag["decision_reason"]
    _assert_reason_shape(reason)
    # Same contract as /api/battery-plan (web + diagnostics tell one story).
    assert set(reason) == set(plan["reason"])
    assert reason["safety_constraint"]["action"] == plan["reason"]["safety_constraint"]["action"]


def test_decision_endpoint_carries_structured_decision_reason(tmp_path):
    """Without a controller the decision endpoint is unconfigured but still carries reason."""
    with TestClient(_app(tmp_path)) as c:
        body = c.get("/api/decision").json()
        plan = c.get("/api/battery-plan").json()

    reason = body["decision_reason"]
    _assert_reason_shape(reason)
    # Unconfigured path uses empty/paused shape; battery-plan may have a live plan — both
    # share the same key contract so web/logs never invent a parallel schema.
    assert set(reason) == set(plan["reason"])


def test_export_package_manifest_carries_decision_reason(tmp_path):
    with TestClient(_app(tmp_path)) as c:
        r = c.get("/api/export/package?days=7")
        plan = c.get("/api/battery-plan").json()

    assert r.status_code == 200
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        manifest = json.loads(z.read("manifest.json"))
        summary = z.read("validation_summary.txt").decode()

    reason = manifest["decision_reason"]
    _assert_reason_shape(reason)
    assert set(reason) == set(plan["reason"])
    assert "Decision reason" in summary


def test_diagnostics_reason_paused_when_inputs_stale(tmp_path):
    """Gate outcomes on diagnostics match the battery-plan reason (validator unsafe → paused)."""
    fresh = FreshnessTracker()
    fresh.register(*SIGNALS)
    with TestClient(_app(tmp_path, freshness=fresh)) as c:
        diag = c.get("/api/diagnostics").json()
        plan = c.get("/api/battery-plan").json()

    reason = diag["decision_reason"]
    assert reason["safety_constraint"]["action"] == "paused"
    assert reason["safety_constraint"]["code"] == plan["reason"]["safety_constraint"]["code"]
    assert reason["gates"]["validator_code"] == reason["safety_constraint"]["code"]


def test_replay_decision_embeds_structured_reason(tmp_path):
    with TestClient(_app(tmp_path)) as c:
        body = c.get("/api/replay").json()

    if body.get("plan") is None:
        return
    reason = body["decision"]["decision_reason"]
    _assert_reason_shape(reason)
