"""B-104 / B-109: trading planner T-vs-Z with canned prices (no hardware)."""
from datetime import UTC, datetime

from ems.domain import BatteryIntent
from ems.planner.rule_based import PlannerConfig, plan_rule_based
from ems.planner.schedule import SLOT, Plan, PlanSlot
from ems.planner.strategy import build_plan
from ems.planner.summer import SummerConfig
from ems.planner.trading import (
    TradingConfig,
    evaluate_trading,
    maybe_apply_trading,
)
from ems.sources.prices import PriceSlot

T0 = datetime(2026, 1, 15, 0, 0, tzinfo=UTC)  # winter day
SUMMER = datetime(2026, 7, 15, 0, 0, tzinfo=UTC)


def _spread_day(n: int = 96, *, cheap: float = 0.05, peak: float = 0.55, start=T0):
    """Cheap night (0–6h) + expensive evening peak (17–21h); rest mid."""
    out = []
    for i in range(n):
        h = (start + i * SLOT).hour
        if 0 <= h < 6:
            price = cheap
        elif 17 <= h < 21:
            price = peak
        else:
            price = 0.18
        out.append(PriceSlot(start + i * SLOT, price))
    return out


def _flat(n: int = 96, price: float = 0.20, start=T0):
    return [PriceSlot(start + i * SLOT, price) for i in range(n)]


def _z_auto(prices, now) -> Plan:
    return Plan(
        created_at=now,
        slots=tuple(
            PlanSlot(p.start, BatteryIntent.ALLOW_SELF_CONSUMPTION, "self")
            for p in prices if p.start + SLOT > now
        ),
        strategy="summer",
    )


def _cfg(**kw) -> TradingConfig:
    base = dict(
        enabled=True,
        min_extra_eur=0.50,
        max_export_kwh_per_day=0.0,
        min_export_kwh=0.5,
        export_mode="peak_slice",
        max_cycles_per_day=1.5,
        daily_min_savings_eur=0.20,
        export_price_model="spot_minus_tax",
        energy_tax_eur_per_kwh=0.13,
        max_charge_w=4000.0,
        max_discharge_w=4000.0,
        usable_kwh=10.0,
        reserve_soc_pct=10.0,
        charge_slots=16,
        discharge_slots=16,
        degradation_eur_per_kwh=0.05,
        risk_margin_eur_per_kwh=0.02,
        round_trip_efficiency=0.90,
        # Empty house → Z opportunity on stored kWh is ~0; buy-to-sell can clear €0.50.
        assumed_house_load_w=0.0,
    )
    base.update(kw)
    return TradingConfig(**base)


def test_trading_off_returns_z_unchanged():
    prices = _spread_day()
    z = _z_auto(prices, T0)
    out = maybe_apply_trading(z, prices, T0, _cfg(enabled=False), soc_pct=80.0)
    assert out is z
    assert out.strategy == "summer"
    assert not any(s.intent is BatteryIntent.EXPORT_FOR_PROFIT for s in out.slots)


def test_t_beats_z_emits_export_and_buy_to_sell():
    """Wide spread + empty house → EXPORT_FOR_PROFIT + cheap charge when T clears €0.50."""
    prices = _spread_day(cheap=0.04, peak=0.60)
    z = _z_auto(prices, T0)
    cfg = _cfg(enabled=True, min_extra_eur=0.50, assumed_house_load_w=0.0)
    out = maybe_apply_trading(z, prices, T0, cfg, soc_pct=40.0)
    assert out.strategy == "trading"
    intents = {s.intent for s in out.slots}
    assert BatteryIntent.EXPORT_FOR_PROFIT in intents
    assert BatteryIntent.GRID_CHARGE_TO_TARGET in intents
    price_by = {p.start: p.eur_per_kwh for p in prices}
    charge_p = [price_by[s.start] for s in out.slots
                if s.intent is BatteryIntent.GRID_CHARGE_TO_TARGET]
    export_p = [price_by[s.start] for s in out.slots
                if s.intent is BatteryIntent.EXPORT_FOR_PROFIT]
    assert charge_p and export_p and max(charge_p) < min(export_p)
    for s in out.slots:
        if s.intent is BatteryIntent.EXPORT_FOR_PROFIT:
            assert s.power_w == cfg.max_discharge_w
            assert s.floor_soc == cfg.reserve_soc_pct
    diag = evaluate_trading(z, prices, T0, cfg, soc_pct=40.0)
    assert diag["would_trade"] is True and diag["extra_eur"] >= 0.50


