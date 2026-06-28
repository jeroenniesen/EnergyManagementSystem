from datetime import UTC, datetime

from ems.domain import BatteryIntent
from ems.planner.explain import build_plan_detail, plan_metrics, summarize_projection
from ems.planner.projection import ProjectedSlot
from ems.planner.schedule import SLOT, Plan, PlanSlot
from ems.sources.forecast import ForecastSlot
from ems.sources.prices import PriceSlot

NOW = datetime(2026, 6, 28, 0, 0, tzinfo=UTC)


def test_summarize_projection_empty():
    s = summarize_projection([])
    assert s["soc_end_pct"] is None
    assert s["import_kwh"] == 0.0
    assert "No projection" in s["summary"]


def test_summarize_projection_reports_peak_trough_end_and_energy():
    # 4 slots: charge to a peak, then drain. import 4 kW for one slot = 1 kWh.
    PS = ProjectedSlot
    proj = [
        PS(NOW + 0 * SLOT, BatteryIntent.GRID_CHARGE_TO_TARGET, 60.0, -4000, 4000, 0, 0),
        PS(NOW + 1 * SLOT, BatteryIntent.HOLD_RESERVE, 90.0, 0, 0, 0, 0),
        PS(NOW + 2 * SLOT, BatteryIntent.DISCHARGE_FOR_LOAD, 70.0, 2000, 0, 0, 2000),
        PS(NOW + 3 * SLOT, BatteryIntent.ALLOW_SELF_CONSUMPTION, 40.0, 0, -1000, 1000, 0),
    ]
    s = summarize_projection(proj)
    assert s["soc_max_pct"] == 90.0 and s["soc_max_at"] == (NOW + SLOT).isoformat()
    assert s["soc_min_pct"] == 40.0
    assert s["soc_end_pct"] == 40.0
    assert s["import_kwh"] == 1.0  # 4000 W × 0.25 h
    assert s["export_kwh"] == 0.25  # 1000 W × 0.25 h
    assert "%" in s["summary"]


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


def test_plan_metrics_counts_and_savings():
    plan = _plan([
        BatteryIntent.GRID_CHARGE_TO_TARGET,
        BatteryIntent.DISCHARGE_FOR_LOAD,
        BatteryIntent.ALLOW_SELF_CONSUMPTION,
    ])
    prices = [PriceSlot(NOW, 0.10), PriceSlot(NOW + SLOT, 0.40), PriceSlot(NOW + 2 * SLOT, 0.20)]
    m = plan_metrics(plan, prices)
    assert m["charge_slots"] == 1 and m["discharge_slots"] == 1 and m["self_consume_slots"] == 1
    assert isinstance(m["savings_eur"], float) and m["savings_eur"] >= 0
    assert "charge" in m["summary"]


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
