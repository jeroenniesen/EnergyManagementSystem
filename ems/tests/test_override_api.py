import asyncio
import logging
from dataclasses import replace
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from ems.control.mode_controller import ModeController
from ems.control.override_must_charge import (
    AUDIT_CANCEL,
    AUDIT_END,
    AUDIT_START,
    WATCH_ONLY_TEXT,
)
from ems.freshness import FreshnessTracker
from ems.lifecycle import Lifecycle
from ems.sense import SIGNALS
from ems.sources.battery import MockBatteryDriver
from ems.sources.forecast import MockSolarForecastSource
from ems.sources.mock import MockSource
from ems.sources.prices import MockPriceSource
from ems.storage.audit import AuditStore
from ems.storage.history import HistoryStore
from ems.storage.settings import SettingsStore
from ems.web.api import _spawn_tracked, create_app


def _fresh_tracker():
    # All signals fresh → data quality not unsafe, so a risky override is honoured (not gated).
    fr = FreshnessTracker()
    fr.register(*SIGNALS)
    now = datetime.now(UTC)
    for s in SIGNALS:
        fr.mark(s, now)
    return fr


def _app(tmp_path, **kw):
    db = str(tmp_path / "ems.sqlite")
    return create_app(
        MockSource(), dry_run=True, dev_mode="mock",
        # Known-zero grid-charge counter. A missing store is an unknown counter and blocks
        # grid charging (#317); these tests are about the override, not that fail-closed path.
        store=HistoryStore(db),
        override_store=SettingsStore(db, table="runtime_state"),
        **kw,
    )


def test_get_override_defaults_to_none(tmp_path):
    with TestClient(_app(tmp_path)) as c:
        b = c.get("/api/override").json()
    assert b["intent"] is None
    assert b["active"] is False
    assert "grid_charge_to_target" in b["options"]


def test_set_and_clear_override(tmp_path):
    with TestClient(_app(tmp_path)) as c:
        r = c.post("/api/override", json={"intent": "grid_charge_to_target", "minutes": 30})
        assert r.status_code == 200
        b = r.json()
        assert b["intent"] == "grid_charge_to_target"
        assert b["active"] is True
        assert 0 < b["seconds_remaining"] <= 30 * 60
        # Clearing returns to following the plan.
        cleared = c.post("/api/override", json={"intent": None}).json()
        assert cleared["intent"] is None
        assert cleared["active"] is False


def test_override_persists_across_restart(tmp_path):
    with TestClient(_app(tmp_path)) as c:
        c.post("/api/override", json={"intent": "hold_reserve", "minutes": 120})
    with TestClient(_app(tmp_path)) as c2:
        b = c2.get("/api/override").json()
    assert b["intent"] == "hold_reserve"
    assert b["active"] is True


def test_invalid_override_rejected(tmp_path):
    with TestClient(_app(tmp_path)) as c:
        bad_intent = c.post("/api/override", json={"intent": "nope", "minutes": 30})
        assert bad_intent.status_code == 422
        assert "intent" in bad_intent.json()["errors"]
        bad_minutes = c.post("/api/override", json={"intent": "hold_reserve", "minutes": 99999})
        assert bad_minutes.status_code == 422
        assert "minutes" in bad_minutes.json()["errors"]
        # #135: 24 h at max power is no longer admissible — duration is server-clamped to 8 h.
        day_long = c.post(
            "/api/override",
            json={"intent": "grid_charge_to_target", "minutes": 24 * 60},
        )
        assert day_long.status_code == 422
        assert "minutes" in day_long.json()["errors"]
        # A rejected payload must leave no override set.
        assert c.get("/api/override").json()["active"] is False


def test_override_without_store_returns_503():
    app = create_app(MockSource(), dry_run=True, dev_mode="mock")  # no override_store
    assert TestClient(app).post("/api/override", json={"intent": "hold_reserve"}).status_code == 503