def test_summer_auto_with_house_load_does_not_treat_z_as_zero():
    """BLOCKER fix: summer AUTO + house load → Z values avoided import (not €0).

    Heavy evening load absorbs stored kWh at full import; under spot_minus_tax that
    opportunity cost keeps T from beating Z by €0.50.
    """
    prices = _spread_day(cheap=0.04, peak=0.60)
    z = _z_auto(prices, T0)
    # Peak hours: 2 kW house load so Z can soak the stored surplus at import prices.
    load = {
        p.start: 2000.0 for p in prices if 17 <= p.start.hour < 21
    }
    cfg = _cfg(enabled=True, min_extra_eur=0.50, assumed_house_load_w=800.0)
    diag = evaluate_trading(z, prices, T0, cfg, soc_pct=90.0, load_w_by=load)
    assert diag["z_eur"] > 0.0
    out = maybe_apply_trading(z, prices, T0, cfg, soc_pct=90.0, load_w_by=load)
    assert out.strategy == "summer"
    assert not any(s.intent is BatteryIntent.EXPORT_FOR_PROFIT for s in out.slots)


def test_under_threshold_keeps_z():
    """Thin spread after spot_minus_tax → no export."""
    prices = _spread_day(cheap=0.10, peak=0.28)
    z = _z_auto(prices, T0)
    cfg = _cfg(enabled=True, min_extra_eur=0.50, assumed_house_load_w=800.0)
    out = maybe_apply_trading(z, prices, T0, cfg, soc_pct=50.0)
    assert out.strategy == "summer"
    assert not any(s.intent is BatteryIntent.EXPORT_FOR_PROFIT for s in out.slots)


def test_no_buy_to_sell_charge_when_t_loses():
    prices = _flat(price=0.20)
    z = plan_rule_based(prices, T0, PlannerConfig())
    out = maybe_apply_trading(z, prices, T0, _cfg(enabled=True), soc_pct=50.0)
    assert not any(s.intent is BatteryIntent.EXPORT_FOR_PROFIT for s in out.slots)


def test_spot_minus_tax_blocks_thin_or_negative_export():
    prices = _spread_day(cheap=0.10, peak=0.20)
    z = _z_auto(prices, T0)
    cfg = _cfg(enabled=True, export_price_model="spot_minus_tax", energy_tax_eur_per_kwh=0.13)
    diag = evaluate_trading(z, prices, T0, cfg, soc_pct=80.0)
    assert diag["would_trade"] is False
    assert "handel" in diag["reason"].lower()
    out = maybe_apply_trading(z, prices, T0, cfg, soc_pct=80.0)
    assert not any(s.intent is BatteryIntent.EXPORT_FOR_PROFIT for s in out.slots)


def test_daily_min_savings_fail_whole_day_auto():
    """Decision 9: daily_min fail → whole-day AUTO, not seasonal Z with load-arb."""
    prices = _spread_day(cheap=0.05, peak=0.50)
    z = _z_auto(prices, T0)
    z = Plan(
        created_at=T0,
        slots=tuple(
            PlanSlot(p.start, BatteryIntent.DISCHARGE_FOR_LOAD, "peak")
            if 17 <= p.start.hour < 21
            else PlanSlot(p.start, BatteryIntent.ALLOW_SELF_CONSUMPTION, "self")
            for p in prices if p.start + SLOT > T0
        ),
        strategy="winter",
    )
    cfg = _cfg(
        enabled=True, daily_min_savings_eur=50.0, min_extra_eur=0.01,
        assumed_house_load_w=0.0,
    )
    diag = evaluate_trading(z, prices, T0, cfg, soc_pct=40.0)
    assert diag["would_trade"] is False
    assert diag.get("whole_day_auto") is True or "daily_min" in diag["reason"]
    out = maybe_apply_trading(z, prices, T0, cfg, soc_pct=40.0)
    assert out.strategy == "auto"
    assert all(s.intent is BatteryIntent.ALLOW_SELF_CONSUMPTION for s in out.slots)
    assert not any(s.intent is BatteryIntent.DISCHARGE_FOR_LOAD for s in out.slots)
    assert not any(s.intent is BatteryIntent.EXPORT_FOR_PROFIT for s in out.slots)


def test_cycle_budget_trims_or_blocks_trade():
    """Tiny cycle budget trims; if under min export → no trade."""
    prices = _spread_day(cheap=0.04, peak=0.60)
    z = _z_auto(prices, T0)
    cfg = _cfg(enabled=True, max_cycles_per_day=0.01, min_extra_eur=0.01, assumed_house_load_w=0.0)
    diag = evaluate_trading(z, prices, T0, cfg, soc_pct=40.0)
    assert diag["would_trade"] is False
    assert "cycle" in diag["reason"].lower() or "handel" in diag["reason"].lower()


def test_reserve_floor_on_export_slots():
    prices = _spread_day(cheap=0.04, peak=0.60)
    z = _z_auto(prices, T0)
    cfg = _cfg(enabled=True, reserve_soc_pct=15.0, assumed_house_load_w=0.0)
    out = maybe_apply_trading(z, prices, T0, cfg, soc_pct=50.0)
    if out.strategy == "trading":
        for s in out.slots:
            if s.floor_soc is not None:
                assert s.floor_soc >= 15.0 - 1e-9


