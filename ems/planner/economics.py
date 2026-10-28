"""Shared energy economics: the two money questions the planners and finance both ask.

Post-2027 context (backlog B-05): Dutch net-metering (*saldering*) ends 1 Jan 2027. Until then,
every exported kWh nets against imports at the FULL retail price. After it ends, a dynamic contract
typically pays roughly spot MINUS energy tax for export while imports still cost the full price, or
a contract may pay a FLAT feed-in tariff. Exporting can therefore cost money on negative-spot slots
— that is real and intentional; it must not be clamped away. Pure functions, no I/O.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from typing import Protocol

from ems.domain import BatteryIntent
from ems.economics import EconomicSnapshot
from ems.planner.reason import is_must_charge_slot
from ems.planner.schedule import Plan, PlanSlot
from ems.savings import estimate_daily_savings_eur
from ems.sources.prices import PriceSlot
from ems.tariffs import TariffPolicy

EXPORT_MODELS = ("net_metering", "spot_minus_tax", "fixed")


def breakeven(
    charge_price_eur_per_kwh: float,
    *,
    round_trip_efficiency: float,
    degradation_eur_per_kwh: float,
    risk_margin_eur_per_kwh: float,
) -> float:
    """The sell price a stored kWh must beat to be worth cycling: the charge price grossed up for
    round-trip losses, plus per-kWh wear and a risk margin. Single source of truth for the
    arbitrage gate in `rule_based.py` / `adaptive.py`."""
    snapshot = EconomicSnapshot(
        import_price_eur_per_kwh=charge_price_eur_per_kwh,
        export_price_eur_per_kwh=charge_price_eur_per_kwh,
        round_trip_efficiency=round_trip_efficiency,
        degradation_eur_per_kwh=degradation_eur_per_kwh,
        risk_margin_eur_per_kwh=risk_margin_eur_per_kwh,
    )
    return snapshot.delivered_energy_cost()


def max_buy_for_peak(peak_price: float, rte: float, deg: float, risk: float) -> float:
    """Highest charge price that still undercuts `peak_price` after losses, wear and risk.

    Inverse of `breakeven`: one kWh bought at this price and delivered at `peak_price` is
    break-even. This is the only buy ceiling (audit F-08, F-10). Winter used to inline
    `(peak - degradation - risk) * round_trip_efficiency`; that expression lives here now.
    """
    snapshot = EconomicSnapshot(
        import_price_eur_per_kwh=float(peak_price),
        export_price_eur_per_kwh=float(peak_price),
        round_trip_efficiency=float(rte),
        degradation_eur_per_kwh=float(deg),
        risk_margin_eur_per_kwh=float(risk),
    )
    return snapshot.break_even_import_price(float(peak_price))


class _BuyCeilingCfg(Protocol):
    """PlannerConfig, AdaptiveConfig or SummerConfig: the three fields the ceiling reads."""

    round_trip_efficiency: float
    degradation_eur_per_kwh: float
    risk_margin_eur_per_kwh: float


def max_buy_for_planner_config(peak_price: float, cfg: _BuyCeilingCfg) -> float:
    """PlannerConfig / AdaptiveConfig / SummerConfig adapter for `max_buy_for_peak`.

    Reads `round_trip_efficiency`, `degradation_eur_per_kwh` and `risk_margin_eur_per_kwh`
    off the config. No new settings — the same three fields the planners already carry.
    """
    return max_buy_for_peak(
        float(peak_price),
        float(cfg.round_trip_efficiency),
        float(cfg.degradation_eur_per_kwh),
        float(cfg.risk_margin_eur_per_kwh),
    )


def apply_max_buy_cap(auto_max_buy: float, cap_eur_per_kwh: float) -> float:
    """Resident max buy (#324 / F-15): min(auto, cap) when cap > 0; 0 leaves auto unchanged."""
    cap = float(cap_eur_per_kwh)
    if cap <= 0:
        return float(auto_max_buy)
    return min(float(auto_max_buy), cap)


def export_value(
    price_eur_per_kwh: float,
    *,
    model: str = "net_metering",
    energy_tax_eur_per_kwh: float = 0.13,
    fixed_feed_in_eur_per_kwh: float = 0.01,
) -> float:
    """What one exported kWh is worth under the configured feed-in model (see module docstring).

    - net_metering  → the full price (today's saldering: export nets against import).
    - spot_minus_tax → price − energy tax (post-2027 dynamic export). MAY be negative on a
      negative-spot slot — exporting can cost money — so it is deliberately NOT clamped.
    - fixed          → a flat feed-in tariff, independent of spot.

    Product default for settings key `prices.export_price_model` is `spot_minus_tax` (E-11 /
    B-103). This *function* still defaults to `net_metering` so direct library callers stay
    stable; live paths must pass the settings value. Unknown model → net_metering.
    """
    snapshot = EconomicSnapshot(
        import_price_eur_per_kwh=price_eur_per_kwh,
        export_price_eur_per_kwh=price_eur_per_kwh,
        raw_price_eur_per_kwh=price_eur_per_kwh,
        export_model=model,
        energy_tax_eur_per_kwh=energy_tax_eur_per_kwh,
        fixed_feed_in_eur_per_kwh=fixed_feed_in_eur_per_kwh,
    )
    return snapshot.export_credit()


_CHARGE = BatteryIntent.GRID_CHARGE_TO_TARGET
_DISCHARGE = BatteryIntent.DISCHARGE_FOR_LOAD
_EXPORT = BatteryIntent.EXPORT_FOR_PROFIT
_ACTIONABLE = frozenset({_CHARGE, _DISCHARGE, _EXPORT})


def _euro_nl(amount: float) -> str:
    """Two decimals with a Dutch decimal comma, e.g. 0.1 → '€0,10'."""
    return f"€{float(amount):.2f}".replace(".", ",")


def format_savings_floor_reason(projected_eur: float, minimum_eur: float) -> str:
    """Homeowner reason when projected savings sit under planner.daily_min_savings_eur."""
    return (
        f"Geen laden: verwachte besparing {_euro_nl(projected_eur)} < "
        f"minimum {_euro_nl(minimum_eur)} (daily_min)"
    )


def _price_map(prices: list[PriceSlot]) -> dict[datetime, float]:
    return {p.start: float(p.eur_per_kwh) for p in prices}


def estimate_plan_savings(
    plan: Plan | None,
    prices: list[PriceSlot],
    load_w_by: dict[datetime, float] | None,
    rte: float,
    deg: float,
    risk: float,
    import_fee: float,
) -> float | None:
    """Projected net € of executing `plan`.

    Load-arbitrage reuses `ems.savings.estimate_daily_savings_eur` (same charge-worst,
    fixed discharge energy, same rounding). Export slots add a breakeven credit on top
    so a trading plan is not scored as €0. `load_w_by` is the shared signature with
    path Z; the published savings figure does not scale with it, and a missing profile
    is not an unknown estimate.

    Returns None when a charge, discharge or export slot has no price — callers must
    leave the plan unchanged. A plan with no such slots is a known €0, not unknown.
    """
    del load_w_by  # signature shared with trading path Z; see docstring
    if plan is None or not plan.slots or not prices:
        return None
    price_by = _price_map(prices)
    actionable = [s for s in plan.slots if s.intent in _ACTIONABLE]
    if any(s.start not in price_by for s in actionable):
        return None
    policy = TariffPolicy(import_fee_eur_per_kwh=max(0.0, float(import_fee or 0.0)))
    arbitrage = estimate_daily_savings_eur(
        plan,
        price_by,
        efficiency=float(rte),
        degradation_eur_per_kwh=float(deg),
        risk_margin_eur_per_kwh=float(risk),
        tariff_policy=policy,
    )
    export_eur = _export_net_eur(
        plan, price_by, rte=float(rte), deg=float(deg), risk=float(risk),
        import_fee=max(0.0, float(import_fee or 0.0)),
    )
    if export_eur is None:
        return None
    return round(float(arbitrage) + export_eur, 2)


def _export_net_eur(
    plan: Plan,
    price_by: dict[datetime, float],
    *,
    rte: float,
    deg: float,
    risk: float,
    import_fee: float,
) -> float | None:
    """Net € of EXPORT_FOR_PROFIT slots. 0 when the plan does not export.

    Credit is the raw slot price (an upper bound versus spot-minus-tax), so this
    term does not by itself undercut path T's own daily-min gate.
    """
    exports = [s for s in plan.slots if s.intent is _EXPORT]
    if not exports:
        return 0.0
    if any(s.start not in price_by for s in exports):
        return None
    charges = [s for s in plan.slots if s.intent is _CHARGE and s.start in price_by]
    if charges:
        charge_price = max(price_by[s.start] + import_fee for s in charges)
    else:
        charge_price = min(price_by[s.start] + import_fee for s in exports)
    be = breakeven(
        charge_price,
        round_trip_efficiency=rte,
        degradation_eur_per_kwh=deg,
        risk_margin_eur_per_kwh=risk,
    )
    total = 0.0
    for slot in exports:
        if slot.target_kwh is not None and slot.target_kwh > 0:
            kwh = float(slot.target_kwh)
        elif slot.power_w is not None and slot.power_w > 0:
            kwh = float(slot.power_w) * 0.25 / 1000.0
        else:
            kwh = 0.0
        total += (price_by[slot.start] - be) * kwh
    return total


def apply_daily_savings_floor(
    plan: Plan,
    prices: list[PriceSlot],
    load_w_by: dict[datetime, float] | None,
    minimum_eur: float,
    rte: float,
    deg: float,
    risk: float,
    import_fee: float,
) -> Plan:
    """Drop grid-charge slots when the known projection is under `minimum_eur`.

    `minimum_eur <= 0` is off (the plan is returned unchanged). None from
    `estimate_plan_savings` is unknown and also leaves the plan unchanged.
    Only `GRID_CHARGE_TO_TARGET` at a non-negative price is rewritten to
    `ALLOW_SELF_CONSUMPTION`. Negative-price soak stays. Reserve (`floor_soc`)
    is copied unchanged. Must-charge (#323) slots are exempt from this rewrite; manual override
    (#322) is applied later by the decision engine, not as a plan-slot kind here.
    """
    if minimum_eur <= 1e-12:
        return plan
    projected = estimate_plan_savings(
        plan, prices, load_w_by, rte, deg, risk, import_fee,
    )
    if projected is None or projected + 1e-9 >= minimum_eur:
        return plan
    reason = format_savings_floor_reason(projected, minimum_eur)
    price_by = _price_map(prices)
    changed = False
    slots: list[PlanSlot] = []
    for slot in plan.slots:
        price = price_by.get(slot.start)
        soak = slot.intent is _CHARGE and price is not None and price < 0.0
        if is_must_charge_slot(slot):
            slots.append(slot)
            continue
        if slot.intent is not _CHARGE or soak:
            slots.append(slot)
            continue
        changed = True
        slots.append(replace(
            slot,
            intent=BatteryIntent.ALLOW_SELF_CONSUMPTION,
            reason=reason,
            target_kwh=None,
            power_w=None,
        ))
    if not changed:
        return plan
    return replace(plan, slots=tuple(slots))