def test_active_override_drives_the_decision(tmp_path):
    # The decision must reflect the forced intent, beating whatever the planner would pick.
    controller = ModeController(MockBatteryDriver(), Lifecycle(dry_run=True), dry_run=True)
    app = _app(
        tmp_path, controller=controller, freshness=_fresh_tracker(),
        price_source=MockPriceSource(ZoneInfo("Europe/Amsterdam")),
    )
    with TestClient(app) as c:
        c.post("/api/override", json={"intent": "grid_charge_to_target", "minutes": 60})
        d = c.get("/api/decision").json()
    assert d["intent"] == "grid_charge_to_target"
    assert d["override_active"] is True
    assert "manual override" in d["plan_reason"]


def test_override_works_without_price_source(tmp_path):
    # An override is a control action, not a forecast — it must apply even with no prices/plan.
    controller = ModeController(MockBatteryDriver(), Lifecycle(dry_run=True), dry_run=True)
    app = _app(tmp_path, controller=controller, freshness=_fresh_tracker())  # no price_source
    with TestClient(app) as c:
        c.post("/api/override", json={"intent": "hold_reserve", "minutes": 60})
        d = c.get("/api/decision").json()
        alerts = c.get("/api/alerts").json()
    assert d["intent"] == "hold_reserve"
    assert d["override_active"] is True
    assert any(a["key"] == "manual_override_active" for a in alerts["alerts"])


def test_spawn_tracked_holds_ref_logs_crash_and_drops_ref(caplog):
    # The override endpoint fires the immediate control cycle via _spawn_tracked so the task (a) is
    # strongly referenced and can't be GC'd mid-run (the "charge now" silent no-op), (b) drops that
    # ref on completion, and (c) surfaces a crash loudly instead of the exception vanishing.
    async def _boom():
        raise RuntimeError("cycle blew up")

    async def _ok():
        return None

    async def _run_crash(task_set):
        t = _spawn_tracked(_boom(), "Override control cycle", task_set)
        assert t in task_set          # strong ref held while the task is in flight
        try:
            await t
        except RuntimeError:
            pass
        await asyncio.sleep(0)        # let the done-callbacks (discard + crash-logger) run
        return t

    async def _run_ok(task_set):
        t = _spawn_tracked(_ok(), "Override control cycle", task_set)
        await t
        await asyncio.sleep(0)
        return t

    crash_tasks: set = set()
    with caplog.at_level(logging.ERROR):
        asyncio.run(_run_crash(crash_tasks))
    assert crash_tasks == set()                       # ref dropped on completion (set empties)
    assert "exited unexpectedly" in caplog.text       # the crash is logged loudly

    ok_tasks: set = set()
    asyncio.run(_run_ok(ok_tasks))
    assert ok_tasks == set()                           # a normal run also removes its ref


_AMS = ZoneInfo("Europe/Amsterdam")
_WHEN = datetime(2026, 1, 15, 22, 0, tzinfo=UTC)
_CHEAP_SLOTS = (
    "2026-01-16T05:15:00+01:00",
    "2026-01-16T05:30:00+01:00",
    "2026-01-16T05:45:00+01:00",
)


class _StepClock:
    """Injectable clock. Tests move ``instant``; nothing reads the wall clock."""

    def __init__(self, instant: datetime) -> None:
        self.instant = instant

    def now_utc(self) -> datetime:
        return self.instant


def _must_app(tmp_path, *, clock=None, price_source=None, controller=None, with_prices=True):
    db = str(tmp_path / "ems.sqlite")
    clock = clock or _StepClock(_WHEN)
    if with_prices and price_source is None:
        price_source = MockPriceSource(_AMS, clock=clock.now_utc)
    if controller is None and with_prices:
        controller = ModeController(MockBatteryDriver(), Lifecycle(dry_run=True), dry_run=True)
    return create_app(
        MockSource(), dry_run=True, dev_mode="mock", tz=_AMS, clock=clock,
        store=HistoryStore(db),
        override_store=SettingsStore(db, table="runtime_state"),
        settings_store=SettingsStore(db),
        audit_store=AuditStore(db),
        price_source=price_source,
        solar_forecast=MockSolarForecastSource(_AMS, clock=clock.now_utc) if with_prices else None,
        controller=controller,
    )