def test_peak_slice_picks_expensive_slots_not_fixed_clock():
    start = T0
    prices = []
    for i in range(96):
        h = (start + i * SLOT).hour
        if 7 <= h < 9:
            price = 0.40
        elif 17 <= h < 21:
            price = 0.70
        elif 0 <= h < 6:
            price = 0.04
        else:
            price = 0.15
        prices.append(PriceSlot(start + i * SLOT, price))
    z = _z_auto(prices, T0)
    cfg = _cfg(
        enabled=True, export_mode="peak_slice", discharge_slots=8,
        min_extra_eur=0.10, assumed_house_load_w=0.0,
    )
    out = maybe_apply_trading(z, prices, T0, cfg, soc_pct=40.0)
    assert out.strategy == "trading"
    export_hours = [
        s.start.hour for s in out.slots if s.intent is BatteryIntent.EXPORT_FOR_PROFIT
    ]
    assert export_hours
    evening = sum(1 for h in export_hours if 17 <= h < 21)
    morning = sum(1 for h in export_hours if 7 <= h < 9)
    assert evening >= morning


def test_full_dump_uses_more_slots_than_peak_slice():
    prices = _spread_day(cheap=0.04, peak=0.60)
    z = _z_auto(prices, T0)
    slice_plan = maybe_apply_trading(
        z, prices, T0,
        _cfg(export_mode="peak_slice", discharge_slots=4, min_extra_eur=0.10,
             assumed_house_load_w=0.0),
        soc_pct=80.0,
    )
    dump_plan = maybe_apply_trading(
        z, prices, T0,
        _cfg(export_mode="full_dump", discharge_slots=4, min_extra_eur=0.10,
             assumed_house_load_w=0.0),
        soc_pct=80.0,
    )
    if slice_plan.strategy == "trading" and dump_plan.strategy == "trading":
        n_slice = sum(1 for s in slice_plan.slots if s.intent is BatteryIntent.EXPORT_FOR_PROFIT)
        n_dump = sum(1 for s in dump_plan.slots if s.intent is BatteryIntent.EXPORT_FOR_PROFIT)
        assert n_dump >= n_slice


def test_flag_off_no_export_intent():
    prices = _spread_day(cheap=0.04, peak=0.60)
    z = _z_auto(prices, T0)
    out = maybe_apply_trading(z, prices, T0, _cfg(enabled=False), soc_pct=80.0)
    assert not any(s.intent is BatteryIntent.EXPORT_FOR_PROFIT for s in out.slots)


def test_summer_day_with_spread_allows_trading_when_house_empty():
    """Year-round: summer calendar day trades when economics pass (empty house)."""
    prices = _spread_day(cheap=0.04, peak=0.60, start=SUMMER)
    z = Plan(created_at=SUMMER, slots=_z_auto(prices, SUMMER).slots, strategy="summer")
    cfg = _cfg(enabled=True, min_extra_eur=0.50, assumed_house_load_w=0.0)
    out = maybe_apply_trading(z, prices, SUMMER, cfg, soc_pct=40.0)
    assert out.strategy == "trading"
    assert any(s.intent is BatteryIntent.EXPORT_FOR_PROFIT for s in out.slots)


def test_build_plan_wires_trading_overlay():
    """build_plan with empty-house trading_cfg overlays on summer Z."""
    prices = _spread_day(cheap=0.04, peak=0.60)
    cfg = _cfg(enabled=True, min_extra_eur=0.50, assumed_house_load_w=0.0)
    plan = build_plan(
        "summer",
        prices=prices,
        forecast=None,
        now=T0,
        soc_pct=40.0,
        winter_cfg=PlannerConfig(),
        summer_cfg=SummerConfig(usable_kwh=10.0, target_soc_pct=80.0),
        trading_cfg=cfg,
    )
    assert plan.strategy == "trading"
    assert any(s.intent is BatteryIntent.EXPORT_FOR_PROFIT for s in plan.slots)


def test_build_plan_trading_disabled_keeps_seasonal():
    prices = _spread_day(cheap=0.04, peak=0.60)
    plan = build_plan(
        "winter",
        prices=prices,
        forecast=None,
        now=T0,
        soc_pct=40.0,
        winter_cfg=PlannerConfig(),
        summer_cfg=SummerConfig(usable_kwh=10.0, target_soc_pct=80.0),
        trading_cfg=_cfg(enabled=False),
    )
    assert plan.strategy == "winter"
    assert not any(s.intent is BatteryIntent.EXPORT_FOR_PROFIT for s in plan.slots)


def test_nl_reasons_on_acting_export_slots():
    prices = _spread_day(cheap=0.04, peak=0.60)
    out = maybe_apply_trading(
        _z_auto(prices, T0), prices, T0,
        _cfg(enabled=True, assumed_house_load_w=0.0), soc_pct=40.0,
    )
    assert out.strategy == "trading"
    export = [s for s in out.slots if s.intent is BatteryIntent.EXPORT_FOR_PROFIT]
    assert export and all(
        "handelen" in s.reason.lower() or "exporteren" in s.reason.lower() for s in export
    )
    assert "pad T" in out.slots[0].reason or "Handelen" in out.slots[0].reason
