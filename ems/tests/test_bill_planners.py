from datetime import UTC, datetime

from ems.domain import BatteryIntent
from ems.planner.adaptive import AdaptiveConfig, plan_adaptive
from ems.planner.rule_based import PlannerConfig, plan_rule_based
from ems.planner.schedule import SLOT
from ems.sources.forecast import ForecastSlot
from ems.sources.prices import PriceSlot

NOW = datetime(2026, 9, 17, tzinfo=UTC)


def inputs(values, solar, load):
    prices = [PriceSlot(NOW + i * SLOT, v) for i, v in enumerate(values)]
    forecast = [ForecastSlot(p.start, w, w, w) for p, w in zip(prices, solar, strict=True)]
    loads = {p.start: w for p, w in zip(prices, load, strict=True)}
    return prices, forecast, loads


def charges(plan):
    return [s for s in plan.slots if s.intent == BatteryIntent.GRID_CHARGE_TO_TARGET]


def test_summer_rejects_marginal_unprofitable_purchases():
    prices, forecast, loads = inputs([.1] * 3 + [.29] * 3 + [.3] * 6,
                                      [0] * 12, [0] * 6 + [4000] * 6)
    cfg = AdaptiveConfig(usable_kwh=10, bill_optimization_enabled=True)
    plan = plan_adaptive(prices, forecast, NOW, soc_pct=10, load_w_by=loads, cfg=cfg)
    assert charges(plan)
    assert all(s.start < prices[3].start for s in charges(plan))


def winter(solar, *, values=None, load=None, usable=10):
    values = values or [.1] * 4 + [.5] * 4
    load = load or [0] * 4 + [2000] * 4
    prices, forecast, loads = inputs(values, solar, load)
    return plan_rule_based(prices, NOW, PlannerConfig(charge_slots=1,
        bill_optimization_enabled=True), soc_pct=10, load_w_by=loads,
        usable_kwh=usable, forecast=forecast, solar_confidence=1)


def test_winter_does_not_buy_energy_supplied_by_prepeak_solar():
    assert not charges(winter([4000] * 4 + [0] * 4))


def test_winter_cannot_use_sunshine_after_the_peak():
    plan = winter([0] * 6 + [16000] * 2,
                  values=[.1] * 4 + [.5] * 2 + [.1] * 2,
                  load=[0] * 4 + [4000] * 2 + [0] * 2)
    assert charges(plan)
    assert all(s.start < NOW + 4 * SLOT for s in charges(plan))


def test_solar_credit_respects_capacity_between_peaks():
    plan = winter([16000, 0, 0, 0, 0, 0], values=[.1, .5, .5, .1, .5, .5],
                  load=[0, 4000, 4000, 0, 4000, 4000], usable=2)
    assert any(s.start == NOW + 3 * SLOT for s in charges(plan))


def test_flat_prices_do_not_purchase_energy():
    assert not charges(winter([0] * 8, values=[.3] * 8))


def test_summer_import_fee_prevents_false_negative_price_soak():
    prices, forecast, loads = inputs([-.01, .02], [0, 0], [0, 1000])
    plan = plan_adaptive(prices, forecast, NOW, soc_pct=10, load_w_by=loads,
        cfg=AdaptiveConfig(usable_kwh=10, bill_optimization_enabled=True,
                           import_fee_eur_per_kwh=.2, negative_price_soak=True))
    assert not charges(plan)


def test_negative_import_can_profitably_supply_later_load():
    plan = winter([0, 0], values=[-.1, .3], load=[0, 1000])
    assert len(charges(plan)) == 1
    assert charges(plan)[0].target_kwh < .27
    assert charges(plan)[0].target_soc < 13


def test_peak_solar_reduces_purchased_energy():
    without = winter([0] * 8)
    with_solar = winter([0] * 4 + [1000] * 4)
    assert sum(s.target_kwh for s in charges(with_solar)) < sum(
        s.target_kwh for s in charges(without))


def test_import_price_already_complete_is_not_charged_an_extra_fee():
    prices, forecast, loads = inputs([.1, .2], [0, 0], [0, 1000])
    cfg = AdaptiveConfig(usable_kwh=10, bill_optimization_enabled=True,
                         import_fee_eur_per_kwh=1, tibber_total_includes_all=True)
    assert charges(plan_adaptive(prices, forecast, NOW, soc_pct=10,
                                 load_w_by=loads, cfg=cfg))


def test_negative_peak_is_not_simulated_as_both_charge_and_discharge():
    # Negative prices can clear the loss-adjusted gate even at identical prices. A slot
    # with demand still cannot execute charge and discharge intents simultaneously.
    plan = winter([0, 0], values=[-1, -1], load=[4000, 4000])
    assert not charges(plan)


