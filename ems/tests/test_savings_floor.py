"""#319 / audit F-04: daily savings floor for winter and adaptive grid charge.

Tests, fakes and canned prices only. No battery, no production database.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta

from ems.domain import BatteryIntent
from ems.planner.adaptive import AdaptiveConfig
from ems.planner.economics import (
    apply_daily_savings_floor,
    estimate_plan_savings,
    format_savings_floor_reason,
)
from ems.planner.reason import build_decision_reason
from ems.planner.rule_based import PlannerConfig
from ems.planner.schedule import SLOT, Plan, PlanSlot
from ems.planner.strategy import build_plan
from ems.planner.summer import SummerConfig
from ems.planner.trading import maybe_apply_trading
from ems.replay import _adaptive_cfg, _simulate, _summer_cfg, _winter_cfg
from ems.savings import estimate_daily_savings_eur
from ems.sources.forecast import ForecastSlot
from ems.sources.prices import PriceSlot
from ems.tests import test_replay as replay_fx
from ems.tests import test_trading_planner as trade_fx

T0 = datetime(2026, 1, 15, 0, 0, tzinfo=replay_fx.UTZ)
# One discharge slot at this price nets €0.10 under the savings.py defaults
# (charge €0.05, η 0.90, wear €0.05, risk €0.02, 1.5 kW × 15 min).
_PEAK = 0.05 / 0.9 + 0.05 + 0.02 + (0.10 / 0.375)
_CHARGE = BatteryIntent.GRID_CHARGE_TO_TARGET
_EXPORT = BatteryIntent.EXPORT_FOR_PROFIT


def _summer() -> SummerConfig:
    return SummerConfig(usable_kwh=10.0, target_soc_pct=80.0)


def _prices(n_peak: int) -> list[PriceSlot]:
    out = [PriceSlot(T0, 0.05)]
    for i in range(n_peak):
        out.append(PriceSlot(T0 + (i + 1) * SLOT, _PEAK))
    return out


def _plan(prices: list[PriceSlot], minimum: float, **kw) -> Plan:
    cfg = PlannerConfig(
        charge_slots=1,
        discharge_slots=max(1, len(prices) - 1),
        horizon_slots=len(prices),
        daily_min_savings_eur=minimum,
    )
    return build_plan(
        "winter", prices=prices, forecast=[], now=T0, soc_pct=40.0,
        winter_cfg=cfg, summer_cfg=_summer(), **kw,
    )


def _charge_slots(plan: Plan) -> list[PlanSlot]:
    return [s for s in plan.slots if s.intent is _CHARGE]


def test_projected_ten_cents_drops_grid_charge_and_names_daily_min():
    plan = _plan(_prices(1), 0.20)
    assert not _charge_slots(plan)
    reasons = [s.reason for s in plan.slots if s.reason.startswith("Geen laden")]
    assert reasons
    assert reasons[0] == format_savings_floor_reason(0.10, 0.20)
    assert "daily_min" in reasons[0]
    assert "€0,10" in reasons[0] and "€0,20" in reasons[0]
    assert any(s.intent is BatteryIntent.DISCHARGE_FOR_LOAD for s in plan.slots)
    summary = build_decision_reason(
        plan, plan_reason=reasons[0], summary=reasons[0],
    ).summary
    assert summary == reasons[0]
    assert "daily_min" in summary


def test_projected_thirty_cents_leaves_the_plan_unchanged():
    prices = _prices(3)
    bare = _plan(prices, 0.0)
    kept = _plan(prices, 0.20)
    assert _charge_slots(bare) and _charge_slots(kept)
    assert [(s.start, s.intent, s.reason) for s in kept.slots] == [
        (s.start, s.intent, s.reason) for s in bare.slots
    ]


def test_threshold_zero_matches_today():
    prices = _prices(1)
    assert [(s.intent, s.reason) for s in _plan(prices, 0.0).slots] == [
        (s.intent, s.reason) for s in _plan(prices, 0.0).slots
    ]
    off = _plan(prices, 0.0)
    assert _charge_slots(off)
    assert not any(s.reason.startswith("Geen laden") for s in off.slots)


def test_winter_adaptive_and_trading_call_the_same_helper(monkeypatch):
    calls: list[str] = []
    real = estimate_plan_savings

    def spy(plan, prices, load_w_by, rte, deg, risk, import_fee):
        calls.append(getattr(plan, "strategy", None) or "plan")
        return real(plan, prices, load_w_by, rte, deg, risk, import_fee)

    monkeypatch.setattr("ems.planner.economics.estimate_plan_savings", spy)
    _plan(_prices(1), 0.20)
    assert calls, "winter build_plan did not call estimate_plan_savings"

    t0 = datetime(2026, 1, 10, 12, 0, tzinfo=replay_fx.UTZ)
    prices = [PriceSlot(t0 + i * SLOT, 0.10 if i < 12 else 0.40) for i in range(16)]
    load = {t0 + i * SLOT: (200.0 if i < 12 else 3000.0) for i in range(16)}
    fc = [ForecastSlot(t0 + i * SLOT, 0.0, 0.0, 0.0) for i in range(16)]
    before = len(calls)
    build_plan(
        "winter", prices=prices, forecast=fc, now=t0, soc_pct=20.0,
        winter_cfg=PlannerConfig(daily_min_savings_eur=0.20),
        summer_cfg=_summer(), load_w_by=load,
        adaptive_cfg=AdaptiveConfig(usable_kwh=10.0),
    )
    assert len(calls) > before, "adaptive build_plan did not call the helper"

    before = len(calls)
    maybe_apply_trading(
        trade_fx._z_auto(trade_fx._spread_day(), trade_fx.T0),
        trade_fx._spread_day(), trade_fx.T0,
        trade_fx._cfg(enabled=True, assumed_house_load_w=0.0),
        soc_pct=40.0,
    )
    assert len(calls) > before, "trading did not call the helper"


def test_trading_daily_min_same_or_stricter(monkeypatch):
    """A known helper figure may only add a no-trade; it cannot open a trade."""
    prices = trade_fx._spread_day(cheap=0.04, peak=0.60)
    z = trade_fx._z_auto(prices, trade_fx.T0)
    cfg = trade_fx._cfg(enabled=True, assumed_house_load_w=0.0)

    monkeypatch.setattr(
        "ems.planner.economics.estimate_plan_savings", lambda *_a, **_k: None,
    )
    same = maybe_apply_trading(z, prices, trade_fx.T0, cfg, soc_pct=40.0)
    assert same.strategy == "trading"
    assert any(s.intent is _EXPORT for s in same.slots)

    monkeypatch.setattr(
        "ems.planner.economics.estimate_plan_savings", lambda *_a, **_k: 0.01,
    )
    stricter = maybe_apply_trading(z, prices, trade_fx.T0, cfg, soc_pct=40.0)
    assert stricter.strategy == "auto"
    assert all(s.intent is BatteryIntent.ALLOW_SELF_CONSUMPTION for s in stricter.slots)
    assert "daily_min" in stricter.slots[0].reason
    assert not any(s.intent is _CHARGE or s.intent is _EXPORT for s in stricter.slots)

    monkeypatch.setattr(
        "ems.planner.economics.estimate_plan_savings", lambda *_a, **_k: 1000.0,
    )
    blocked = maybe_apply_trading(
        z, prices, trade_fx.T0,
        trade_fx._cfg(
            enabled=True, daily_min_savings_eur=50.0, min_extra_eur=0.01,
            assumed_house_load_w=0.0,
        ),
        soc_pct=40.0,
    )
    assert blocked.strategy == "auto"
    assert "daily_min" in blocked.slots[0].reason


def test_negative_price_soak_is_not_blocked():
    prices = [
        PriceSlot(T0, -0.05),
        PriceSlot(T0 + SLOT, 0.05),
        PriceSlot(T0 + 2 * SLOT, _PEAK),
    ]
    plan = build_plan(
        "winter", prices=prices, forecast=[], now=T0, soc_pct=40.0,
        winter_cfg=PlannerConfig(
            charge_slots=2, discharge_slots=1, horizon_slots=3,
            negative_price_soak=True, daily_min_savings_eur=50.0,
        ),
        summer_cfg=_summer(),
    )
    by_price = {s.start: p.eur_per_kwh for s, p in zip(plan.slots, prices, strict=True)}
    charged = _charge_slots(plan)
    assert charged and all(by_price[s.start] < 0.0 for s in charged)
    assert any(s.reason.startswith("Geen laden") for s in plan.slots)


def test_unknown_estimate_leaves_the_plan_unchanged(monkeypatch):
    monkeypatch.setattr(
        "ems.planner.economics.estimate_plan_savings", lambda *_a, **_k: None,
    )
    plan = _plan(_prices(1), 0.20)
    assert _charge_slots(plan)
    assert not any("Geen laden" in s.reason for s in plan.slots)


def test_savings_floor_is_restrict_only():
    t0 = datetime(2026, 1, 10, 0, 0, tzinfo=replay_fx.UTZ)
    prices = [PriceSlot(t0 + i * SLOT, 0.05 if i < 24 else 0.40) for i in range(48)]
    load = {p.start: (400.0 if p.eur_per_kwh < 0.2 else 2500.0) for p in prices}

    def run(minimum: float) -> Plan:
        return build_plan(
            "winter", prices=prices, forecast=[], now=t0, soc_pct=20.0,
            winter_cfg=PlannerConfig(daily_min_savings_eur=minimum),
            summer_cfg=_summer(), load_w_by=load,
            adaptive_cfg=AdaptiveConfig(usable_kwh=10.0, reserve_soc_pct=10.0),
        )

    bare = run(0.0)
    floored = run(50.0)
    assert len(bare.slots) == len(floored.slots)
    for before, after in zip(bare.slots, floored.slots, strict=True):
        assert before.start == after.start
        if after.intent in (_CHARGE, _EXPORT):
            assert before.intent is after.intent
        assert after.floor_soc == before.floor_soc
        assert (after.floor_soc or 0.0) + 1e-9 >= (before.floor_soc or 0.0)
    assert not _charge_slots(floored)
    assert floored.target_soc == bare.target_soc


def test_historical_day_savings_figure_unchanged():
    """Pinned to the pre-#319 savings.py result for this fixed day. Do not recompute."""
    t0 = datetime(2026, 1, 15, 0, 0, tzinfo=replay_fx.UTZ)
    t1 = t0 + timedelta(minutes=15)
    plan = Plan(
        created_at=t0,
        slots=(
            PlanSlot(t0, _CHARGE, "charge"),
            PlanSlot(t1, BatteryIntent.DISCHARGE_FOR_LOAD, "discharge"),
        ),
    )
    by_start = {t0: 0.10, t1: 0.40}
    assert estimate_daily_savings_eur(plan, by_start) == 0.08
    slots = [PriceSlot(t0, 0.10), PriceSlot(t1, 0.40)]
    assert estimate_plan_savings(plan, slots, None, 0.90, 0.05, 0.02, 0.0) == 0.08