def _widen_caps(client: TestClient) -> None:
    saved = client.post("/api/settings", json={
        "planner.max_daily_grid_charge_kwh": 40,
        "planner.max_cycles_per_day": 5,
        "planner.daily_min_savings_eur": 0,
    })
    assert saved.status_code == 200


def test_must_charge_post_plans_cheapest_slots_before_deadline(tmp_path):
    clock = _StepClock(_WHEN)
    app = _must_app(tmp_path, clock=clock)
    with TestClient(app) as c:
        _widen_caps(c)
        svc = app.state.control_service
        assert c.get("/api/plan-detail").status_code == 200
        builds = svc.plan_builds
        key_before = svc._ctx.plan_cache["key"]
        created = c.post("/api/override/must-charge", json={"target_soc": 80, "deadline": "07:00"})
        assert created.status_code == 200
        body = created.json()
        assert body["active"] is True
        assert body["target_soc"] == 80.0
        assert body["deadline"] == "2026-01-16T06:00:00+00:00"
        assert body["created"] == "2026-01-15T22:00:00+00:00"
        assert body["expires"] == "2026-01-16T06:00:00+00:00"
        assert body["deadline_hhmm"] == "07:00"
        assert body["seconds_remaining"] == 8 * 3600
        assert body["execution"] == WATCH_ONLY_TEXT
        assert body["watch_only"] is True
        assert "HH:MM" in body["deadline_format"]
        assert svc.plan_builds == builds + 1
        assert svc._ctx.plan_cache["key"] != key_before
        assert svc._ctx.plan_cache["key"][2] == (
            "active", 80.0, "2026-01-16T06:00:00+00:00", "2026-01-15T22:00:00+00:00",
        )
        detail = c.get("/api/plan-detail").json()
        assert svc.plan_builds == builds + 1  # same override token: cache hit
        resident = [s for s in detail["slots"] if "vul tot 80%" in (s.get("reason") or "")]
        assert [s["start"] for s in resident] == list(_CHEAP_SLOTS)
        assert [s["eur_per_kwh"] for s in resident] == [0.08, 0.08, 0.08]
        assert {s["intent"] for s in resident} == {"grid_charge_to_target"}
        # ISO-8601 with offset is the same instant as HH:MM 07:00.
        iso = c.post("/api/override/must-charge", json={
            "target_soc": 80, "deadline": "2026-01-16T07:00:00+01:00",
        })
        assert iso.status_code == 200
        assert iso.json()["deadline"] == "2026-01-16T06:00:00+00:00"


