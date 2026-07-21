"""Runtime-derived intelligence capability status (B-79 Task 1): the box starts empty and honest
-- only a recorded evaluation may report shadow_evaluation/advisory/active. This is the anti-
constant proof for `_intelligence_status()` and `/api/intelligence`.

App/client construction mirrors test_battery_plan_api.py's `_app(tmp_path)` + `TestClient` pattern.
"""
from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from ems.freshness import FreshnessTracker
from ems.sense import SIGNALS
from ems.sources.forecast import MockSolarForecastSource
from ems.sources.mock import MockSource
from ems.sources.prices import MockPriceSource
from ems.storage.history import HistoryStore
from ems.storage.settings import SettingsStore
from ems.web.api import create_app

AMS = ZoneInfo("Europe/Amsterdam")


def _fresh_tracker():
    now = datetime.now(UTC)
    fresh = FreshnessTracker()
    fresh.register(*SIGNALS)
    for signal in SIGNALS:
        fresh.mark(signal, now)
    return fresh


def _app(tmp_path):
    db = str(tmp_path / "ems.sqlite")
    return create_app(
        MockSource(), dry_run=True, dev_mode="mock", tz=AMS,
        store=HistoryStore(db), freshness=_fresh_tracker(),
        price_source=MockPriceSource(AMS),
        solar_forecast=MockSolarForecastSource(AMS),
        settings_store=SettingsStore(db),
        web_auth_token=None,
    )


def _prov_intelligence(client):
    body = client.get("/api/battery-plan").json()
    return body["provenance"]["intelligence"]


def test_intelligence_status_default_is_not_active_object(tmp_path):
    with TestClient(_app(tmp_path)) as c:
        prov = _prov_intelligence(c)
        assert isinstance(prov, dict)
        assert prov["state"] == "not_active"
        assert prov["last_evaluated_at"] is None
        assert prov["last_result"] is None
        assert isinstance(prov["reason"], str) and prov["reason"]


def test_api_intelligence_endpoint_shape(tmp_path):
    with TestClient(_app(tmp_path)) as c:
        body = c.get("/api/intelligence").json()
        assert set(body) == {"state", "last_evaluated_at", "last_result", "reason"}
        assert body["state"] == "not_active"


def test_status_is_runtime_derived_not_a_constant(tmp_path):
    # The anti-constant proof: inject a recorded evaluation into the runtime seam and the reported
    # status must reflect it (state + timestamp + result), via BOTH surfaces.
    with TestClient(_app(tmp_path)) as c:
        c.app.state.intelligence_box["latest"] = {
            "state": "shadow_evaluation",
            "ts": "2026-07-21T12:00:00+00:00",
            "result": "pessimistic vs baseline: -0.3 kWh",
        }
        ep = c.get("/api/intelligence").json()
        assert ep["state"] == "shadow_evaluation"
        assert ep["last_evaluated_at"] == "2026-07-21T12:00:00+00:00"
        assert ep["last_result"] == "pessimistic vs baseline: -0.3 kWh"
        prov = _prov_intelligence(c)
        assert prov["state"] == "shadow_evaluation"


def test_no_false_capability_claim_when_unrecorded(tmp_path):
    with TestClient(_app(tmp_path)) as c:
        ep = c.get("/api/intelligence").json()
        assert ep["state"] not in {"shadow_evaluation", "advisory", "active"}
