"""GET /api/car/plan + POST /api/car/soc — the API layer gluing the committed EV modules
(ems/ev_schedule, ems/ev_session, ems/ev_planner, ems/cars) to settings, the SoC anchor, and the
SAME price/forecast access as /api/advisor/ev-charge. Advisory only — never commands anything.
POST /api/car/soc is auth-gated + audited exactly like POST /api/override (its first write)."""
from __future__ import annotations

import json
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from ems.cars import by_id
from ems.ev_schedule import default_schedule
from ems.sources.forecast import MockSolarForecastSource
from ems.sources.mock import MockSource
from ems.sources.prices import MockPriceSource
from ems.storage.audit import AuditStore
from ems.storage.history import HistoryStore
from ems.storage.settings import SettingsStore
from ems.web.api import create_app

AMS = ZoneInfo("Europe/Amsterdam")


def _app(tmp_path, *, token=None):
    db = str(tmp_path / "ems.sqlite")
    return create_app(
        MockSource(), dry_run=True, dev_mode="mock", tz=AMS,
        store=HistoryStore(db),
        price_source=MockPriceSource(AMS), solar_forecast=MockSolarForecastSource(AMS),
        settings_store=SettingsStore(db),
        override_store=SettingsStore(db, table="runtime_state"),
        audit_store=AuditStore(db),
        web_auth_token=token,
    )


def _all_days_enabled(min_pct: int = 80, ready_by: str = "07:30") -> dict:
    sched = default_schedule()
    for day in sched:
        sched[day] = {"enabled": True, "min_pct": min_pct, "ready_by": ready_by}
    return sched


# ---- GET /api/car/plan ----

def test_plan_disabled_returns_enabled_false(tmp_path):
    with TestClient(_app(tmp_path)) as c:  # ev.advice_enabled defaults False
        body = c.get("/api/car/plan").json()
    assert body == {"enabled": False, "plan": None, "soc": None}


def test_enabled_without_anchor_needs_anchor(tmp_path):
    with TestClient(_app(tmp_path)) as c:
        c.post("/api/settings", json={"ev.advice_enabled": True})
        body = c.get("/api/car/plan").json()
    assert body["enabled"] is True
    assert body["plan"] is None
    assert body["soc"] is None
    assert body["needs_anchor"] is True
    assert body["car_meter_configured"] is False


def test_anchor_but_empty_schedule_needs_schedule(tmp_path):
    with TestClient(_app(tmp_path)) as c:
        c.post("/api/settings", json={"ev.advice_enabled": True})
        c.post("/api/car/soc", json={"pct": 55})  # schedule stays default (all disabled)
        body = c.get("/api/car/plan").json()
    assert body["enabled"] is True
    assert body["plan"] is None
    assert body["needs_schedule"] is True
    assert body["soc"]["anchor_pct"] == 55


def test_full_plan_happy_path_and_effective_kw(tmp_path):
    with TestClient(_app(tmp_path)) as c:
        c.post("/api/settings", json={
            "ev.advice_enabled": True,
            "ev.car_id": "tesla-model-y-long-range",  # 75 kWh usable, 11 kW onboard AC
            "ev.charger_kw": 22.0,                     # wallbox is bigger than the car accepts
            "ev.battery_kwh": 57.5,
            "ev.schedule": json.dumps(_all_days_enabled(min_pct=80)),
        })
        c.post("/api/car/soc", json={"pct": 20})
        body = c.get("/api/car/plan").json()

    assert body["enabled"] is True
    # Effective power = min(charger 22 kW, car AC 11 kW) → 11 kW.
    assert body["effective_kw"] == 11.0
    assert body["car"]["id"] == "tesla-model-y-long-range"
    assert body["car"]["max_ac_kw"] == by_id("tesla-model-y-long-range").max_ac_kw == 11.0
    assert body["schedule"]["mon"]["enabled"] is True
    assert body["soc"]["soc_pct"] == 20.0
    assert body["car_meter_configured"] is False

    plan = body["plan"]
    assert plan is not None
    for key in ("soc", "deadlines", "slots", "windows", "advice",
                "total_est_cost_eur", "total_planned_kwh"):
        assert key in plan
    assert plan["soc"] == 20.0
    assert plan["deadlines"]  # at least one materialized deadline
    d0 = plan["deadlines"][0]
    for key in ("ready_by", "min_pct", "required_kwh", "planned_kwh",
                "pending_kwh", "shortfall_kwh", "already_met", "feasible"):
        assert key in d0
    assert d0["min_pct"] == 80
    assert d0["required_kwh"] > 0  # 20% → 80% on a 57.5 kWh pack needs energy
    assert isinstance(plan["slots"], list)
    assert isinstance(plan["windows"], list)
    assert isinstance(plan["advice"], str)


