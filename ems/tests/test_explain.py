from datetime import UTC, datetime

from ems.domain import BatteryIntent
from ems.planner.explain import build_plan_detail
from ems.planner.schedule import SLOT, Plan, PlanSlot
from ems.sources.forecast import ForecastSlot
from ems.sources.prices import PriceSlot

NOW = datetime(2026, 6, 28, 0, 0, tzinfo=UTC)


def _plan(intents):
    return Plan(created_at=NOW, slots=tuple(
        PlanSlot(NOW + i * SLOT, it, f"slot {i}") for i, it in enumerate(intents)
    ))


def test_detail_joins_price_and_solar_on_plan_timeline():
    intents = [BatteryIntent.GRID_CHARGE_TO_TARGET, BatteryIntent.ALLOW_SELF_CONSUMPTION]
    plan = _plan(intents)
    prices = [PriceSlot(NOW, 0.08), PriceSlot(NOW + SLOT, 0.20)]
    forecast = [ForecastSlot(NOW, 0, 0, 0), ForecastSlot(NOW + SLOT, 600, 1000, 1150)]
    d = build_plan_detail(NOW, prices, plan, forecast)
    assert len(d["slots"]) == 2
    # Every slot carries its aligned price + solar (same timestamp join).
    assert d["slots"][0]["eur_per_kwh"] == 0.08
    assert d["slots"][0]["intent"] == "grid_charge_to_target"
    assert d["slots"][0]["label"] == "charge"
    assert d["slots"][1]["eur_per_kwh"] == 0.20 and d["slots"][1]["solar_w"] == 1000


def test_summary_describes_charge_and_discharge_windows():
    plan = _plan([
        BatteryIntent.GRID_CHARGE_TO_TARGET,
        BatteryIntent.HOLD_RESERVE,
        BatteryIntent.DISCHARGE_FOR_LOAD,
    ])
    prices = [PriceSlot(NOW, 0.08), PriceSlot(NOW + SLOT, 0.20), PriceSlot(NOW + 2 * SLOT, 0.45)]
    d = build_plan_detail(NOW, prices, plan, None)
    s = d["summary"]
    assert "charge 1×15m at ≤€0.08" in s
    assert "discharge 1×15m at ≥€0.45" in s
    assert "hold 1×15m" in s


def test_missing_price_or_solar_is_none_not_crash():
    plan = _plan([BatteryIntent.ALLOW_SELF_CONSUMPTION])
    d = build_plan_detail(NOW, [], plan, None)  # no prices, no forecast
    assert d["slots"][0]["eur_per_kwh"] is None
    assert d["slots"][0]["solar_w"] is None


def test_empty_plan_summary():
    d = build_plan_detail(NOW, [], Plan(created_at=NOW, slots=()), None)
    assert d["slots"] == [] and d["summary"] == "No plan yet."


def test_horizon_caps_slots():
    plan = _plan([BatteryIntent.ALLOW_SELF_CONSUMPTION] * 200)
    d = build_plan_detail(NOW, [], plan, None, horizon=96)
    assert len(d["slots"]) == 96


def test_alignment_shared_timestamps_match_input():
    # Regression for the "cheap moments don't align" bug: detail slot starts == price slot starts.
    plan = _plan([BatteryIntent.GRID_CHARGE_TO_TARGET] * 3)
    prices = [PriceSlot(NOW + i * SLOT, 0.10) for i in range(3)]
    d = build_plan_detail(NOW, prices, plan, None)
    assert [s["start"] for s in d["slots"]] == [p.start.isoformat() for p in prices]
    assert all(s["eur_per_kwh"] == 0.10 for s in d["slots"])
