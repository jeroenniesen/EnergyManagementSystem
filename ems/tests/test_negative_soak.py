"""Negative-price soak (`planner.negative_price_soak`, opt-in, default OFF): when the price drops
below €0 you are PAID to consume, so those slots become battery-charge slots — even outside a
normal cheap window and even on a no-trade day. OFF must be byte-identical to today's behaviour
(regression guard). Pure — canned prices/forecast, no hardware.
"""
from datetime import UTC, datetime

from ems.domain import BatteryIntent
from ems.planner.adaptive import AdaptiveConfig, plan_adaptive
from ems.planner.rule_based import PlannerConfig, plan_rule_based
from ems.planner.schedule import SLOT
from ems.sources.forecast import ForecastSlot
from ems.sources.prices import PriceSlot

T0 = datetime(2026, 1, 15, 0, 0, tzinfo=UTC)  # a winter day


def _prices(eur: list[float]) -> list[PriceSlot]:
    return [PriceSlot(T0 + i * SLOT, e) for i, e in enumerate(eur)]


def _fingerprint(plan) -> list[tuple]:
    """Everything a consumer sees about a slot — so 'identical' really means identical."""
    return [(s.start, s.intent, s.reason, s.target_soc, s.power_w, s.deadline) for s in plan.slots]


# --- winter (rule_based) ---------------------------------------------------------------------

def test_winter_soak_off_is_identical_with_negative_slots():
    # A curve with genuinely negative slots. OFF (default) must not touch the plan at all.
    prices = _prices([-0.05, -0.02, 0.05, 0.60, 0.60, 0.05])
    base = plan_rule_based(prices, T0, PlannerConfig(charge_slots=2, discharge_slots=2))
    off = plan_rule_based(
        prices, T0, PlannerConfig(charge_slots=2, discharge_slots=2, negative_price_soak=False)
    )
    assert _fingerprint(base) == _fingerprint(off)


def test_winter_soak_on_no_negative_slots_is_identical():
    prices = _prices([0.10, 0.12, 0.60, 0.60, 0.10, 0.12])
    off = plan_rule_based(prices, T0, PlannerConfig(charge_slots=2, discharge_slots=2))
    on = plan_rule_based(
        prices, T0, PlannerConfig(charge_slots=2, discharge_slots=2, negative_price_soak=True)
    )
    assert _fingerprint(off) == _fingerprint(on)  # nothing below €0 ⇒ soak is a no-op


def test_winter_soak_on_charges_negatives_and_never_discharges_them():
    prices = _prices([-0.05, -0.02, 0.05, 0.60, 0.60, 0.05])
    cfg = PlannerConfig(charge_slots=2, discharge_slots=2, negative_price_soak=True)
    plan = plan_rule_based(prices, T0, cfg)
    by_start = {s.start: s for s in plan.slots}
    neg = {p.start for p in prices if p.eur_per_kwh < 0.0}
    assert neg  # the fixture really has sub-zero slots
    for start in neg:
        s = by_start[start]
        assert s.intent is BatteryIntent.GRID_CHARGE_TO_TARGET
        assert "paid to charge" in s.reason
        assert s.target_soc is not None and 0.0 <= s.target_soc <= 100.0
    # A sub-zero slot is NEVER a discharge slot.
    assert all(by_start[start].intent is not BatteryIntent.DISCHARGE_FOR_LOAD for start in neg)
    # The genuine peaks (positive price) still discharge — the soak only touches sub-zero slots.
    assert any(s.intent is BatteryIntent.DISCHARGE_FOR_LOAD for s in plan.slots)


def test_winter_soak_fires_even_on_a_no_trade_day():
    # Flat cheap prices + a couple of sub-zero slots: no profitable peak (a no-trade day that would
    # otherwise be all self-consumption), yet the soak must still charge the negative slots.
    prices = _prices([-0.03, 0.05, 0.05, 0.05, -0.03, 0.05])
    plan = plan_rule_based(prices, T0, PlannerConfig(negative_price_soak=True))
    charge = [s for s in plan.slots if s.intent is BatteryIntent.GRID_CHARGE_TO_TARGET]
    assert {s.start for s in charge} == {p.start for p in prices if p.eur_per_kwh < 0.0}
    assert all("paid to charge" in s.reason for s in charge)


# --- adaptive --------------------------------------------------------------------------------

def _fc(watts: list[float]) -> list[ForecastSlot]:
    return [ForecastSlot(T0 + i * SLOT, w, w, w) for i, w in enumerate(watts)]


def _load(watts: list[float]) -> dict:
    return {T0 + i * SLOT: w for i, w in enumerate(watts)}


def _acfg(**kw) -> AdaptiveConfig:
    base = dict(usable_kwh=10.0, reserve_soc_pct=10.0, round_trip_efficiency=1.0,
                max_charge_w=4000.0)
    base.update(kw)
    return AdaptiveConfig(**base)


def test_adaptive_soak_off_is_identical_with_negative_slots():
    prices = _prices([-0.05, -0.02, 0.10, 0.40, 0.40, 0.10])
    fc = _fc([0.0] * 6)
    load = _load([200.0, 200.0, 200.0, 3000.0, 3000.0, 200.0])
    base = plan_adaptive(prices, fc, T0, soc_pct=15.0, load_w_by=load, cfg=_acfg())
    off = plan_adaptive(prices, fc, T0, soc_pct=15.0, load_w_by=load,
                        cfg=_acfg(negative_price_soak=False))
    assert _fingerprint(base) == _fingerprint(off)


def test_adaptive_soak_on_charges_negatives_and_never_discharges_them():
    prices = _prices([-0.05, -0.02, 0.10, 0.40, 0.40, 0.10])
    fc = _fc([0.0] * 6)
    load = _load([200.0, 200.0, 200.0, 3000.0, 3000.0, 200.0])
    plan = plan_adaptive(prices, fc, T0, soc_pct=15.0, load_w_by=load,
                         cfg=_acfg(negative_price_soak=True))
    by_start = {s.start: s for s in plan.slots}
    neg = {p.start for p in prices if p.eur_per_kwh < 0.0}
    assert neg
    for start in neg:
        s = by_start[start]
        assert s.intent is BatteryIntent.GRID_CHARGE_TO_TARGET
        assert "paid to charge" in s.reason
        assert s.intent is not BatteryIntent.DISCHARGE_FOR_LOAD
    assert any(s.intent is BatteryIntent.DISCHARGE_FOR_LOAD for s in plan.slots)  # real peak stays


def test_adaptive_soak_on_no_negative_slots_is_identical():
    prices = _prices([0.10, 0.12, 0.10, 0.40, 0.40, 0.10])
    fc = _fc([0.0] * 6)
    load = _load([200.0, 200.0, 200.0, 3000.0, 3000.0, 200.0])
    off = plan_adaptive(prices, fc, T0, soc_pct=15.0, load_w_by=load, cfg=_acfg())
    on = plan_adaptive(prices, fc, T0, soc_pct=15.0, load_w_by=load,
                       cfg=_acfg(negative_price_soak=True))
    assert _fingerprint(off) == _fingerprint(on)
