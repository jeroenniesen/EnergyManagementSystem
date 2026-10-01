"""Pure unit tests for the B-74 / #84 decision-reason schema (slice 1)."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from ems.domain import BatteryIntent
from ems.planner.reason import (
    build_decision_reason,
    empty_decision_reason,
)
from ems.planner.schedule import Plan, PlanSlot
from ems.planner.validator import Finding, PlanValidation

NOW = datetime(2026, 1, 15, 2, 0, tzinfo=UTC)
SLOT = timedelta(minutes=15)


def _slot(i: int, intent: BatteryIntent, reason: str, **kw) -> PlanSlot:
    return PlanSlot(NOW + i * SLOT, intent, reason, **kw)


def _charge_plan() -> Plan:
    slots = (
        _slot(0, BatteryIntent.GRID_CHARGE_TO_TARGET, "charge: cheap window €0.10/kWh",
              target_soc=80.0, power_w=4000.0, deadline=NOW + 8 * SLOT),
        _slot(1, BatteryIntent.GRID_CHARGE_TO_TARGET, "charge: cheap window €0.11/kWh",
              target_soc=80.0, power_w=4000.0, deadline=NOW + 8 * SLOT),
        _slot(2, BatteryIntent.HOLD_RESERVE, "hold cheap energy for the coming peak"),
        _slot(3, BatteryIntent.DISCHARGE_FOR_LOAD, "discharge: €0.40/kWh > break-even €0.20"),
    )
    return Plan(created_at=NOW, slots=slots, strategy="winter", target_soc=80.0,
                deadline=NOW + 8 * SLOT)


def _no_trade_plan() -> Plan:
    slots = tuple(
        _slot(i, BatteryIntent.ALLOW_SELF_CONSUMPTION,
              f"no-trade: spread below break-even (€0.2{i}/kWh)")
        for i in range(4)
    )
    return Plan(created_at=NOW, slots=slots, strategy="winter")


def test_empty_reason_is_paused_with_stable_shape():
    r = empty_decision_reason(summary="waiting").to_dict()
    assert set(r) == {
        "chosen_window", "rejected_alternative", "expected_benefit", "risk",
        "safety_constraint", "gates", "summary",
    }
    assert r["chosen_window"] is None
    assert r["safety_constraint"]["action"] == "paused"
    assert r["gates"] == {
        "validator_code": None, "failsafe": False, "dwell": False,
        "cap_reached": False, "unconfirmed": False,
    }


def test_charge_plan_carries_chosen_window_and_rejected_self_consume():
    plan = _charge_plan()
    price_by = {s.start: 0.10 + i * 0.01 for i, s in enumerate(plan.slots)}
    # Make discharge expensive so savings estimate is positive.
    price_by[plan.slots[3].start] = 0.45
    r = build_decision_reason(
        plan, price_by=price_by, risk_margin_eur_per_kwh=0.02,
    ).to_dict()

    cw = r["chosen_window"]
    assert cw["intent"] == BatteryIntent.GRID_CHARGE_TO_TARGET
    assert cw["start"] == plan.slots[0].start.isoformat()
    assert cw["end"] == plan.slots[1].slot_end.isoformat()
    assert cw["label"] == "cheap charge window"
    assert cw["eur_per_kwh_min"] <= cw["eur_per_kwh_max"]

    alt = r["rejected_alternative"]
    assert alt["intent"] == BatteryIntent.ALLOW_SELF_CONSUMPTION
    assert "self-consumption" in alt["reason"].lower()

    assert r["expected_benefit"]["eur"] is not None
    assert r["risk"]["margin_eur_per_kwh"] == 0.02
    assert r["safety_constraint"]["action"] == "proceed"
    assert r["safety_constraint"]["code"] is None
    assert r["gates"]["validator_code"] is None
    assert r["gates"]["failsafe"] is False


def test_no_trade_plan_rejects_arbitrage_window():
    plan = _no_trade_plan()
    price_by = {s.start: 0.20 for s in plan.slots}
    r = build_decision_reason(plan, price_by=price_by).to_dict()
    assert r["chosen_window"]["intent"] == BatteryIntent.ALLOW_SELF_CONSUMPTION
    assert r["rejected_alternative"]["intent"] == BatteryIntent.GRID_CHARGE_TO_TARGET
    assert "no-trade" in r["rejected_alternative"]["reason"].lower()


def test_validator_unsafe_sets_safety_constraint_code_and_paused_action():
    plan = _charge_plan()
    val = PlanValidation(
        status="unsafe",
        findings=(Finding("unsafe", "stale_inputs", "critical inputs are stale"),),
    )
    r = build_decision_reason(plan, validation=val, paused=True).to_dict()
    assert r["safety_constraint"]["code"] == "stale_inputs"
    assert r["safety_constraint"]["action"] == "paused"
    assert r["gates"]["validator_code"] == "stale_inputs"
    assert r["safety_constraint"]["message"] == "critical inputs are stale"


def test_gate_outcomes_reflect_dwell_cap_failsafe_unconfirmed():
    plan = _charge_plan()
    r = build_decision_reason(
        plan,
        decision_outcome="dwell",
        failsafe=True,
        unconfirmed=True,
    ).to_dict()
    assert r["gates"]["dwell"] is True
    assert r["gates"]["cap_reached"] is False
    assert r["gates"]["failsafe"] is True
    assert r["gates"]["unconfirmed"] is True

    r2 = build_decision_reason(plan, decision_outcome="cap_reached").to_dict()
    assert r2["gates"]["cap_reached"] is True
    assert r2["gates"]["dwell"] is False


def test_discharge_plan_labels_self_consumption_not_forced_dump():
    """DISCHARGE_FOR_LOAD is vendor self-consumption — reason copy must not say 'discharging'."""
    slots = (
        _slot(0, BatteryIntent.DISCHARGE_FOR_LOAD, "serve house at peak €0.40/kWh"),
        _slot(1, BatteryIntent.DISCHARGE_FOR_LOAD, "serve house at peak €0.42/kWh"),
    )
    plan = Plan(created_at=NOW, slots=slots, strategy="winter")
    price_by = {slots[0].start: 0.40, slots[1].start: 0.42}
    r = build_decision_reason(plan, price_by=price_by).to_dict()
    assert r["chosen_window"]["label"] == "expensive self-consumption window"
    assert "discharge" not in (r["chosen_window"]["label"] or "").lower()
    alt = r["rejected_alternative"]
    assert alt is not None
    assert "self-consumption" in alt["reason"].lower()
    assert "discharging" not in alt["reason"].lower()


def test_summary_rewrites_bare_discharge_slot_reason():
    """Expanded why Summary must not show planner-internal `discharge: €…`."""
    slots = (
        _slot(0, BatteryIntent.DISCHARGE_FOR_LOAD,
              "discharge: €0.40/kWh > break-even €0.20"),
    )
    plan = Plan(created_at=NOW, slots=slots, strategy="winter")
    price_by = {slots[0].start: 0.40}
    r = build_decision_reason(
        plan, price_by=price_by,
        summary="discharge: €0.40/kWh > break-even €0.20",
    ).to_dict()
    assert not r["summary"].lower().startswith("discharge:")
    assert "self-consumption" in r["summary"].lower()
    assert "€0.40" in r["summary"]
    assert "break-even" in r["summary"].lower()


def test_none_plan_returns_paused_empty_reason():
    r = build_decision_reason(None, plan_reason="gone").to_dict()
    assert r["chosen_window"] is None
    assert r["safety_constraint"]["action"] == "paused"
    assert r["summary"] == "gone"


def test_format_reason_log_line_includes_key_facts():
    from ems.planner.reason import format_reason_log_line

    r = build_decision_reason(
        _charge_plan(),
        price_by={NOW: 0.10, NOW + SLOT: 0.11},
        risk_margin_eur_per_kwh=0.02,
    )
    line = format_reason_log_line(r)
    assert line.startswith("decision.reason ")
    assert "action=proceed" in line
    assert "window=grid_charge_to_target" in line
    assert "benefit_eur=" in line
    assert "summary='" in line or 'summary="' in line  # quoted so spaces don't break the line
    # Also accepts a plain dict (logs / export path).
    assert format_reason_log_line(r.to_dict()).startswith("decision.reason ")


def test_reason_dict_keys_constant_matches_to_dict():
    from ems.planner.reason import GATE_DICT_KEYS, REASON_DICT_KEYS

    r = empty_decision_reason().to_dict()
    assert set(r) == REASON_DICT_KEYS
    assert set(r["gates"]) == GATE_DICT_KEYS
