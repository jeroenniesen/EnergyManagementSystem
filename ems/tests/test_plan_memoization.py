"""B-48 / #87: plan + forward-projection memoization.

A dashboard poll fans out to many endpoints (battery-plan, energy-story, decision, diagnostics…).
Without memoization each call rebuilt `plan_with_recovery` / `_forward_projection` (~6–16× per
poll). These tests pin the fix: one build per quantized coalesce window, shared across endpoints
and concurrent clients. Mocks only — no real Indevolt / HomeWizard / Tibber.
"""
from __future__ import annotations

import asyncio
from zoneinfo import ZoneInfo

import httpx
from fastapi.testclient import TestClient

from ems.control import service as control_svc
from ems.sources.forecast import MockSolarForecastSource
from ems.sources.mock import MockSource
from ems.sources.prices import MockPriceSource
from ems.storage.history import HistoryStore
from ems.storage.settings import SettingsStore
from ems.web.api import create_app

AMS = ZoneInfo("Europe/Amsterdam")

# Endpoints that used to each rebuild the plan / projection on a typical poll.
_HOT_PLAN_PATHS = (
    "/api/energy-story",
    "/api/battery-plan",
    "/api/decision",
    "/api/energy-forecast",
    "/api/plan-detail",
    "/api/diagnostics",
    "/api/alerts",
)


def _app(tmp_path, *, live_read_seconds: float = 60.0):
    db = str(tmp_path / "ems.sqlite")
    app = create_app(
        MockSource(),
        dry_run=True,
        dev_mode="mock",
        tz=AMS,
        store=HistoryStore(db),
        price_source=MockPriceSource(AMS),
        solar_forecast=MockSolarForecastSource(AMS),
        settings_store=SettingsStore(db),
    )
    # Force a known coalesce window so quantized keys are stable in tests.
    app.state.control_service._settings["control.live_read_seconds"] = live_read_seconds
    return app


def test_hot_endpoints_share_one_plan_build_per_cycle(tmp_path):
    """One dashboard-like poll → ≤1 plan_with_recovery compute (cache miss)."""
    app = _app(tmp_path)
    builds_before = app.state.control_service.plan_builds

    with TestClient(app) as client:
        for path in _HOT_PLAN_PATHS:
            assert client.get(path).status_code == 200

    builds = app.state.control_service.plan_builds - builds_before
    assert builds <= 1, f"expected ≤1 plan build per cycle, got {builds}"


def test_hot_endpoints_share_one_forward_projection_per_cycle(tmp_path):
    """energy-story + battery-plan + forecast + decision share one forward bundle."""
    app = _app(tmp_path)
    before = app.state.control_service.forward_builds

    with TestClient(app) as client:
        for path in (
            "/api/energy-story",
            "/api/battery-plan",
            "/api/energy-forecast",
            "/api/decision",
        ):
            assert client.get(path).status_code == 200

    builds = app.state.control_service.forward_builds - before
    assert builds <= 1, f"expected ≤1 forward projection per cycle, got {builds}"


def test_plan_build_counter_increments_only_on_cache_miss(tmp_path):
    app = _app(tmp_path, live_read_seconds=60.0)
    svc = app.state.control_service

    with TestClient(app) as client:
        assert client.get("/api/plan-detail").status_code == 200
        after_first = svc.plan_builds
        assert after_first >= 1

        # Same quantized window → no extra build.
        assert client.get("/api/plan-detail").status_code == 200
        assert client.get("/api/alerts").status_code == 200
        assert svc.plan_builds == after_first


def test_decision_reason_reuses_plan_with_recovery_path(tmp_path, monkeypatch):
    """Explain/DecisionReason must go through plan_with_recovery (not a parallel rebuild)."""
    calls = {"n": 0}
    orig = control_svc.ControlService.plan_with_recovery

    def _counting(self, now=None):
        calls["n"] += 1
        return orig(self, now)

    monkeypatch.setattr(control_svc.ControlService, "plan_with_recovery", _counting)

    app = _app(tmp_path)
    before = app.state.control_service.plan_builds
    with TestClient(app) as client:
        body = client.get("/api/battery-plan").json()

    assert calls["n"] >= 1, "battery-plan must use plan_with_recovery"
    assert "reason" in body
    # Cache misses (actual builds) ≤1 even though the method is entered multiple times.
    builds = app.state.control_service.plan_builds - before
    assert builds <= 1, f"reason must reuse memoized plan; got {builds} builds"


def test_settings_change_invalidates_plan_cache(tmp_path):
    app = _app(tmp_path)
    svc = app.state.control_service

    # Lifespan opens the settings SQLite schema (same as test_settings_api).
    with TestClient(app) as client:
        assert client.get("/api/plan-detail").status_code == 200
        mid = svc.plan_builds
        assert mid >= 1

        resp = client.post(
            "/api/settings",
            json={"planner.risk_margin_eur_per_kwh": 0.03},
        )
        assert resp.status_code == 200, resp.text

        assert client.get("/api/plan-detail").status_code == 200
        assert svc.plan_builds == mid + 1


def test_quantize_now_floors_to_coalesce_window(tmp_path):
    from datetime import UTC, datetime, timedelta

    app = _app(tmp_path, live_read_seconds=30.0)
    svc = app.state.control_service
    t0 = datetime(2026, 6, 15, 12, 0, 7, tzinfo=UTC)
    t1 = t0 + timedelta(seconds=20)
    t2 = t0 + timedelta(seconds=35)
    assert svc.quantize_now(t0) == svc.quantize_now(t1)
    assert svc.quantize_now(t0) != svc.quantize_now(t2)


def test_concurrent_poll_single_flight_plan_build(tmp_path):
    """Many concurrent clients in one coalesce window → one plan compute."""
    app = _app(tmp_path)
    svc = app.state.control_service

    async def go():
        # Lifespan so store schema exists for forward_projection history reads.
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
                before = svc.plan_builds
                results = await asyncio.gather(
                    *[c.get("/api/battery-plan") for _ in range(12)],
                    *[c.get("/api/energy-story") for _ in range(8)],
                )
                return before, results

    before, results = asyncio.run(go())
    assert all(r.status_code == 200 for r in results)
    builds = svc.plan_builds - before
    assert builds <= 1, f"single-flight failed: {builds} plan builds under concurrent load"
