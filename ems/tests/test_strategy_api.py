"""The strategy selector wired through the API: /api/strategy + the chosen strategy driving the
plan. Mock sources only."""
import json
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from ems.planner.strategy import HysteresisState
from ems.sources.forecast import MockSolarForecastSource
from ems.sources.mock import MockSource
from ems.sources.prices import MockPriceSource
from ems.storage.audit import AuditStore
from ems.storage.settings import SettingsStore
from ems.web.api import create_app

AMS = ZoneInfo("Europe/Amsterdam")


def _app(tmp_path):
    db = str(tmp_path / "ems.sqlite")
    return create_app(
        MockSource(), dry_run=True, dev_mode="mock", tz=AMS,
        price_source=MockPriceSource(AMS), solar_forecast=MockSolarForecastSource(AMS),
        settings_store=SettingsStore(db),
    )


def test_strategy_endpoint_reports_mode_and_active(tmp_path):
    with TestClient(_app(tmp_path)) as c:
        b = c.get("/api/strategy").json()
        assert b["mode"] == "auto"  # default
        assert b["active"] in ("summer", "winter")
        assert b["auto"] is True
        assert b["summary"]  # plain-language description present


def test_forcing_summer_makes_the_strategy_active_and_explained(tmp_path):
    with TestClient(_app(tmp_path)) as c:
        c.post("/api/settings", json={"strategy.mode": "summer"})
        b = c.get("/api/strategy").json()
        assert b["mode"] == "summer" and b["active"] == "summer"
        assert b["auto"] is False
        assert "solar" in b["summary"].lower()


def test_strategy_choice_changes_the_plan(tmp_path):
    # The toggle resolves the active strategy AND produces a materially different plan: winter is
    # price-arbitrage (now demand-sized — it only charges if the peak needs it above the stored
    # reserve), summer is solar-first. The plans differ in their per-slot reasoning regardless of
    # whether the high mock SoC happens to need a winter grid charge.
    with TestClient(_app(tmp_path)) as c:
        c.post("/api/settings", json={"strategy.mode": "winter"})
        winter = c.get("/api/plan").json()
        w_active = c.get("/api/strategy").json()["active"]
        c.post("/api/settings", json={"strategy.mode": "summer"})
        summer = c.get("/api/plan").json()
        s_active = c.get("/api/strategy").json()["active"]
    assert w_active == "winter" and s_active == "summer"
    # Distinct planners → distinct reasoning (winter cites break-even/peaks, summer cites solar).
    w_reasons = " ".join(s["reason"] for s in winter["slots"])
    s_reasons = " ".join(s["reason"] for s in summer["slots"])
    assert w_reasons != s_reasons


def test_strategy_endpoint_exposes_pending_hysteresis(tmp_path):
    # F-39 / #345: count/days/pending/committed come from the live HysteresisState. Seeding the
    # box does not run apply_hysteresis; last_day is today so the endpoint's existing resolve
    # does not advance the counter either.
    app = _app(tmp_path)
    with TestClient(app) as c:
        now = datetime.now(UTC)
        day = now.astimezone(AMS).date().isoformat()
        app.state.control_service._ctx.hysteresis_box["state"] = HysteresisState(
            committed="winter", pending="summer", count=2, last_day=day,
        )
        body = c.get("/api/strategy").json()
    assert body["active"] == "winter"
    assert body["hysteresis"] == {
        "committed": "winter", "pending": "summer", "count": 2, "days": 3,
    }


def test_force_summer_changes_active_strategy_within_one_cycle_and_audits(tmp_path):
    # F-39 / #345 AC2: 'Nu solar-first' is a settings write (strategy.mode=summer). The next
    # control cycle reads that mode via ControlService.resolve_strategy — one call, no battery
    # write. The same POST records config_change. 'Terug naar Auto' restores strategy.mode=auto.
    db = str(tmp_path / "ems.sqlite")
    audit = AuditStore(db)
    app = create_app(
        MockSource(), dry_run=True, dev_mode="mock", tz=AMS,
        price_source=MockPriceSource(AMS), solar_forecast=MockSolarForecastSource(AMS),
        settings_store=SettingsStore(db), audit_store=audit,
    )
    with TestClient(app) as c:
        svc = app.state.control_service
        now = datetime.now(UTC)
        day = now.astimezone(AMS).date().isoformat()
        svc._ctx.hysteresis_box["state"] = HysteresisState(
            committed="winter", pending="summer", count=2, last_day=day,
        )
        held = c.get("/api/strategy").json()
        assert held["mode"] == "auto" and held["active"] == "winter"
        assert svc.resolve_strategy(now)[0] == "winter"

        saved = c.post("/api/settings", json={"strategy.mode": "summer"})
        assert saved.status_code == 200
        # Exactly one resolution: what the following cycle uses. Forced summer bypasses the hold.
        assert svc.resolve_strategy(now)[0] == "summer"
        after = c.get("/api/strategy").json()
        assert after["mode"] == "summer" and after["active"] == "summer"

        rows = c.get("/api/audit", params={"category": "config_change"}).json()["entries"]
        assert rows
        assert rows[0]["detail"]["keys"] == ["strategy.mode"]
        assert "strategy.mode" in rows[0]["summary"]

        restored = c.post("/api/settings", json={"strategy.mode": "auto"})
        assert restored.status_code == 200
        assert restored.json()["values"]["strategy.mode"] == "auto"
        assert c.get("/api/strategy").json()["mode"] == "auto"
        back_rows = c.get("/api/audit", params={"category": "config_change"}).json()["entries"]
        assert back_rows[0]["detail"]["keys"] == ["strategy.mode"]
        # The override path never arms the writer or flips the dry-run floors.
        values = c.get("/api/settings").json()["values"]
        assert "control.operational" in values and "control.dry_run" in values
        assert all(
            "control.operational" not in e["detail"]["keys"]
            and "control.dry_run" not in e["detail"]["keys"]
            for e in back_rows
        )


def test_plan_detail_includes_active_strategy(tmp_path):
    with TestClient(_app(tmp_path)) as c:
        c.post("/api/settings", json={"strategy.mode": "winter"})
        b = c.get("/api/plan-detail").json()
    assert b["strategy"] == "winter"


def test_replay_bundle_is_complete_and_secret_free(tmp_path):
    # The replay bundle reproduces a decision: inputs + plan + projection + validation + decision.
    # It must NEVER carry secrets/identifiers (privacy §12) — only planning knobs + values.
    with TestClient(_app(tmp_path)) as c:
        c.post("/api/settings", json={"strategy.mode": "winter"})
        b = c.get("/api/replay").json()
    assert {"generated_at", "strategy", "inputs", "plan", "projection",
            "validation", "decision"} <= set(b)
    assert b["plan"] is not None and "slots" in b["plan"]
    # The settings block is the explicit planning allow-list — no IP/token/key/location identifier.
    skeys = set(b["inputs"]["settings"])
    assert "battery.usable_kwh" in skeys
    assert not any(k for k in skeys if k.endswith("_ip") or "token" in k or "api_key" in k
                   or k.startswith("site.") or "tibber" in k)
    blob = json.dumps(b).lower()
    for forbidden in ("192.168", "token", "api_key", "secret"):
        assert forbidden not in blob