def test_must_charge_validation_and_no_prices(tmp_path):
    clock = _StepClock(_WHEN)
    with TestClient(_must_app(tmp_path, clock=clock)) as c:
        low = c.post("/api/override/must-charge", json={"target_soc": 14, "deadline": "07:00"})
        assert low.status_code == 422
        assert low.json()["errors"]["target_soc"] == "must be between 15 and 100"
        high = c.post("/api/override/must-charge", json={"target_soc": 101, "deadline": "07:00"})
        assert high.status_code == 422
        assert "target_soc" in high.json()["errors"]
        flag = c.post("/api/override/must-charge", json={"target_soc": True, "deadline": "07:00"})
        assert flag.status_code == 422
        assert flag.json()["errors"]["target_soc"] == "must be a number"
        past = c.post("/api/override/must-charge", json={
            "target_soc": 80, "deadline": "2026-01-15T21:00:00+00:00",
        })
        assert past.status_code == 422
        assert past.json()["errors"]["deadline"] == (
            "must be in the future and at most 24 hours ahead"
        )
        far = c.post("/api/override/must-charge", json={
            "target_soc": 80, "deadline": "2026-01-16T22:30:00+00:00",
        })
        assert far.status_code == 422
        assert far.json()["errors"]["deadline"] == (
            "must be in the future and at most 24 hours ahead"
        )
        naive = c.post("/api/override/must-charge", json={
            "target_soc": 80, "deadline": "2026-01-16T07:00:00",
        })
        assert naive.status_code == 422
        assert "HH:MM" in naive.json()["errors"]["deadline"]
        assert c.get("/api/override/must-charge").json()["active"] is False
    bare = create_app(
        MockSource(), dry_run=True, dev_mode="mock", tz=_AMS, clock=clock,
        override_store=SettingsStore(str(tmp_path / "bare.sqlite"), table="runtime_state"),
    )
    with TestClient(bare) as c:
        refused = c.post("/api/override/must-charge", json={"target_soc": 80, "deadline": "07:00"})
        assert refused.status_code == 422
        assert refused.json()["detail"] == "geen sturing"
        assert c.get("/api/override/must-charge").json()["active"] is False


def test_must_charge_expires_on_deadline_and_when_target_reached(tmp_path):
    clock = _StepClock(_WHEN)
    app = _must_app(tmp_path, clock=clock)
    with TestClient(app) as c:
        _widen_caps(c)
        assert c.post(
            "/api/override/must-charge", json={"target_soc": 80, "deadline": "07:00"},
        ).status_code == 200
        clock.instant = datetime(2026, 1, 16, 6, 0, tzinfo=UTC)
        ended = c.get("/api/override/must-charge").json()
        assert ended["active"] is False
        detail = c.get("/api/plan-detail").json()
        assert [s for s in detail["slots"] if "vul tot" in (s.get("reason") or "")] == []
        rows = c.get("/api/audit", params={"category": AUDIT_END}).json()["entries"]
        assert [row["category"] for row in rows] == [AUDIT_END]
        assert rows[0]["detail"]["cause"] == "deadline"
        assert rows[0]["detail"]["action"] == "end"

    clock = _StepClock(_WHEN)
    (tmp_path / "reached").mkdir()
    app = _must_app(tmp_path / "reached", clock=clock)
    with TestClient(app) as c:
        _widen_caps(c)
        assert c.post(
            "/api/override/must-charge", json={"target_soc": 80, "deadline": "07:00"},
        ).status_code == 200
        svc = app.state.control_service
        now = clock.now_utc()
        svc.current_sample(now)
        cached = svc._ctx.sample_cache["sample"]
        svc._ctx.sample_cache["sample"] = replace(cached, soc_pct=80.0)
        svc._ctx.sample_cache["soc_at"] = now
        ended = c.get("/api/override/must-charge").json()
        assert ended["active"] is False
        detail = c.get("/api/plan-detail").json()
        assert [s for s in detail["slots"] if "vul tot" in (s.get("reason") or "")] == []
        rows = c.get("/api/audit", params={"category": AUDIT_END}).json()["entries"]
        assert rows[0]["category"] == AUDIT_END
        assert rows[0]["detail"]["cause"] == "target"


def test_must_charge_cancel_audits_and_clears_the_plan_cache(tmp_path):
    clock = _StepClock(_WHEN)
    app = _must_app(tmp_path, clock=clock)
    with TestClient(app) as c:
        _widen_caps(c)
        assert c.post(
            "/api/override/must-charge", json={"target_soc": 80, "deadline": "07:00"},
        ).status_code == 200
        svc = app.state.control_service
        builds = svc.plan_builds
        cancelled = c.delete("/api/override/must-charge")
        assert cancelled.status_code == 200
        assert cancelled.json()["active"] is False
        detail = c.get("/api/plan-detail").json()
        assert [s for s in detail["slots"] if "vul tot" in (s.get("reason") or "")] == []
        assert svc.plan_builds == builds + 1
        assert svc._ctx.plan_cache["key"][2] == (None,)
        cats = [row["category"] for row in c.get("/api/audit").json()["entries"]]
        assert AUDIT_START in cats
        assert AUDIT_CANCEL in cats
        assert cats.count(AUDIT_CANCEL) == 1