def test_grid_charge_cannot_claim_solar_energy_it_displaces():
    prices, forecast, loads = inputs([.05, .1, .5], [0, 4000, 0], [0, 0, 4000])
    plan = plan_rule_based(prices, NOW, PlannerConfig(bill_optimization_enabled=True),
        soc_pct=60, load_w_by=loads, usable_kwh=2, forecast=forecast, solar_confidence=1)
    assert not charges(plan)
    assert all(s.floor_soc == 10 for s in plan.slots)


def test_partial_current_slot_cannot_purchase_a_full_quarter_hour():
    from datetime import timedelta

    prices, forecast, loads = inputs([.1, .5], [0, 0], [0, 4000])
    plan = plan_rule_based(prices, NOW + timedelta(minutes=14),
        PlannerConfig(bill_optimization_enabled=True), soc_pct=10, load_w_by=loads,
        forecast=forecast)
    assert len(charges(plan)) == 1
    assert charges(plan)[0].target_kwh < .064


def test_grid_purchase_respects_maximum_deliverable_power():
    prices, forecast, loads = inputs([.1] * 4 + [.5], [0] * 5, [0] * 4 + [16000])
    plan = plan_rule_based(prices, NOW,
        PlannerConfig(bill_optimization_enabled=True, max_discharge_w=1000),
        soc_pct=10, load_w_by=loads, forecast=forecast)
    assert .26 < sum(s.target_kwh for s in charges(plan)) < .27


def test_costly_forecast_solar_is_not_treated_as_free_peak_energy():
    prices, forecast, loads = inputs([.1, .2, .5], [0, 4000, 0], [0, 0, 4000])
    plan = plan_rule_based(prices, NOW, PlannerConfig(bill_optimization_enabled=True),
        soc_pct=10, load_w_by=loads, forecast=forecast,
        export_price_by={prices[1].start: 1.0})
    assert all(s.intent is BatteryIntent.ALLOW_SELF_CONSUMPTION for s in plan.slots)
    assert all("export" in s.reason for s in plan.slots)


def test_opt_in_explains_when_unconditional_negative_soak_is_not_used():
    prices, forecast, loads = inputs([-.1, .3], [0, 0], [0, 0])
    plan = plan_adaptive(prices, forecast, NOW, soc_pct=10, load_w_by=loads,
        cfg=AdaptiveConfig(usable_kwh=10, bill_optimization_enabled=True,
                           negative_price_soak=True))
    assert not charges(plan)
    assert "negative-price soak" in plan.slots[0].reason
    assert "future load" in plan.slots[0].reason


def test_contiguous_charge_window_has_one_executable_target():
    from ems.tests.test_control_service import _controlling_controller

    prices, forecast, loads = inputs([.1, .1, .5, .5], [0] * 4, [0, 0, 4000, 4000])
    plan = plan_rule_based(prices, NOW, PlannerConfig(
        bill_optimization_enabled=True, round_trip_efficiency=1,
        degradation_eur_per_kwh=0, risk_margin_eur_per_kwh=0),
        soc_pct=10, load_w_by=loads, forecast=forecast)
    bought = charges(plan)
    assert len(bought) == 2
    assert bought[0].target_soc == bought[1].target_soc
    assert abs(bought[0].target_soc - 30) < 1e-6
    controller = _controlling_controller()
    outcomes = [controller.decide(s.intent, s.start, target_soc=s.target_soc, power_w=s.power_w)
                for s in bought]
    assert outcomes[0].outcome == "applied"
    assert outcomes[1].outcome == "idempotent"
    assert abs(controller.driver.last_target_soc - 30) < 1e-6
    assert controller.switches_today == 1


def test_contiguous_targets_match_realized_purchase_timing():
    prices, forecast, loads = inputs([.15, .05, .5, .5], [0] * 4, [0, 0, 4000, 2000])
    plan = plan_rule_based(prices, NOW, PlannerConfig(
        bill_optimization_enabled=True, round_trip_efficiency=1,
        degradation_eur_per_kwh=0, risk_margin_eur_per_kwh=0),
        soc_pct=10, load_w_by=loads, forecast=forecast)
    bought = charges(plan)
    assert len(bought) == 2
    assert abs(bought[0].target_kwh - 1) < 1e-6
    assert abs(bought[1].target_kwh - .5) < 1e-6


def test_charge_window_does_not_replace_credited_solar_with_grid_purchase():
    prices, forecast, loads = inputs([.1, .05, .5, .5], [0, 2000, 0, 0],
                                      [0, 0, 4000, 2000])
    plan = plan_rule_based(prices, NOW, PlannerConfig(
        bill_optimization_enabled=True, round_trip_efficiency=1,
        degradation_eur_per_kwh=0, risk_margin_eur_per_kwh=0),
        soc_pct=10, load_w_by=loads, forecast=forecast, solar_confidence=1)
    assert all(s.start != prices[1].start for s in charges(plan))
    assert abs(sum(s.target_kwh for s in charges(plan)) - 1) < 1e-6
