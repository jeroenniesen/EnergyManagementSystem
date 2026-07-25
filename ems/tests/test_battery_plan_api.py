"""/api/battery-plan: homeowner-facing confidence contract."""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from ems.domain import RawSample
from ems.freshness import FreshnessTracker
from ems.load_model import reconstruct
from ems.sense import SIGNALS
from ems.sources.forecast import MockSolarForecastSource
from ems.sources.mock import MockSource
from ems.sources.prices import MockPriceSource
from ems.storage.history import HistoryStore
from ems.storage.settings import SettingsStore
from ems.web.api import create_app

AMS = ZoneInfo("Europe/Amsterdam")
UNIFIED_ACTIONS = {"grid_charge", "solar_charge", "discharge", "hold", "self_consume", "paused"}

_DEFAULT_FRESHNESS = object()


def _fresh_tracker():
    now = datetime.now(UTC)
    fresh = FreshnessTracker()
    fresh.register(*SIGNALS)
    for signal in SIGNALS:
        fresh.mark(signal, now)
    return fresh


def _app(tmp_path, *, source=None, freshness=_DEFAULT_FRESHNESS, store=None,
         with_forecast=True, token=None, solar_forecast=None):
    db = str(tmp_path / "ems.sqlite")
    store = store or HistoryStore(db)
    if freshness is _DEFAULT_FRESHNESS:
        freshness = _fresh_tracker()
    if solar_forecast is None:
        solar_forecast = MockSolarForecastSource(AMS) if with_forecast else None
    return create_app(
        source or MockSource(), dry_run=True, dev_mode="mock", tz=AMS,
        store=store, freshness=freshness,
        price_source=MockPriceSource(AMS),
        solar_forecast=solar_forecast,
        settings_store=SettingsStore(db),
        web_auth_token=token,
    )


def _seed(store, *, soc: float, minutes=(60, 45, 30, 15)):
    """Seed a few recent recorded samples ending near now at the given SoC."""
    async def run():
        await store.init()
        now = datetime.now(UTC)
        for i in minutes:
            ts = now - timedelta(minutes=i)
            raw = RawSample(200.0, 0.0, 800.0, 0.0, soc)
            await store.record(ts.isoformat(), raw, reconstruct(raw))
    asyncio.run(run())


def test_battery_plan_contract_answers_the_homeowner_questions(tmp_path):
    with TestClient(_app(tmp_path)) as c:
        body = c.get("/api/battery-plan").json()

    assert body["status"] in {"on_track", "needs_topup", "behind_target",
                              "paused_safely", "data_stale"}
    assert body["summary"]
    assert body["current_action"] in UNIFIED_ACTIONS
    assert body["current_reason"]
    assert body["window_start"] < body["window_end"]
    assert body["reserve_soc_pct"] == 10.0
    assert body["target_soc_pct"] is not None
    assert body["planned_grid_topup_kwh"] >= 0.0
    assert body["deviation"]["status"] in {"ok", "behind_forecast", "missing"}

    graph = body["graph"]
    assert graph["forecast_soc"]
    assert graph["reserve_line"]
    assert graph["target_line"]
    assert graph["planned_actions"]
    assert graph["solar"]
    first = graph["forecast_soc"][0]
    assert set(first) == {"ts", "soc_pct"}

    # price_windows highlight where the planner ACTUALLY grid-charges — each must line up with a
    # grid_charge action block and carry sane bounds (they may be empty on a solar-only day).
    charge_spans = {(b["start"], b["end"]) for b in graph["planned_actions"]
                    if b["action"] == "grid_charge"}
    for w in graph["price_windows"]:
        assert w["min_eur_per_kwh"] <= w["max_eur_per_kwh"]
        assert any(b0 <= w["start"] and w["end"] <= b1 for b0, b1 in charge_spans)


def test_current_action_uses_the_same_vocabulary_as_the_action_strip(tmp_path):
    # current_action is the first planned slot's action, so the chip and the graph never disagree.
    with TestClient(_app(tmp_path)) as c:
        body = c.get("/api/battery-plan").json()
    blocks = body["graph"]["planned_actions"]
    if body["status"] not in {"data_stale", "paused_safely"} and blocks:
        assert body["current_action"] == blocks[0]["action"]


