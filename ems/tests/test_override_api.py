import asyncio
import logging
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from ems.control.mode_controller import ModeController
from ems.freshness import FreshnessTracker
from ems.lifecycle import Lifecycle
from ems.sense import SIGNALS
from ems.sources.battery import MockBatteryDriver
from ems.sources.mock import MockSource
from ems.sources.prices import MockPriceSource
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
    return create_app(
        MockSource(), dry_run=True, dev_mode="mock",
        override_store=SettingsStore(str(tmp_path / "ems.sqlite"), table="runtime_state"),
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
