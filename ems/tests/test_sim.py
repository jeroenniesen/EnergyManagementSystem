"""Sanity checks for the backtest harness — more sun => more self-sufficient, and the harness runs
the real planners across all NL scenarios without crashing."""
from zoneinfo import ZoneInfo

from ems.planner.adaptive import AdaptiveConfig, plan_adaptive
from ems.planner.charge_need import compute_charge_need
from ems.planner.rule_based import PlannerConfig, plan_rule_based
from ems.planner.summer import SummerConfig, plan_summer
from ems.sim import nl_scenarios, simulate, simulate_rolling

AMS = ZoneInfo("Europe/Amsterdam")


def _winter(sc):
    return plan_rule_based(sc.prices, sc.now, PlannerConfig())


def _summer(sc):
    need = compute_charge_need(soc_pct=sc.start_soc_pct, usable_kwh=10.8, min_reserve_soc=10.0,
                               night_reserve_kwh=2.0, overnight_load_kwh=6.0)
    return plan_summer(sc.prices, sc.forecast, sc.now, soc_pct=sc.start_soc_pct,
                       cfg=SummerConfig(usable_kwh=10.8, target_soc_pct=need.target_soc_pct))


def test_harness_runs_for_all_scenarios_and_planners():
    for sc in nl_scenarios(AMS):
        for plan_fn in (_summer, _winter):
            r = simulate(sc, plan_fn)
            assert 0.0 <= r.self_sufficiency_pct <= 100.0
            assert 0.0 <= r.soc_min_pct <= 100.0
            assert r.import_kwh >= 0.0 and r.export_kwh >= 0.0


def test_more_sun_means_more_self_sufficient_and_less_import():
    scs = {s.name: s for s in nl_scenarios(AMS)}
    bad = simulate(scs["bad"], _summer)
    extreme = simulate(scs["extreme"], _summer)
    assert extreme.self_sufficiency_pct > bad.self_sufficiency_pct
    assert extreme.import_kwh < bad.import_kwh


def _nt():
    return compute_charge_need(soc_pct=50.0, usable_kwh=10.8, min_reserve_soc=10.0,
                               night_reserve_kwh=2.0, overnight_load_kwh=6.0).target_soc_pct


def test_adaptive_beats_current_on_cost_under_rolling_replanning():
    # The realistic test: replan every slot. The adaptive demand-aware charger must cost no more
    # than the current solar-first one across every NL day, and clearly less in total — while never
    # discharging below the reserve floor.
    total_cur = total_adp = 0.0
    for sc in nl_scenarios(AMS):
        cur = simulate_rolling(
            sc, lambda now, soc, sc=sc: plan_summer(
                sc.prices, sc.forecast, now, soc_pct=soc,
                cfg=SummerConfig(usable_kwh=10.8, target_soc_pct=_nt())))
        adp = simulate_rolling(
            sc, lambda now, soc, sc=sc: plan_adaptive(
                sc.prices, sc.forecast, now, soc_pct=soc, load_w_by=sc.load_w,
                cfg=AdaptiveConfig(usable_kwh=10.8)))
        assert adp.grid_cost_eur <= cur.grid_cost_eur + 0.01, f"{sc.name}: adaptive worse"
        assert adp.night_ok, f"{sc.name}: adaptive dipped below reserve"
        total_cur += cur.grid_cost_eur
        total_adp += adp.grid_cost_eur
    assert total_adp < total_cur - 1.0  # a clear, material saving over the four days


def test_scenarios_cover_the_expected_solar_range():
    # Realised daily solar (kWh) should span a realistic NL 3 kWp range across the four days.
    by = {s.name: sum(s.actual_solar_w.values()) * 0.25 / 1000.0 for s in nl_scenarios(AMS)}
    assert by["bad"] < 3.0
    assert 6.0 < by["average"] < 12.0
    assert by["extreme"] > 15.0
    assert by["bad"] < by["average"] < by["good"] < by["extreme"]