def test_deviation_is_plan_derived_not_two_actuals(tmp_path):
    # Regression for the reviewed bug: deviation used to be (live SoC − last recorded SoC). Here we
    # seed a wildly different recent SoC (5%) while the live battery is full (95%); the OLD code
    # would flag 'behind_forecast' from that 90-pt gap. The verdict must instead track the PLAN
    # (a full battery is on track), independent of the recent-history sample.
    store = HistoryStore(str(tmp_path / "ems.sqlite"))
    _seed(store, soc=5.0)

    class FullSource(MockSource):
        def read(self):
            raw = super().read()
            return RawSample(raw.grid_power_w, raw.solar_power_w, raw.battery_power_w,
                             raw.ev_power_w, 95.0)

    with TestClient(_app(tmp_path, source=FullSource(), store=store)) as c:
        body = c.get("/api/battery-plan").json()

    assert body["status"] == "on_track"
    assert body["deviation"]["status"] == "ok"


def test_window_start_covers_the_recent_actual_history(tmp_path):
    # Regression for the off-axis actual line: the plotted window must span the recent actuals, not
    # start at 'now' — otherwise the actual-SoC series falls entirely left of the chart.
    store = HistoryStore(str(tmp_path / "ems.sqlite"))
    _seed(store, soc=60.0)

    with TestClient(_app(tmp_path, store=store)) as c:
        body = c.get("/api/battery-plan").json()

    actual = body["graph"]["actual_soc"]
    assert actual, "recent actuals should be present after seeding"
    assert body["window_start"] <= actual[0]["ts"]
    assert all(body["window_start"] <= p["ts"] <= body["window_end"] for p in actual)


def test_battery_plan_reports_data_stale_when_inputs_are_unsafe(tmp_path):
    fresh = FreshnessTracker()
    fresh.register(*SIGNALS)

    with TestClient(_app(tmp_path, freshness=fresh)) as c:
        body = c.get("/api/battery-plan").json()

    assert body["status"] == "data_stale"
    assert body["current_action"] == "paused"
    assert "stale" in body["summary"].lower() or "missing" in body["summary"].lower()
    assert body["warnings"]
    assert body["deviation"]["status"] == "missing"
    # B-68: unsafe data quality must cap the plan-confidence score at low, reason leading.
    assert body["confidence"]["level"] == "low"
    assert body["confidence"]["reasons"]
    assert "safety fallback" in body["confidence"]["reasons"][0].lower()


def test_battery_plan_pauses_safely_when_there_is_no_plan(tmp_path):
    # No solar forecast → no forward projection → the endpoint degrades to a safe, empty contract
    # rather than erroring.
    with TestClient(_app(tmp_path, with_forecast=False)) as c:
        body = c.get("/api/battery-plan").json()

    assert body["status"] == "paused_safely"
    assert body["current_action"] == "paused"
    assert body["deviation"]["status"] == "missing"
    assert body["graph"]["forecast_soc"] == []
    assert body["current_soc_pct"] is None
    # Even the safe, empty contract carries a confidence block (B-68) — never a missing key.
    assert body["confidence"]["level"] in {"high", "medium", "low"}
    assert body["confidence"]["reasons"]


def test_battery_plan_carries_a_confidence_block(tmp_path):
    # Fresh data, no forecast-skill history yet -> capped at medium ("still learning your roof"),
    # never silently missing and never falsely "high" on zero evidence.
    with TestClient(_app(tmp_path)) as c:
        body = c.get("/api/battery-plan").json()

    confidence = body["confidence"]
    assert confidence["level"] in {"high", "medium", "low"}
    assert confidence["reasons"]
    assert all(isinstance(r, str) and r for r in confidence["reasons"])
    assert len(confidence["reasons"]) <= 2


