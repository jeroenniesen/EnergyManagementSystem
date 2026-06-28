"""The strategy selector wired through the API: /api/strategy + the chosen strategy driving the
plan. Mock sources only."""
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from ems.sources.forecast import MockSolarForecastSource
from ems.sources.mock import MockSource
from ems.sources.prices import MockPriceSource
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
    # Winter arbitrage charges the cheap window (GRID_CHARGE); summer solar-first (mock sun) does
    # not force a winter-style charge. The two strategies must produce different plans.
    with TestClient(_app(tmp_path)) as c:
        c.post("/api/settings", json={"strategy.mode": "winter"})
        winter = c.get("/api/plan").json()
        c.post("/api/settings", json={"strategy.mode": "summer"})
        summer = c.get("/api/plan").json()
    winter_intents = {s["intent"] for s in winter["slots"]}
    summer_intents = [s["intent"] for s in summer["slots"]]
    assert "grid_charge_to_target" in winter_intents
    # Summer with the mock midday sun fills from solar -> mostly self-consumption, not a winter
    # charge-the-cheap-window plan.
    assert summer_intents != [s["intent"] for s in winter["slots"]]


def test_plan_detail_includes_active_strategy(tmp_path):
    with TestClient(_app(tmp_path)) as c:
        c.post("/api/settings", json={"strategy.mode": "winter"})
        b = c.get("/api/plan-detail").json()
    assert b["strategy"] == "winter"
