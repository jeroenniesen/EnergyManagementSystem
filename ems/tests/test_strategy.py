"""Strategy selection (auto by season, or forced) + the dispatcher that maps a strategy name to
its planner. Both planners emit the same Plan, so everything downstream is unchanged."""
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from ems.domain import BatteryIntent
from ems.planner.adaptive import AdaptiveConfig
from ems.planner.rule_based import PlannerConfig
from ems.planner.schedule import SLOT
from ems.planner.strategy import (
    HysteresisState,
    apply_hysteresis,
    build_plan,
    resolve_strategy_hysteretic,
    select_strategy,
    select_strategy_with_reason,
)
from ems.planner.summer import SummerConfig
from ems.sources.forecast import ForecastSlot
from ems.sources.prices import PriceSlot

AMS = ZoneInfo("Europe/Amsterdam")


def test_explicit_mode_wins():
    jan = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)
    assert select_strategy(jan, "summer", AMS) == "summer"  # forced summer in January
    assert select_strategy(datetime(2026, 7, 1, tzinfo=UTC), "winter", AMS) == "winter"


def test_auto_picks_by_season():
    assert select_strategy(datetime(2026, 6, 28, tzinfo=UTC), "auto", AMS) == "summer"
    assert select_strategy(datetime(2026, 1, 10, tzinfo=UTC), "auto", AMS) == "winter"
    assert select_strategy(datetime(2026, 10, 5, tzinfo=UTC), "auto", AMS) == "winter"


def test_with_reason_honours_forced_mode():
    s, why = select_strategy_with_reason(datetime(2026, 1, 10, tzinfo=UTC), "summer", AMS)
    assert s == "summer" and "You chose" in why


def test_with_reason_auto_picks_solar_first_on_high_surplus_even_in_winter_month():
    # A sunny day in January → solar-first, NOT by calendar (energy review P1.1).
    jan = datetime(2026, 1, 10, tzinfo=UTC)
    s, why = select_strategy_with_reason(jan, "auto", AMS, surplus_kwh=8.0, price_spread_eur=0.05)
    assert s == "summer" and "solar-first" in why


def test_with_reason_auto_picks_price_smart_on_low_solar_high_spread_in_summer_month():
    # A dull day in a summer month with a wide spread → price-smart arbitrage.
    jul = datetime(2026, 7, 1, tzinfo=UTC)
    s, why = select_strategy_with_reason(jul, "auto", AMS, surplus_kwh=0.5, price_spread_eur=0.30)
    assert s == "winter" and "price-smart" in why


def test_with_reason_auto_falls_back_to_season_without_inputs():
    s, why = select_strategy_with_reason(datetime(2026, 6, 28, tzinfo=UTC), "auto", AMS)
    assert s == "summer" and "by season" in why


def test_auto_uses_local_month_not_utc():
    # 2026-03-31 23:30 UTC is 2026-04-01 01:30 in Amsterdam (summer DST) -> April -> summer.
    near_midnight = datetime(2026, 3, 31, 23, 30, tzinfo=UTC)
    assert select_strategy(near_midnight, "auto", AMS) == "summer"


def test_unknown_mode_falls_back_to_auto():
    assert select_strategy(datetime(2026, 7, 1, tzinfo=UTC), "banana", AMS) == "summer"
    assert select_strategy(datetime(2026, 1, 1, tzinfo=UTC), None, AMS) == "winter"