def test_battery_plan_read_is_open_on_lan_but_gated_by_require_auth(tmp_path):
    with TestClient(_app(tmp_path, token="s3cret")) as c:
        # Reads are open on the LAN by default even with a token set.
        assert c.get("/api/battery-plan").status_code == 200
        # Lock reads down for remote use → the plan read now needs the token.
        assert c.post("/api/settings", json={"web.require_auth": True},
                      headers={"Authorization": "Bearer s3cret"}).status_code == 200
        assert c.get("/api/battery-plan").status_code == 401
        assert c.get("/api/battery-plan",
                     headers={"Authorization": "Bearer s3cret"}).status_code == 200


# --- Plan provenance (feat/ux-batch-3): what's ACTUALLY steering the plan, never overstating the
# scenario/ML intelligence layer (ems/intelligence/planning.py) which is built but not wired in —
# CLAUDE.md honesty.


def test_battery_plan_carries_a_provenance_block_with_the_expected_shape(tmp_path):
    with TestClient(_app(tmp_path)) as c:
        body = c.get("/api/battery-plan").json()

    prov = body["provenance"]
    assert set(prov) == {"forecast_source", "solar_confidence_pct", "planner", "intelligence"}
    # MockSolarForecastSource (no live forecast wired in this test) humanizes to "Built-in model".
    assert prov["forecast_source"] == "Built-in model"
    assert prov["solar_confidence_pct"] == 80.0  # planner.solar_confidence default
    assert prov["planner"] in {"rule_based", "adaptive", "summer"}
    # The scenario/ML intelligence layer is built but not wired in — never claims to be active.
    # (B-79) Runtime-derived status object, not a bare string — see _intelligence_status.
    assert prov["intelligence"]["state"] == "not_active"
    assert prov["intelligence"]["last_evaluated_at"] is None


def test_battery_plan_provenance_reflects_the_solar_confidence_setting(tmp_path):
    with TestClient(_app(tmp_path)) as c:
        assert c.post("/api/settings", json={"planner.solar_confidence": 65}).status_code == 200
        body = c.get("/api/battery-plan").json()

    assert body["provenance"]["solar_confidence_pct"] == 65.0


def test_battery_plan_provenance_reflects_a_forced_winter_strategy(tmp_path):
    with TestClient(_app(tmp_path)) as c:
        assert c.post("/api/settings", json={"strategy.mode": "winter"}).status_code == 200
        body = c.get("/api/battery-plan").json()

    # Winter always dispatches to the rule-based arbitrage planner (ems.planner.strategy).
    assert body["provenance"]["planner"] == "rule_based"


def test_battery_plan_provenance_reflects_a_forced_summer_strategy(tmp_path):
    with TestClient(_app(tmp_path)) as c:
        assert c.post("/api/settings", json={"strategy.mode": "summer"}).status_code == 200
        body = c.get("/api/battery-plan").json()

    # Summer, with a live load profile + AdaptiveConfig (always supplied by _build_plan_now),
    # dispatches to the demand-aware adaptive charger, not the plain solar-first planner.
    assert body["provenance"]["planner"] == "adaptive"


def test_battery_plan_provenance_humanizes_the_forecast_solar_class_name(tmp_path):
    from ems.sources.forecast_solar import ForecastSolarSource

    fc = ForecastSolarSource(
        tz=AMS, lat=52.0, lon=5.0, tilt=35.0, azimuth=0.0, kwp=3.0,
        # Empty watts -> falls back to the model curve internally, but the CLASS stays the same.
        http_get=lambda url: {"result": {"watts": {}}},
    )
    with TestClient(_app(tmp_path, solar_forecast=fc)) as c:
        body = c.get("/api/battery-plan").json()

    assert body["provenance"]["forecast_source"] == "Forecast.Solar"


def test_battery_plan_provenance_is_present_even_when_paused_safely(tmp_path):
    # No solar forecast -> no forward projection -> the safe, empty contract branch — provenance
    # must still be present and honest (never a missing key just because there's no plan yet).
    with TestClient(_app(tmp_path, with_forecast=False)) as c:
        body = c.get("/api/battery-plan").json()

    assert body["status"] == "paused_safely"
    prov = body["provenance"]
    assert prov["forecast_source"] == "No forecast source"
    assert prov["solar_confidence_pct"] == 80.0
    assert prov["planner"] in {"rule_based", "adaptive", "summer"}
    assert prov["intelligence"]["state"] == "not_active"
