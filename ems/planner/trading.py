"""Trading-mode planner — path T vs path Z (SPEC §8.3a / E-11 / B-104).

When `TradingConfig.enabled` is on, compare a buy-to-sell / forced-export plan (path **T**)
against the seasonal house-first plan (path **Z**). Emit T only when projected extra €
(T − Z) ≥ `min_extra_eur` (default €0.50). Year-round; mode-switching only; no watt-tracking.

Dry-run / arming floors live outside this module — the planner only emits a `Plan`. Live forced
`DISCHARGE` still needs `control.allow_export_discharge` + the existing Watch-only floors (B-108).
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from ems.domain import BatteryIntent
from ems.planner import economics
from ems.planner.charge_need import stored_kwh_per_slot
from ems.planner.schedule import SLOT, Plan, PlanSlot
from ems.sources.prices import PriceSlot

_DH = 0.25  # hours per 15-min slot
_EXPORT_MODES = frozenset({"peak_slice", "full_dump"})


@dataclass(frozen=True)
class TradingConfig:
    """Runtime trading overlay (settings `planner.trading_*` + economics). Default OFF."""

    enabled: bool = False
    min_extra_eur: float = 0.50
    max_export_kwh_per_day: float = 0.0  # 0 = uncapped within reserve
    min_export_kwh: float = 0.5
    export_mode: str = "peak_slice"  # peak_slice | full_dump
    max_cycles_per_day: float = 1.5
    daily_min_savings_eur: float = 0.20
    export_price_model: str = "spot_minus_tax"
    energy_tax_eur_per_kwh: float = 0.13
    fixed_feed_in_eur_per_kwh: float = 0.01
    round_trip_efficiency: float = 0.90
    degradation_eur_per_kwh: float = 0.05
    risk_margin_eur_per_kwh: float = 0.02
    charge_slots: int = 12
    discharge_slots: int = 24  # peak_slice window size; full_dump ignores this ceiling
    horizon_slots: int = 96
    max_charge_w: float = 4000.0
    max_discharge_w: float = 4000.0
    usable_kwh: float = 10.0
    reserve_soc_pct: float = 10.0
    import_fee_eur_per_kwh: float = 0.0
    tibber_total_includes_all: bool = False


def trading_config_from_settings(s: dict) -> TradingConfig:
    """Build TradingConfig from the runtime settings store (canonical planner.* keys)."""
    mode = str(s.get("planner.export_mode", "peak_slice") or "peak_slice")
    if mode not in _EXPORT_MODES:
        mode = "peak_slice"
    return TradingConfig(
        enabled=bool(s.get("planner.trading_enabled", False)),
        min_extra_eur=float(s.get("planner.trading_min_extra_eur", 0.50)),
        max_export_kwh_per_day=float(s.get("planner.max_export_kwh_per_day", 0.0)),
        min_export_kwh=float(s.get("planner.min_export_kwh", 0.5)),
        export_mode=mode,
        max_cycles_per_day=float(s.get("planner.max_cycles_per_day", 1.5)),
        daily_min_savings_eur=float(s.get("planner.daily_min_savings_eur", 0.20)),
        export_price_model=str(s.get("prices.export_price_model", "spot_minus_tax")),
        energy_tax_eur_per_kwh=float(s.get("prices.energy_tax_eur_per_kwh", 0.13)),
        fixed_feed_in_eur_per_kwh=float(s.get("prices.fixed_feed_in_eur_per_kwh", 0.01)),
        round_trip_efficiency=float(s.get("planner.round_trip_efficiency", 0.90)),
        degradation_eur_per_kwh=float(s.get("planner.degradation_eur_per_kwh", 0.05)),
        risk_margin_eur_per_kwh=float(s.get("planner.risk_margin_eur_per_kwh", 0.02)),
        charge_slots=int(s.get("planner.charge_slots", 12)),
        discharge_slots=int(s.get("planner.discharge_slots", 24)),
        max_charge_w=float(s.get("battery.max_charge_w", 4000.0)),
        max_discharge_w=float(s.get("battery.max_discharge_w", 4000.0)),
        usable_kwh=float(s.get("battery.usable_kwh", 10.0)),
        reserve_soc_pct=float(s.get("battery.min_reserve_soc", 10.0)),
        import_fee_eur_per_kwh=(
            float(s.get("grid_fees.import_fee_eur_per_kwh", 0.0))
            if not s.get("grid_fees.tibber_total_includes_all", False) else 0.0
        ),
        tibber_total_includes_all=bool(s.get("grid_fees.tibber_total_includes_all", False)),
    )


def _import_price(p: PriceSlot, cfg: TradingConfig) -> float:
    if cfg.tibber_total_includes_all:
        return p.eur_per_kwh
    return p.eur_per_kwh + cfg.import_fee_eur_per_kwh


def _export_credit(price: float, cfg: TradingConfig) -> float:
    return economics.export_value(
        price,
        model=cfg.export_price_model,
        energy_tax_eur_per_kwh=cfg.energy_tax_eur_per_kwh,
        fixed_feed_in_eur_per_kwh=cfg.fixed_feed_in_eur_per_kwh,
    )


def _slot_export_ac_kwh(cfg: TradingConfig) -> float:
    return max(0.0, cfg.max_discharge_w) * _DH / 1000.0


def _slot_charge_dc_kwh(cfg: TradingConfig) -> float:
    return stored_kwh_per_slot(cfg.max_charge_w, cfg.round_trip_efficiency)


def score_z_plan(z_plan: Plan, prices: list[PriceSlot], cfg: TradingConfig) -> float:
    """Projected € for path Z (house-first / load-arbitrage) vs doing nothing.

    Credits avoided import on DISCHARGE_FOR_LOAD slots at (import − breakeven), using the
    plan's own charge window as the opportunity cost. Pure export is never credited here.
    """
    price_by = {p.start: _import_price(p, cfg) for p in prices}
    charge = [s for s in z_plan.slots if s.intent is BatteryIntent.GRID_CHARGE_TO_TARGET]
    discharge = [s for s in z_plan.slots if s.intent is BatteryIntent.DISCHARGE_FOR_LOAD]
    if not discharge:
        return 0.0
    if charge:
        charge_price = max(price_by.get(s.start, 0.0) for s in charge)
    else:
        charge_price = min(price_by.values(), default=0.0)
    be = economics.breakeven(
        charge_price,
        round_trip_efficiency=cfg.round_trip_efficiency,
        degradation_eur_per_kwh=cfg.degradation_eur_per_kwh,
        risk_margin_eur_per_kwh=cfg.risk_margin_eur_per_kwh,
    )
    slot_ac = _slot_export_ac_kwh(cfg)
    total = 0.0
    for s in discharge:
        kwh = float(s.target_kwh) if s.target_kwh is not None else slot_ac
        price = price_by.get(s.start, 0.0)
        benefit = price - be
        if benefit > 0:
            total += benefit * kwh
    return total


def score_t_plan(
    charge_starts: set,
    export_starts: set,
    price_by: dict,
    cfg: TradingConfig,
    *,
    export_ac_kwh: float,
    charge_dc_kwh: float,
) -> float:
    """Projected net € for path T (buy-to-sell + export) after wear."""
    if not export_starts or export_ac_kwh <= 1e-9:
        return 0.0
    # Average charge price (import) weighted equally across charge slots used.
    if charge_starts:
        avg_charge = sum(price_by[t] for t in charge_starts) / len(charge_starts)
    else:
        avg_charge = min(price_by.values(), default=0.0)
    be = economics.breakeven(
        avg_charge,
        round_trip_efficiency=cfg.round_trip_efficiency,
        degradation_eur_per_kwh=cfg.degradation_eur_per_kwh,
        risk_margin_eur_per_kwh=cfg.risk_margin_eur_per_kwh,
    )
    # Revenue from export credits minus the delivered-energy cost of the charged kWh.
    # Spread evenly across export slots for the credit side.
    per_slot = export_ac_kwh / len(export_starts)
    revenue = sum(_export_credit(price_by[t], cfg) * per_slot for t in export_starts)
    # Cost of energy delivered: breakeven already includes wear+risk per delivered kWh.
    cost = be * export_ac_kwh
    return revenue - cost


def _build_path_t(
    prices: list[PriceSlot],
    now,
    cfg: TradingConfig,
    *,
    soc_pct: float,
) -> tuple[Plan | None, float, str]:
    """Build a candidate trading plan. Returns (plan|None, projected_T_eur, skip_reason)."""
    horizon = [p for p in prices if p.start + SLOT > now][: cfg.horizon_slots]
    if not horizon:
        return None, 0.0, "Geen handel: geen prijsvensters over."

    price_by = {p.start: _import_price(p, cfg) for p in horizon}
    eta = math.sqrt(max(1e-6, min(1.0, cfg.round_trip_efficiency)))
    reserve_kwh = cfg.reserve_soc_pct / 100.0 * cfg.usable_kwh
    avail_now = max(0.0, soc_pct / 100.0 * cfg.usable_kwh - reserve_kwh)
    headroom = max(0.0, cfg.usable_kwh - reserve_kwh - avail_now)

    slot_export_ac = _slot_export_ac_kwh(cfg)
    slot_charge_dc = _slot_charge_dc_kwh(cfg)
    if slot_export_ac <= 1e-9 or cfg.max_discharge_w <= 0:
        return None, 0.0, "Geen handel: geen ontlaadvermogen (probe)."

    # Charge candidates: cheapest first.
    by_cheap = sorted(horizon, key=lambda p: (_import_price(p, cfg), p.start))
    charge_pool = by_cheap[: cfg.charge_slots]
    charge_price = max((_import_price(p, cfg) for p in charge_pool), default=0.0)
    be = economics.breakeven(
        charge_price,
        round_trip_efficiency=cfg.round_trip_efficiency,
        degradation_eur_per_kwh=cfg.degradation_eur_per_kwh,
        risk_margin_eur_per_kwh=cfg.risk_margin_eur_per_kwh,
    )

    # Export candidates: rank by export credit (spot_minus_tax), require credit > breakeven and > 0.
    def export_net(p: PriceSlot) -> float:
        return _export_credit(p.eur_per_kwh, cfg) - be

    by_export = sorted(horizon, key=lambda p: (-export_net(p), p.start))
    profitable = [
        p for p in by_export
        if export_net(p) > 1e-9 and _export_credit(p.eur_per_kwh, cfg) > 0
    ]
    if not profitable:
        return None, 0.0, (
            f"Geen handel: feed-in na model ({cfg.export_price_model}) ≤ break-even "
            f"€{be:.2f}/kWh."
        )

    # Slot budget by policy.
    if cfg.export_mode == "full_dump":
        export_candidates = profitable  # empty surplus across all profitable peaks
    else:
        export_candidates = profitable[: cfg.discharge_slots]

    # Energy budget: available now + what we may buy (headroom), capped by cycle + day export cap.
    cycle_kwh_budget = cfg.max_cycles_per_day * 2.0 * cfg.usable_kwh  # charge+discharge sum
    # Reserve half the cycle budget conceptually for each direction; clamp by headroom/avail.
    max_export_from_cycles = cycle_kwh_budget / 2.0
    day_cap = (
        cfg.max_export_kwh_per_day
        if cfg.max_export_kwh_per_day and cfg.max_export_kwh_per_day > 0
        else float("inf")
    )

    # Buy-to-sell: charge enough DC so after η we can export the planned AC, within headroom.
    # First pick export slots greedily, then size charge to match.
    export_starts: list = []
    export_ac = 0.0
    for p in export_candidates:
        if export_ac + slot_export_ac > day_cap + 1e-9:
            break
        if export_ac + slot_export_ac > max_export_from_cycles + 1e-9:
            break
        # Need enough energy: avail_now + charged, converted to AC (×η for discharge side ≈ √η).
        # Delivered AC from DC store ≈ dc * η_discharge = dc * √rte.
        needed_dc = (export_ac + slot_export_ac) / eta
        if needed_dc > avail_now + headroom + 1e-9:
            break
        export_starts.append(p.start)
        export_ac += slot_export_ac

    if export_ac < cfg.min_export_kwh - 1e-9:
        return None, 0.0, (
            f"Geen handel: exportbare surplus {export_ac:.1f} kWh < drempel "
            f"{cfg.min_export_kwh:.1f} kWh."
        )

    needed_dc = export_ac / eta
    buy_dc = max(0.0, needed_dc - avail_now)
    buy_dc = min(buy_dc, headroom)
    n_charge = math.ceil(buy_dc / slot_charge_dc) if slot_charge_dc > 1e-9 and buy_dc > 1e-9 else 0
    # Only charge before the first export slot.
    first_export = min(export_starts)
    charge_pool_pre = [p for p in charge_pool if p.start < first_export]
    charge_starts = {p.start for p in charge_pool_pre[:n_charge]}
    stored_dc = min(buy_dc, len(charge_starts) * slot_charge_dc) if charge_starts else 0.0
    # If we couldn't buy enough and avail_now alone can't cover min export, skip.
    total_dc = avail_now + stored_dc
    deliverable_ac = total_dc * eta
    if deliverable_ac < cfg.min_export_kwh - 1e-9:
        return None, 0.0, (
            f"Geen handel: onvoldoende energie boven reserve "
            f"(~{deliverable_ac:.1f} kWh leverbaar)."
        )
    # Trim export to what we can actually deliver.
    while export_starts and len(export_starts) * slot_export_ac > deliverable_ac + 1e-9:
        export_starts.pop()
    if not export_starts:
        return None, 0.0, "Geen handel: geen exportslots na energiebudget."
    export_ac = len(export_starts) * slot_export_ac
    export_set = set(export_starts)

    # Cycle check on actual volumes.
    charge_ac_equiv = stored_dc  # DC into pack
    if cfg.usable_kwh > 0:
        efc = (charge_ac_equiv + export_ac / eta) / (2.0 * cfg.usable_kwh)
    else:
        efc = 0.0
    if efc > cfg.max_cycles_per_day + 1e-9:
        return None, 0.0, (
            f"Geen handel: cycle-budget op ({efc:.2f} > {cfg.max_cycles_per_day:.1f} EFC)."
        )

    t_eur = score_t_plan(
        charge_starts, export_set, price_by, cfg,
        export_ac_kwh=export_ac, charge_dc_kwh=stored_dc,
    )
    if t_eur < cfg.daily_min_savings_eur - 1e-9:
        return None, t_eur, (
            f"Geen handel: geprojecteerde besparing €{t_eur:.2f} < "
            f"daily_min €{cfg.daily_min_savings_eur:.2f} — hele dag no-trade."
        )

    target_soc = min(
        100.0,
        (reserve_kwh + avail_now + stored_dc) / cfg.usable_kwh * 100.0,
    ) if cfg.usable_kwh > 0 else None
    floor = cfg.reserve_soc_pct
    per_charge = round(slot_charge_dc, 3)
    per_export = round(slot_export_ac, 3)

    out: list[PlanSlot] = []
    for p in horizon:
        if p.start in charge_starts:
            out.append(PlanSlot(
                p.start, BatteryIntent.GRID_CHARGE_TO_TARGET,
                f"handelen: laden goedkoop €{p.eur_per_kwh:.2f}/kWh (buy-to-sell)",
                target_soc=target_soc, target_kwh=per_charge,
                power_w=cfg.max_charge_w, floor_soc=floor, deadline=first_export,
            ))
        elif p.start in export_set:
            credit = _export_credit(p.eur_per_kwh, cfg)
            out.append(PlanSlot(
                p.start, BatteryIntent.EXPORT_FOR_PROFIT,
                f"handelen: exporteren feed-in €{credit:.2f}/kWh > break-even €{be:.2f} "
                f"(model: {cfg.export_price_model})",
                target_kwh=per_export, power_w=cfg.max_discharge_w,
                floor_soc=floor, deadline=None,
            ))
        elif any(c < p.start for c in charge_starts) and any(e > p.start for e in export_set):
            out.append(PlanSlot(
                p.start, BatteryIntent.HOLD_RESERVE,
                f"handelen: vasthouden voor exportpiek (nu €{p.eur_per_kwh:.2f}/kWh)",
                floor_soc=floor,
            ))
        else:
            out.append(PlanSlot(
                p.start, BatteryIntent.ALLOW_SELF_CONSUMPTION,
                f"zelfconsumptie (€{p.eur_per_kwh:.2f}/kWh)",
                floor_soc=floor,
            ))

    note = (
        f"pad T: ~{export_ac:.1f} kWh export, {cfg.export_mode}, "
        f"geprojecteerd €{t_eur:.2f}"
    )
    plan = Plan(
        created_at=now, slots=tuple(out), strategy="trading",
        target_soc=target_soc, deadline=first_export,
    )
    # Stamp a plan-level hint via first slot reason prefix is enough; callers use strategy=.
    _ = note
    return plan, t_eur, ""


def maybe_apply_trading(
    z_plan: Plan,
    prices: list[PriceSlot],
    now,
    cfg: TradingConfig | None,
    *,
    soc_pct: float,
) -> Plan:
    """Overlay trading when enabled and path T beats path Z by ≥ min_extra_eur.

    Returns `z_plan` unchanged when trading is off, T loses, or T is unbuildable. When returning
    Z after a failed gate, prepends a not-acting reason onto ALLOW_SELF_CONSUMPTION slots is
    avoided (keep Z byte-stable); the skip reason is available via `evaluate_trading` for tests/UI.
    """
    if cfg is None or not cfg.enabled:
        return z_plan
    t_plan, t_eur, skip = _build_path_t(prices, now, cfg, soc_pct=soc_pct)
    if t_plan is None:
        return z_plan
    z_eur = score_z_plan(z_plan, prices, cfg)
    extra = t_eur - z_eur
    if extra < cfg.min_extra_eur - 1e-9:
        return z_plan
    # Annotate plan: rewrite first slot reason to name the T-vs-Z win (explainability).
    if t_plan.slots:
        head = t_plan.slots[0]
        win = (
            f"Handelen: pad T levert €{extra:.2f} meer op dan pad Z "
            f"(T €{t_eur:.2f} − Z €{z_eur:.2f} ≥ drempel €{cfg.min_extra_eur:.2f}). "
            f"{head.reason}"
        )
        slots = (PlanSlot(
            head.start, head.intent, win,
            target_soc=head.target_soc, target_kwh=head.target_kwh,
            power_w=head.power_w, floor_soc=head.floor_soc, deadline=head.deadline,
            end=head.end,
        ),) + t_plan.slots[1:]
        return Plan(
            created_at=t_plan.created_at, slots=slots, strategy="trading",
            target_soc=t_plan.target_soc, deadline=t_plan.deadline,
            id=t_plan.id, version=t_plan.version, input_snapshot=t_plan.input_snapshot,
            planner_mode=t_plan.planner_mode,
        )
    return t_plan


def evaluate_trading(
    z_plan: Plan,
    prices: list[PriceSlot],
    now,
    cfg: TradingConfig,
    *,
    soc_pct: float,
) -> dict:
    """Diagnostics for tests / UI: whether T would win and why not."""
    if not cfg.enabled:
        return {
            "enabled": False, "would_trade": False,
            "reason": "Geen handel: trading staat uit (alleen huis/zelfconsumptie).",
            "t_eur": 0.0, "z_eur": 0.0, "extra_eur": 0.0,
        }
    t_plan, t_eur, skip = _build_path_t(prices, now, cfg, soc_pct=soc_pct)
    z_eur = score_z_plan(z_plan, prices, cfg)
    if t_plan is None:
        return {
            "enabled": True, "would_trade": False, "reason": skip,
            "t_eur": t_eur, "z_eur": z_eur, "extra_eur": t_eur - z_eur,
        }
    extra = t_eur - z_eur
    if extra < cfg.min_extra_eur - 1e-9:
        return {
            "enabled": True, "would_trade": False,
            "reason": (
                f"Geen handel: T−Z €{extra:.2f} < drempel €{cfg.min_extra_eur:.2f} "
                f"(T €{t_eur:.2f}, Z €{z_eur:.2f})."
            ),
            "t_eur": t_eur, "z_eur": z_eur, "extra_eur": extra,
        }
    return {
        "enabled": True, "would_trade": True,
        "reason": (
            f"Handelen: pad T levert €{extra:.2f} meer op dan pad Z "
            f"(≥ €{cfg.min_extra_eur:.2f})."
        ),
        "t_eur": t_eur, "z_eur": z_eur, "extra_eur": extra,
        "export_slots": sum(
            1 for s in t_plan.slots if s.intent is BatteryIntent.EXPORT_FOR_PROFIT
        ),
        "charge_slots": sum(
            1 for s in t_plan.slots if s.intent is BatteryIntent.GRID_CHARGE_TO_TARGET
        ),
    }