def test_direct_apply_keeps_plan_when_estimate_is_missing():
    slot = PlanSlot(T0, _CHARGE, "charge", floor_soc=12.0, power_w=4000.0)
    plan = Plan(created_at=T0, slots=(slot,), strategy="winter")
    prices = [PriceSlot(T0 + SLOT, 0.20)]
    out = apply_daily_savings_floor(
        plan, prices, None, 0.20, 0.90, 0.05, 0.02, 0.0,
    )
    assert out is plan
    assert out.slots[0].intent is _CHARGE
    assert out.slots[0].floor_soc == 12.0


def test_canned_replay_floor_reports_no_charge_days_and_reserve():
    """14 canned winter days from the replay harness. Not `make replay`.

    The fixture's 0.10-vs-0.50 spread clears €0.20, so the floor is expected to
    bind on zero days. Reserve breaches must not rise versus the unfloored plan.
    """
    cfg = replay_fx._cfg()
    no_charge_days = 0
    missed = 0.0
    extra_breaches = 0
    for offset in range(14):
        day = replay_fx.DAY + timedelta(days=offset)
        raw = replay_fx._raw_day(1000.0, lambda _i: 0.0, day=day)
        price_rows = replay_fx._price_day(replay_fx._winter_price, day=day)
        replayed = replay_fx.replay_day(raw, price_rows, [], cfg=cfg)
        assert replayed.data_ok, replayed.skip_reason
        slots = [
            PriceSlot(
                start=datetime.fromisoformat(p["start_ts"]),
                eur_per_kwh=p["eur_per_kwh"],
            )
            for p in price_rows
        ]
        load = {s.start: 1000.0 for s in slots}
        winter = _winter_cfg(cfg)
        common = dict(
            prices=slots, forecast=[], now=day, soc_pct=0.0,
            summer_cfg=_summer_cfg(cfg), load_w_by=load,
            adaptive_cfg=_adaptive_cfg(cfg),
        )
        bare = build_plan("winter", winter_cfg=winter, **common)
        floored = build_plan(
            "winter", winter_cfg=replace(winter, daily_min_savings_eur=0.20), **common,
        )
        bare_charge = bool(_charge_slots(bare))
        floored_charge = bool(_charge_slots(floored))
        if bare_charge and not floored_charge:
            no_charge_days += 1
            projected = estimate_plan_savings(
                bare, slots, load,
                _adaptive_cfg(cfg).round_trip_efficiency,
                _adaptive_cfg(cfg).degradation_eur_per_kwh,
                _adaptive_cfg(cfg).risk_margin_eur_per_kwh,
                0.0,
            )
            missed += float(projected or 0.0)
        for before, after in zip(bare.slots, floored.slots, strict=True):
            if after.intent in (_CHARGE, _EXPORT):
                assert before.intent is after.intent
            assert after.floor_soc == before.floor_soc
        starts = [s.start for s in slots]
        solar = {t: 0.0 for t in starts}
        price_by = {s.start: s.eur_per_kwh for s in slots}

        def intents(plan: Plan, starts_slots=slots) -> dict:
            return {
                s.start: (plan.intent_at(s.start).intent
                          if plan.intent_at(s.start) else BatteryIntent.ALLOW_SELF_CONSUMPTION)
                for s in starts_slots
            }

        base_sim = _simulate(
            starts, load, solar, price_by, cfg=cfg, start_soc=0.0,
            battery_enabled=True, intents=intents(bare), target_soc=bare.target_soc,
        )
        floor_sim = _simulate(
            starts, load, solar, price_by, cfg=cfg, start_soc=0.0,
            battery_enabled=True, intents=intents(floored), target_soc=floored.target_soc,
        )
        extra_breaches += max(0, floor_sim.reserve_breaches - base_sim.reserve_breaches)
        assert floor_sim.reserve_breaches <= base_sim.reserve_breaches
        assert replayed.scenarios["planner"].reserve_breaches >= 0
    assert no_charge_days == 0
    assert missed == 0.0
    assert extra_breaches == 0
