"""Service ↔ API coupling for the daily grid-charge counter (#317).

`ControlService.plan_with_recovery` (service.py already-bought stamp) and the validator
closure in `api.py` must see the same history total. Mocks only — no live battery.
"""
from __future__ import annotations

import sqlite3
from dataclasses import replace
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from ems.clock import FrozenClock
from ems.domain import BatteryIntent
from ems.planner.budgets import planned_grid_charge_kwh
from ems.planner.schedule import SLOT, Plan, PlanSlot
from ems.sources.forecast import MockSolarForecastSource
from ems.sources.mock import MockSource
from ems.sources.prices import MockPriceSource
from ems.storage.history import HistoryStore
from ems.storage.settings import SettingsStore
from ems.web.api import create_app

AMS = ZoneInfo("Europe/Amsterdam")
# 02:00: the eight quarter-hours since midnight are already complete, and the cheap
# window until 06:00 is still today — so the cap limits today's new charge, not tomorrow's.
WHEN = datetime(2026, 1, 15, 2, 0, tzinfo=AMS)


class _LowSoc(MockSource):
    def read(self):
        return replace(super().read(), soc_pct=5.0)


def _charge(start: datetime) -> PlanSlot:
    return PlanSlot(
        start, BatteryIntent.GRID_CHARGE_TO_TARGET, "charge",
        target_soc=80.0, power_w=4000.0, floor_soc=10.0,
    )


def _today_kwh(plan, day) -> float:
    return planned_grid_charge_kwh(plan, 1.0, tz=AMS, day=day)


def _insert_eight_kwh(db_path: str) -> None:
    utc = ZoneInfo("UTC")
    midnight = WHEN.replace(hour=0, minute=0, second=0, microsecond=0)
    rows = []
    for i in range(8):
        ts = (midnight + timedelta(minutes=15 * i)).astimezone(utc).isoformat()
        rows.append((ts, 4000.0, 0.0, -4000.0, 0.0, 40.0))
    conn = sqlite3.connect(db_path)
    conn.executemany(
        "INSERT INTO raw_samples (ts, grid_power_w, solar_power_w, battery_power_w, "
        "ev_power_w, soc_pct) VALUES (?, ?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    conn.close()


def _app(tmp_path):
    clock = FrozenClock(WHEN)
    db = str(tmp_path / "ems.sqlite")
    app = create_app(
        _LowSoc(),
        dry_run=True,
        dev_mode="mock",
        tz=AMS,
        store=HistoryStore(db),
        price_source=MockPriceSource(AMS, clock=clock.now_utc),
        solar_forecast=MockSolarForecastSource(AMS, kwp=0.0, clock=clock.now_utc),
        settings_store=SettingsStore(db),
        clock=clock,
    )
    return app, db


def test_history_of_8_kwh_limits_plan_and_api_to_4(tmp_path):
    """8 kWh already bought, cap 12 → plan_with_recovery and the API add at most 4 kWh."""
    app, db = _app(tmp_path)
    day = WHEN.date()
    with TestClient(app) as client:
        svc = app.state.control_service
        svc._settings["strategy.mode"] = "winter"
        svc._settings["battery.overnight_load_kwh"] = 18.0
        svc._settings["planner.max_daily_grid_charge_kwh"] = 12.0
        svc._settings["planner.round_trip_efficiency"] = 0.9

        open_plan = svc.plan_with_recovery(WHEN)
        assert open_plan is not None
        open_kwh = _today_kwh(open_plan[2], day)
        assert open_kwh > 4.0  # otherwise "at most 4" would not be exercising the cap

        _insert_eight_kwh(db)
        svc.invalidate_cycle_caches()
        limited = svc.plan_with_recovery(WHEN)
        assert limited is not None
        plan = limited[2]
        assert _today_kwh(plan, day) <= 4.0 + 1e-6
        assert _today_kwh(plan, day) < open_kwh

        detail = client.get("/api/plan-detail")
        assert detail.status_code == 200
        body = detail.json()
        api_today = [
            s for s in body["slots"]
            if s["intent"] in ("grid_charge_to_target", "GRID_CHARGE_TO_TARGET")
            and datetime.fromisoformat(s["start"]).astimezone(AMS).date() == day
        ]
        assert len(api_today) <= 4
        plan_today = [
            s for s in plan.slots
            if s.intent is BatteryIntent.GRID_CHARGE_TO_TARGET
            and s.start.astimezone(AMS).date() == day
        ]
        assert len(api_today) == len(plan_today)

        decision = client.get("/api/decision")
        assert decision.status_code == 200
        assert svc.already_bought_today_kwh(WHEN) == 8.0

        # Validator closure (api.py) reads the same counter. Unknown capability clamps each
        # slot to one tower (2400 W → 0.6 kWh), so the over-cap plan needs enough slots that
        # 8 kWh already bought plus the new AC energy still exceeds 12 either before or after
        # that clamp. Four full-power slots (4 kWh) stay inside the remaining allowance.
        over = Plan(created_at=WHEN, slots=tuple(_charge(WHEN + i * SLOT) for i in range(9)),
                    strategy="winter")
        over_val = svc._validate_plan_obj(over, WHEN)
        assert any(f.code == "daily_grid_charge_cap" for f in over_val.findings)
        fits = Plan(created_at=WHEN, slots=tuple(_charge(WHEN + i * SLOT) for i in range(4)),
                    strategy="winter")
        fit_val = svc._validate_plan_obj(fits, WHEN)
        assert not any(f.code == "daily_grid_charge_cap" for f in fit_val.findings)


def test_none_counter_at_service_blocks_grid_charge(tmp_path):
    """A missing database is None at the service: today's plan does not grid-charge."""
    app, _db = _app(tmp_path)
    day = WHEN.date()
    with TestClient(app) as client:
        svc = app.state.control_service
        svc._settings["strategy.mode"] = "winter"
        svc._settings["battery.overnight_load_kwh"] = 18.0
        svc._settings["planner.max_daily_grid_charge_kwh"] = 12.0
        known = svc.plan_with_recovery(WHEN)
        assert known is not None and _today_kwh(known[2], day) > 0.0

        svc._store.db_path = str(tmp_path / "missing.sqlite")
        svc.invalidate_cycle_caches()
        blocked = svc.plan_with_recovery(WHEN)
        assert blocked is not None
        assert _today_kwh(blocked[2], day) == 0.0
        assert any(
            s.reason == "Dagteller onbekend: niet laden van het net" for s in blocked[2].slots
        )
        assert any(
            s.intent is not BatteryIntent.GRID_CHARGE_TO_TARGET for s in blocked[2].slots
        )

        detail = client.get("/api/plan-detail")
        assert detail.status_code == 200
        assert not any(
            s["intent"] in ("grid_charge_to_target", "GRID_CHARGE_TO_TARGET")
            and datetime.fromisoformat(s["start"]).astimezone(AMS).date() == day
            for s in detail.json()["slots"]
        )
        flagged = svc._validate_plan_obj(
            Plan(created_at=WHEN, slots=(_charge(WHEN),), strategy="winter"), WHEN,
        )
        finding = next(f for f in flagged.findings if f.code == "daily_grid_charge_cap")
        assert "Dagteller onbekend: niet laden van het net" in finding.message
