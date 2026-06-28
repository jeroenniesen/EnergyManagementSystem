"""Sanity checks for the backtest harness — more sun => more self-sufficient, and the harness runs
the real planners across all NL scenarios without crashing."""
from zoneinfo import ZoneInfo

from ems.planner.charge_need import compute_charge_need
from ems.planner.rule_based import PlannerConfig, plan_rule_based
from ems.planner.summer import SummerConfig, plan_summer
from ems.sim import nl_scenarios, simulate

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


def test_scenarios_cover_the_expected_solar_range():
    # Realised daily solar (kWh) should span a realistic NL 3 kWp range across the four days.
    by = {s.name: sum(s.actual_solar_w.values()) * 0.25 / 1000.0 for s in nl_scenarios(AMS)}
    assert by["bad"] < 3.0
    assert 6.0 < by["average"] < 12.0
    assert by["extreme"] > 15.0
    assert by["bad"] < by["average"] < by["good"] < by["extreme"]
