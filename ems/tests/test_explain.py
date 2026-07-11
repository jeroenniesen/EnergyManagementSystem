from datetime import UTC, datetime

from ems.domain import BatteryIntent
from ems.planner.explain import (
    ExternalLlmExplainer,
    TemplateExplainer,
    _has_ungrounded_number,
    _llm_error_message,
    _strip_reasoning,
    build_plan_detail,
    plan_metrics,
    summarize_projection,
)
from ems.planner.projection import ProjectedSlot
from ems.planner.rule_based import PlannerConfig, plan_rule_based
from ems.planner.schedule import SLOT, Plan, PlanSlot
from ems.sources.forecast import ForecastSlot
from ems.sources.prices import PriceSlot

NOW = datetime(2026, 6, 28, 0, 0, tzinfo=UTC)

# ---- Explainer port (M6c prototype: TemplateExplainer + ExternalLlmExplainer) ----------------

REASON = "Charging in the cheapest 3-hour window at €0.30/kWh to reach 72% by 07:00."
FACTS = {"price_eur_kwh": 0.30, "target_pct": 72}


def _fake_post(content):
    """A fake OpenAI-compatible chat transport returning a fixed assistant message."""
    def post(messages, params):
        return {"choices": [{"message": {"content": content}}]}
    return post


def test_strip_reasoning_removes_think_block_keeps_answer():
    # MiniMax-M2.7 inlines a <think>…</think> block before the answer (OpenAI content).
    assert _strip_reasoning("<think>weighing options</think>We run on battery now.") == \
        "We run on battery now."
    assert _strip_reasoning("plain answer, no thinking") == "plain answer, no thinking"
    # An unclosed <think> = reply truncated mid-thought → no answer arrived → "" (→ fallback).
    assert _strip_reasoning("<think>still reasoning and ran out of tok") == ""


def test_external_explainer_strips_reasoning_then_grounds():
    # Reasoning mentions a figure not in the inputs, but it's in <think> → must NOT trip the guard,
    # and the clean answer (grounded) is what's returned.
    out = "<think>price could be €0.95 but inputs say €0.30</think>Charging at €0,30/kWh to 72%."
    e = ExternalLlmExplainer(_fake_post(out), model="m", language="Dutch").explain(REASON, FACTS)
    assert e.source == "external_llm"
    assert "<think>" not in e.text and e.text == "Charging at €0,30/kWh to 72%."


def test_template_explainer_returns_reason_verbatim():
    e = TemplateExplainer().explain(REASON, FACTS)
    assert e.text == REASON and e.source == "template" and e.base_reason == REASON


def test_external_explainer_happy_path_is_grounded_and_tagged():
    out = "We laden op in het goedkoopste venster van €0,30/kWh tot 72%."  # Dutch, grounded
    e = ExternalLlmExplainer(_fake_post(out), model="MiniMax-M2.5", language="Dutch").explain(
        REASON, FACTS
    )
    assert e.source == "external_llm"
    assert e.text == out
    assert e.base_reason == REASON  # traceability: tied to the deterministic reason


def test_external_explainer_rejects_invented_number_falls_back_to_template():
    out = "Charging now because the price will spike to €0.95/kWh tonight."  # 0.95 not in inputs
    e = ExternalLlmExplainer(_fake_post(out), model="m").explain(REASON, FACTS)
    assert e.source == "template" and e.text == REASON


def test_external_explainer_falls_back_on_transport_error():
    def boom(messages, params):
        raise TimeoutError("slow")

    e = ExternalLlmExplainer(boom, model="m").explain(REASON, FACTS)
    assert e.source == "template" and e.text == REASON


def test_external_explainer_falls_back_on_empty_or_bad_response():
    assert ExternalLlmExplainer(_fake_post("   "), model="m").explain(REASON, FACTS).source == \
        "template"

    def bad_shape(messages, params):
        return {"unexpected": True}

    assert ExternalLlmExplainer(bad_shape, model="m").explain(REASON, FACTS).source == "template"


def test_external_explainer_payload_is_minimal_and_redacted():
    captured = {}

    def capture(messages, params):
        captured["messages"] = messages
        return {"choices": [{"message": {"content": "Opladen tot 72% voor €0,30/kWh."}}]}

    ExternalLlmExplainer(capture, model="m", language="Dutch").explain(REASON, FACTS)
    blob = " ".join(m["content"] for m in captured["messages"])
    # the deterministic reason + the cited facts ARE sent...
    assert REASON in blob and "0.3" in blob and "72" in blob
    assert "Dutch" in blob  # the requested language is in the instruction
    # ...and nothing identifying leaks (none was passed in — the caller controls the facts dict).
    for forbidden in ("192.168", "secret", "token", "latitude", "52.13", "Amsterdam"):
        assert forbidden not in blob


def test_grounding_guard_accepts_reformatted_numbers_rejects_new_ones():
    src = "charge at €0.30/kWh to 72%"
    assert not _has_ungrounded_number("Opladen tot 72 procent voor €0,30/kWh.", src)
    assert _has_ungrounded_number("Charging to 85%.", src)  # 85 was never in the inputs


def test_llm_error_message_is_actionable_by_http_status():
    class _Resp:
        def __init__(self, sc):
            self.status_code = sc

    class _HttpErr(Exception):
        def __init__(self, sc, text=""):
            self.response = _Resp(sc)
            self.response.text = text

    assert "402" in _llm_error_message(_HttpErr(402))
    assert "credit" in _llm_error_message(_HttpErr(402)).lower()
    # A 402 whose body names the balance → the specific, actionable "top up Balance" message.
    bal = _llm_error_message(_HttpErr(402, '{"error":{"type":"insufficient_balance_error"}}'))
    assert "balance" in bal.lower() and "minimax.io" in bal.lower()
    assert "401" in _llm_error_message(_HttpErr(401))
    assert "429" in _llm_error_message(_HttpErr(429))
    # No HTTP status (e.g. a timeout / connection error) → the generic, still-reassuring message.
    assert "reachable" in _llm_error_message(TimeoutError("slow")).lower()


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


def test_negative_price_soak_reason_and_summary_flow_through_detail():
    # End-to-end: the winter planner's negative-price soak produces a per-slot "paid to charge"
    # reason, and the plan-level summary calls out "+N negative-price slots" when it fired.
    prices = [PriceSlot(NOW + i * SLOT, e)
              for i, e in enumerate([-0.05, -0.02, 0.05, 0.60, 0.60, 0.05])]
    plan = plan_rule_based(
        prices, NOW,
        PlannerConfig(charge_slots=2, discharge_slots=2, negative_price_soak=True),
    )
    d = build_plan_detail(NOW, prices, plan, None)
    soak = [s for s in d["slots"] if "paid to charge" in s["reason"]]
    assert len(soak) == 2  # the two sub-zero slots
    assert all(s["label"] == "charge" for s in soak)
    assert "negative-price" in d["summary"] and "+2" in d["summary"]


def test_alignment_shared_timestamps_match_input():
    # Regression for the "cheap moments don't align" bug: detail slot starts == price slot starts.
    plan = _plan([BatteryIntent.GRID_CHARGE_TO_TARGET] * 3)
    prices = [PriceSlot(NOW + i * SLOT, 0.10) for i in range(3)]
    d = build_plan_detail(NOW, prices, plan, None)
    assert [s["start"] for s in d["slots"]] == [p.start.isoformat() for p in prices]
    assert all(s["eur_per_kwh"] == 0.10 for s in d["slots"])