def test_must_charge_dry_run_does_not_write_the_battery(tmp_path):
    clock = _StepClock(_WHEN)
    driver = MockBatteryDriver()
    writes = {"n": 0}
    original = driver.apply

    def _count(mode, *, target_soc=None, power_w=None):
        writes["n"] += 1
        return original(mode, target_soc=target_soc, power_w=power_w)

    driver.apply = _count
    controller = ModeController(driver, Lifecycle(dry_run=True), dry_run=True)
    app = _must_app(tmp_path, clock=clock, controller=controller)
    with TestClient(app) as c:
        _widen_caps(c)
        created = c.post("/api/override/must-charge", json={"target_soc": 80, "deadline": "07:00"})
        assert created.status_code == 200
        assert created.json()["execution"] == "alleen kijken — niet uitgevoerd"
        app.state.control_service.control_tick(clock.now_utc())
    assert writes["n"] == 0


def test_must_charge_api_reports_the_daily_ceiling(tmp_path):
    """A target far above one slot still plans at most the daily AC ceiling, and says why."""
    clock = _StepClock(_WHEN)
    app = _must_app(tmp_path, clock=clock)
    with TestClient(app) as c:
        saved = c.post("/api/settings", json={
            "planner.max_daily_grid_charge_kwh": 1,
            "planner.max_cycles_per_day": 5,
            "planner.daily_min_savings_eur": 0,
            "planner.must_charge_max_price_eur_per_kwh": 0.30,
        })
        assert saved.status_code == 200
        created = c.post("/api/override/must-charge", json={"target_soc": 100, "deadline": "07:00"})
        assert created.status_code == 200
        body = created.json()
        assert body["active"] is True
        assert body["unreachable_reason"] == "Dagplafond 1 kWh bereikt"
        assert body["warning"] == "Dagplafond 1 kWh bereikt"
        detail = c.get("/api/plan-detail").json()
        by_day: dict[str, float] = {}
        for slot in detail["slots"]:
            if slot["intent"] != "grid_charge_to_target":
                continue
            day = slot["start"][:10]
            by_day[day] = by_day.get(day, 0.0) + 1.0
        # 55% → 100% fits in the €0.08 band (04:30–05:45 local). The 1 kWh ceiling
        # keeps one slot on 16 Jan; 15 Jan's €0.12 shoulder is not in that pack.
        assert by_day == {"2026-01-16": 1.0}


def test_reader_cannot_create_or_cancel_must_charge(tmp_path):
    from ems.authn import hash_password
    from ems.storage.auth import AuthStore

    db = str(tmp_path / "auth.sqlite")
    store = AuthStore(db)

    async def seed():
        await store.init()
        await store.create_user("rdr", hash_password("pw12345678"), "reader")
        await store.close()

    asyncio.run(seed())
    app = create_app(
        MockSource(), dry_run=True, dev_mode="mock", tz=_AMS, clock=_StepClock(_WHEN),
        auth_store=AuthStore(db),
        override_store=SettingsStore(db, table="runtime_state"),
    )
    with TestClient(app) as c:
        login = c.post("/api/auth/login", json={"username": "rdr", "password": "pw12345678"})
        assert login.status_code == 200
        headers = {"Authorization": f"Bearer {login.json()['token']}"}
        assert c.get("/api/override/must-charge", headers=headers).status_code == 200
        created = c.post(
            "/api/override/must-charge",
            json={"target_soc": 80, "deadline": "07:00"},
            headers=headers,
        )
        assert created.status_code == 403
        assert c.delete("/api/override/must-charge", headers=headers).status_code == 403
