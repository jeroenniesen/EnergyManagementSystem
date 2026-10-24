"""#330: /api/decision passes Plan.solar_basis through, read-only."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from ems.consumers.start_advice import accepted_windows_revision
from ems.control.mode_controller import ModeController
from ems.lifecycle import Lifecycle
from ems.sources.battery import MockBatteryDriver
from ems.sources.forecast import MockSolarForecastSource
from ems.sources.mock import MockSource
from ems.sources.prices import MockPriceSource
from ems.web.api import create_app

AMS = ZoneInfo("Europe/Amsterdam")


class _SolcastShapedForecast(MockSolarForecastSource):
    """Registry name solcast: capability p10 and not synthetic."""

    provider = "solcast"
    source_label = "solcast"


class _PlanWithoutSolarBasis:
    """Plan-shaped object that never grew a solar_basis attribute."""

    slots: tuple = ()

    def intent_at(self, _now: datetime):
        return None


def _app(*, forecast, prices: bool = True, controller: bool = True):
    battery = MockBatteryDriver()
    ctrl = None
    if controller:
        ctrl = ModeController(battery, Lifecycle(dry_run=True), dry_run=True)
    return create_app(
        MockSource(),
        dry_run=True,
        dev_mode="mock",
        tz=AMS,
        price_source=MockPriceSource(AMS) if prices else None,
        solar_forecast=forecast,
        controller=ctrl,
    )


def _summer(app, basis: str = "auto"):
    settings = app.state.control_service._settings
    settings["strategy.mode"] = "summer"
    settings["planner.commit_solar_basis"] = basis
    return app


def _body(app) -> dict:
    with TestClient(app) as client:
        response = client.get("/api/decision")
    assert response.status_code == 200
    return response.json()


def test_decision_endpoint_includes_solar_basis_when_the_plan_has_it():
    """Real P10 → p10; explicit p50 → p50; synthetic bands → null. Same field as the plan."""
    p10_app = _summer(_app(forecast=_SolcastShapedForecast(AMS)))
    p10 = _body(p10_app)
    p10_plan = p10_app.state.control_service.current_plan()[2]
    assert p10_plan.solar_basis == "p10"
    assert p10["solar_basis"] == "p10"
    assert p10["outcome"] == "dry_run"

    named = _summer(_app(forecast=_SolcastShapedForecast(AMS)), "p50")
    named_body = _body(named)
    assert named.state.control_service.current_plan()[2].solar_basis == "p50"
    assert named_body["solar_basis"] == "p50"

    synthetic = _summer(_app(forecast=MockSolarForecastSource(AMS)))
    synthetic_body = _body(synthetic)
    assert synthetic.state.control_service.current_plan()[2].solar_basis is None
    assert synthetic_body["solar_basis"] is None

    bare = _summer(_app(forecast=_SolcastShapedForecast(AMS), controller=False))
    bare_body = _body(bare)
    assert bare.state.control_service.current_plan()[2].solar_basis == "p10"
    assert bare_body["outcome"] == "unconfigured"
    assert bare_body["solar_basis"] == "p10"


def test_decision_endpoint_solar_basis_null_when_the_plan_field_is_missing():
    """A plan without solar_basis, and a missing plan, both yield JSON null."""
    app = _app(forecast=MockSolarForecastSource(AMS), prices=False)
    svc = app.state.control_service
    svc._settings["control.live_read_seconds"] = 10**9
    planted = _PlanWithoutSolarBasis()
    now = svc._clock.now_utc()
    svc._ctx.plan_cache["result"] = (now, (), planted, None, None)
    svc._ctx.plan_cache["at"] = now
    svc._ctx.plan_cache["key"] = (
        svc.quantize_now(now),
        accepted_windows_revision(svc._cache_store, svc._clock),
        svc._resident_override_token(now),
    )
    body = _body(app)
    assert svc.current_plan()[2] is planted
    assert not hasattr(planted, "solar_basis")
    assert body["outcome"] == "no_plan"
    assert body["solar_basis"] is None

    absent = _body(_app(forecast=None, prices=False))
    assert absent["outcome"] == "no_plan"
    assert absent["solar_basis"] is None