def test_car_meter_configured_is_exposed_when_ev_meter_ip_is_set(tmp_path):
    with TestClient(_app(tmp_path)) as c:
        c.post("/api/settings", json={
            "ev.advice_enabled": True,
            "meters.car_ip": "192.0.2.44",
            "ev.schedule": json.dumps(_all_days_enabled()),
        })
        c.post("/api/car/soc", json={"pct": 40})
        body = c.get("/api/car/plan").json()
    assert body["car_meter_configured"] is True


def test_no_car_picked_uses_charger_kw_as_effective(tmp_path):
    with TestClient(_app(tmp_path)) as c:
        c.post("/api/settings", json={
            "ev.advice_enabled": True, "ev.charger_kw": 7.4,  # no ev.car_id set
            "ev.schedule": json.dumps(_all_days_enabled()),
        })
        c.post("/api/car/soc", json={"pct": 40})
        body = c.get("/api/car/plan").json()
    assert body["effective_kw"] == 7.4
    assert body["car"] is None


# ---- POST /api/car/soc ----

def test_post_soc_validation_errors(tmp_path):
    with TestClient(_app(tmp_path)) as c:
        below = c.post("/api/car/soc", json={"pct": -1})
        above = c.post("/api/car/soc", json={"pct": 101})
        missing = c.post("/api/car/soc", json={})
        assert below.status_code == above.status_code == missing.status_code == 422
        assert "pct" in below.json()["errors"]
        assert "pct" in missing.json()["errors"]
        # A rejected payload leaves no anchor behind.
        c.post("/api/settings", json={"ev.advice_enabled": True})
        assert c.get("/api/car/plan").json().get("needs_anchor") is True


def test_post_soc_requires_token_when_configured(tmp_path):
    with TestClient(_app(tmp_path, token="s3cret")) as c:
        # Write gated exactly like POST /api/override.
        assert c.post("/api/car/soc", json={"pct": 50}).status_code == 401
        assert c.post("/api/car/soc", json={"pct": 50},
                      headers={"Authorization": "Bearer wrong"}).status_code == 401
        ok = c.post("/api/car/soc", json={"pct": 50},
                    headers={"Authorization": "Bearer s3cret"})
        assert ok.status_code == 200
        # The plan read stays open (degrade to read-only), like every other /api/* read.
        assert c.get("/api/car/plan").status_code == 200


def test_post_soc_writes_audit_entry_and_persists(tmp_path):
    with TestClient(_app(tmp_path)) as c:
        c.post("/api/settings", json={"ev.advice_enabled": True})
        r = c.post("/api/car/soc", json={"pct": 62})
        assert r.status_code == 200
        assert r.json()["soc"]["anchor_pct"] == 62
        entries = c.get("/api/audit", params={"category": "car_soc_anchor"}).json()["entries"]
        assert len(entries) == 1
        assert "62" in entries[0]["summary"]
        # GET reflects the persisted anchor (no charging since → soc == the anchored value).
        soc = c.get("/api/car/plan").json()["soc"]
    assert soc["anchor_pct"] == 62
    assert soc["soc_pct"] == 62.0


def test_post_soc_without_store_returns_503():
    app = create_app(MockSource(), dry_run=True, dev_mode="mock")  # no history store
    assert TestClient(app).post("/api/car/soc", json={"pct": 50}).status_code == 503