def _prices(n):
    t0 = datetime(2026, 6, 28, 12, 0, tzinfo=UTC)
    # cheap morning, pricey afternoon -> winter planner will find an arbitrage spread
    return [PriceSlot(t0 + i * SLOT, 0.05 if i < n // 2 else 0.40) for i in range(n)]


def test_build_plan_dispatches_to_summer():
    t0 = datetime(2026, 6, 28, 12, 0, tzinfo=UTC)
    prices = _prices(16)
    fc = [ForecastSlot(t0 + i * SLOT, 3000.0, 3000.0, 3000.0) for i in range(16)]  # sunny
    plan = build_plan(
        "summer", prices=prices, forecast=fc, now=t0, soc_pct=50.0,
        winter_cfg=PlannerConfig(), summer_cfg=SummerConfig(usable_kwh=10.0, target_soc_pct=80.0),
    )
    # Sunny summer -> solar-first, no forced grid charge.
    assert BatteryIntent.GRID_CHARGE_TO_TARGET not in {s.intent for s in plan.slots}
    assert plan.slots  # non-empty


def test_build_plan_dispatches_to_winter():
    t0 = datetime(2026, 1, 10, 0, 0, tzinfo=UTC)
    # A clear cheap window (>= the 12-slot charge default) then an expensive peak -> arbitrage.
    prices = [PriceSlot(t0 + i * SLOT, 0.05 if i < 24 else 0.40) for i in range(48)]
    plan = build_plan(
        "winter", prices=prices, forecast=[], now=t0, soc_pct=50.0,
        winter_cfg=PlannerConfig(), summer_cfg=SummerConfig(usable_kwh=10.0, target_soc_pct=80.0),
    )
    # Winter arbitrage charges the cheap window for the expensive peak.
    assert BatteryIntent.GRID_CHARGE_TO_TARGET in {s.intent for s in plan.slots}


def test_winter_is_demand_sized_when_a_load_profile_is_present():
    # Energy review P1.2: with a load profile + adaptive cfg, WINTER goes through the demand-aware
    # adaptive planner — sizing the top-up to the evening peak load and carrying a target SoC.
    t0 = datetime(2026, 1, 10, 12, 0, tzinfo=UTC)
    # 12 cheap morning slots then a 4-slot expensive evening peak (so the cheapest-12 window is all
    # cheap and break-even is low enough that the peak clears).
    prices = [PriceSlot(t0 + i * SLOT, 0.10 if i < 12 else 0.40) for i in range(16)]
    fc = [ForecastSlot(t0 + i * SLOT, 0.0, 0.0, 0.0) for i in range(16)]  # winter: no solar
    load = {t0 + i * SLOT: (200.0 if i < 12 else 3000.0) for i in range(16)}  # big evening peak
    plan = build_plan(
        "winter", prices=prices, forecast=fc, now=t0, soc_pct=20.0,
        winter_cfg=PlannerConfig(), summer_cfg=SummerConfig(usable_kwh=10.0, target_soc_pct=80.0),
        load_w_by=load, adaptive_cfg=AdaptiveConfig(usable_kwh=10.0),
    )
    assert plan.strategy == "winter"  # still the distinct arbitrage planner, just demand-sized
    charge = [s for s in plan.slots if s.intent is BatteryIntent.GRID_CHARGE_TO_TARGET]
    assert charge and all(s.start < t0 + 12 * SLOT for s in charge)  # cheap pre-peak window
    assert all(s.target_soc is not None for s in charge)  # demand-sized target now carried
    assert plan.target_soc is not None and plan.deadline == t0 + 12 * SLOT


def test_winter_demand_sized_no_discharge_when_no_load_at_the_peak():
    # Load profile present but the expensive window has NO house load → nothing to shave, and this
    # system doesn't export, so it must NOT discharge for price alone → no-trade (review fix #3).
    t0 = datetime(2026, 1, 10, 12, 0, tzinfo=UTC)
    prices = [PriceSlot(t0 + i * SLOT, 0.10 if i < 12 else 0.40) for i in range(16)]
    load = {t0 + i * SLOT: (3000.0 if i < 12 else 0.0) for i in range(16)}  # zero load at the peak
    plan = build_plan(
        "winter", prices=prices, forecast=[], now=t0, soc_pct=20.0,
        winter_cfg=PlannerConfig(), summer_cfg=SummerConfig(usable_kwh=10.0, target_soc_pct=80.0),
        load_w_by=load, adaptive_cfg=AdaptiveConfig(usable_kwh=10.0),
    )
    assert all(s.intent is BatteryIntent.ALLOW_SELF_CONSUMPTION for s in plan.slots)


def test_winter_falls_back_to_rule_based_without_a_load_profile():
    # No load profile → the simple price-arbitrage planner still runs (and is labelled winter).
    t0 = datetime(2026, 1, 10, 0, 0, tzinfo=UTC)
    prices = [PriceSlot(t0 + i * SLOT, 0.05 if i < 24 else 0.40) for i in range(48)]
    plan = build_plan(
        "winter", prices=prices, forecast=None, now=t0, soc_pct=50.0,
        winter_cfg=PlannerConfig(), summer_cfg=SummerConfig(usable_kwh=10.0, target_soc_pct=80.0),
    )
    assert plan.strategy == "winter"
    assert BatteryIntent.GRID_CHARGE_TO_TARGET in {s.intent for s in plan.slots}


# --- Seasonal-transition hysteresis (SPEC §8.4 / BACKLOG B-15) ------------------------------------
def _advance(state, raw, day, *, days=3):
    return apply_hysteresis(raw, state, hysteresis_days=days, day=day)


def test_hysteresis_fresh_state_commits_immediately_like_today():
    # Fresh install (no memory) → the current pick stands on the FIRST evaluation, no switch delay.
    committed, state = _advance(HysteresisState(), "winter", date(2026, 3, 1))
    assert committed == "winter" and state.committed == "winter"


def test_hysteresis_zero_days_disables_and_switches_instantly():
    start = HysteresisState(committed="winter", last_day="2026-03-01")
    committed, state = _advance(start, "summer", date(2026, 3, 2), days=0)
    assert committed == "summer" and state.committed == "summer"


def test_hysteresis_holds_until_n_consecutive_days_then_switches():
    state = HysteresisState(committed="winter", last_day="2026-02-28")
    base = date(2026, 3, 1)
    # Day 1 and 2 lean summer → still HELD on winter (the committed season).
    committed, state = _advance(state, "summer", base)
    assert committed == "winter" and state.count == 1
    committed, state = _advance(state, "summer", base + timedelta(days=1))
    assert committed == "winter" and state.count == 2
    # Day 3 completes the run → the switch commits.
    committed, state = _advance(state, "summer", base + timedelta(days=2))
    assert committed == "summer" and state.committed == "summer" and state.pending is None


def test_hysteresis_flapping_signal_never_switches():
    # A shoulder-month signal that flips summer/winter day by day must resolve to a run that never
    # reaches 3 consecutive → the committed season is held the whole time.
    state = HysteresisState(committed="winter", last_day="2026-02-28")
    base = date(2026, 3, 1)
    picks = ["summer", "winter", "summer", "winter", "summer", "winter"]
    for i, raw in enumerate(picks):
        committed, state = _advance(state, raw, base + timedelta(days=i))
        assert committed == "winter"  # never flaps
    assert state.committed == "winter"


def test_hysteresis_agreeing_day_resets_the_run():
    state = HysteresisState(committed="winter", last_day="2026-02-28")
    base = date(2026, 3, 1)
    _, state = _advance(state, "summer", base)                       # count 1
    _, state = _advance(state, "summer", base + timedelta(days=1))    # count 2
    _, state = _advance(state, "winter", base + timedelta(days=2))    # agrees → reset
    assert state.count == 0 and state.pending is None
    # A fresh summer run must now start from zero (so 2 more days aren't enough).
    committed, state = _advance(state, "summer", base + timedelta(days=3))
    assert committed == "winter" and state.count == 1


def test_hysteresis_does_not_double_count_within_one_day():
    # The 5-min control loop calls this many times a day; only ONE advance may land per date.
    state = HysteresisState(committed="winter", last_day="2026-02-28")
    day = date(2026, 3, 1)
    committed, state = _advance(state, "summer", day)
    assert state.count == 1
    for _ in range(50):  # a day of control cycles
        committed, state = _advance(state, "summer", day)
    assert committed == "winter" and state.count == 1  # still just one day counted


def test_hysteresis_state_survives_a_simulated_restart():
    # Build up two steady days, persist (JSON) + rehydrate as a restart would, then the third day
    # still completes the switch — the counter is not lost across a reboot.
    state = HysteresisState(committed="winter", last_day="2026-02-28")
    base = date(2026, 3, 1)
    _, state = _advance(state, "summer", base)
    _, state = _advance(state, "summer", base + timedelta(days=1))
    restored = HysteresisState.from_json(state.to_json())
    assert restored == state and restored.count == 2
    committed, restored = _advance(restored, "summer", base + timedelta(days=2))
    assert committed == "summer"


def test_hysteresis_from_json_tolerates_garbage():
    assert HysteresisState.from_json(None) == HysteresisState()
    assert HysteresisState.from_json("not json{") == HysteresisState()


def test_resolve_hysteretic_forced_mode_bypasses_and_rebaselines():
    # A forced season is honoured NOW regardless of the pending counter, and clears the memory.
    state = HysteresisState(committed="winter", pending="summer", count=2, last_day="2026-03-01")
    strat, why, new = resolve_strategy_hysteretic(
        datetime(2026, 3, 2, 12, tzinfo=UTC), "summer", AMS, state)
    assert strat == "summer" and "You chose" in why
    assert new.committed == "summer" and new.pending is None and new.count == 0


def test_resolve_hysteretic_auto_dampens_shoulder_switch_with_a_reason():
    # committed winter; a single strong-surplus March day should NOT flip yet, and should explain
    # that it is waiting for the switch to hold.
    state = HysteresisState(committed="winter", last_day="2026-02-28")
    strat, why, new = resolve_strategy_hysteretic(
        datetime(2026, 3, 1, 12, tzinfo=UTC), "auto", AMS, state,
        surplus_kwh=8.0, price_spread_eur=0.05, hysteresis_days=3)
    assert strat == "winter"  # held
    assert "Holding price-smart" in why and "1/3" in why
    assert new.pending == "summer" and new.count == 1
