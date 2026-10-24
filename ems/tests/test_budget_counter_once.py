"""#361: daily grid-charge counter is read once per coalesce window in the control path."""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from ems.clock import FrozenClock
from ems.control import service as control_svc
from ems.domain import BatteryIntent
from ems.planner.budgets import read_already_bought_today
from ems.planner.schedule import Plan, PlanSlot
from ems.sources.forecast import MockSolarForecastSource
from ems.sources.mock import MockSource
from ems.sources.prices import MockPriceSource
from ems.storage.history import HistoryStore
from ems.storage.settings import SettingsStore
from ems.web.api import create_app

AMS = ZoneInfo("Europe/Amsterdam")
WHEN = datetime(2026, 1, 15, 2, 0, tzinfo=AMS)


def _app(tmp_path, *, live_read_seconds: float = 60.0):
    clock = FrozenClock(WHEN)
    db = str(tmp_path / "ems.sqlite")
    app = create_app(
        MockSource(),
        dry_run=True,
        dev_mode="mock",
        tz=AMS,
        store=HistoryStore(db),
        price_source=MockPriceSource(AMS, clock=clock.now_utc),
        solar_forecast=MockSolarForecastSource(AMS, kwp=0.0, clock=clock.now_utc),
        settings_store=SettingsStore(db),
        clock=clock,
    )
    app.state.control_service._settings["control.live_read_seconds"] = live_read_seconds
    return app


def test_one_db_read_per_plan_cycle_through_validator(tmp_path, monkeypatch):
    """build_plan_now + recovery + injected validate_plan_obj share one counter read."""
    app = _app(tmp_path)
    svc = app.state.control_service
    real = read_already_bought_today
    calls = {"n": 0}

    def _counting(*args, **kwargs):
        calls["n"] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(control_svc, "read_already_bought_today", _counting)

    result = svc.plan_with_recovery(WHEN)
    assert result is not None
    plan = result[2]
    svc._validate_plan_obj(plan, WHEN)
    assert calls["n"] == 1


def test_new_coalesce_window_triggers_new_counter_read(tmp_path, monkeypatch):
    app = _app(tmp_path, live_read_seconds=30.0)
    svc = app.state.control_service
    real = read_already_bought_today
    calls = {"n": 0}

    def _counting(*args, **kwargs):
        calls["n"] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(control_svc, "read_already_bought_today", _counting)
    t0 = svc.quantize_now(WHEN)

    assert svc.plan_with_recovery(now=t0) is not None
    assert calls["n"] == 1

    assert svc.plan_with_recovery(now=t0 + timedelta(seconds=35)) is not None
    assert calls["n"] == 2


def test_unknown_counter_passes_none_to_recovery_and_validator(tmp_path, monkeypatch):
    app = _app(tmp_path)
    svc = app.state.control_service
    svc._store.db_path = str(tmp_path / "missing.sqlite")
    seen: dict[str, object] = {}
    real_recover = control_svc.recover_if_needed

    def _capture(*args, **kwargs):
        seen["already_bought_today_kwh"] = kwargs.get("already_bought_today_kwh")
        return real_recover(*args, **kwargs)

    monkeypatch.setattr(control_svc, "recover_if_needed", _capture)
    monkeypatch.setattr(
        control_svc, "read_already_bought_today", lambda *a, **k: None,
    )

    result = svc.plan_with_recovery(WHEN)
    assert result is not None
    assert seen.get("already_bought_today_kwh") is None

    over = Plan(
        created_at=WHEN,
        slots=(
            PlanSlot(
                WHEN, BatteryIntent.GRID_CHARGE_TO_TARGET, "charge",
                target_soc=80.0, power_w=4000.0, floor_soc=10.0,
            ),
        ),
        strategy="winter",
    )
    val = svc._validate_plan_obj(over, WHEN)
    assert any(f.code == "daily_grid_charge_cap" for f in val.findings)
