"""Trading-mode planner — path T vs path Z (SPEC §8.3a / E-11 / B-104).

When `TradingConfig.enabled` is on, compare a buy-to-sell / forced-export plan (path **T**)
against the seasonal house-first plan (path **Z**). Emit T only when projected extra €
(T − Z) ≥ `min_extra_eur` (default €0.50). Year-round; mode-switching only; no watt-tracking.

Path Z values **self-consumption / house-serve** for the already-stored kWh that T would export
(avoided import at peak), so summer AUTO is not treated as €0 under `spot_minus_tax`. Incremental
buy-to-sell kWh has no Z opportunity cost (Z would not buy to sell).

Dry-run / arming floors live outside this module — the planner only emits a `Plan`. Live forced
`DISCHARGE` still needs `control.allow_export_discharge` + the existing Watch-only floors (B-108).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime

from ems.domain import BatteryIntent
from ems.planner import economics
from ems.planner.charge_need import stored_kwh_per_slot
from ems.planner.schedule import SLOT, Plan, PlanSlot
from ems.sources.prices import PriceSlot

_DH = 0.25  # hours per 15-min slot
_EXPORT_MODES = frozenset({"peak_slice", "full_dump"})
_EXPORT_PRICE_MODELS = frozenset(economics.EXPORT_MODELS)
# When no load profile is supplied, assume the house can absorb exported energy at peak
# (conservative Z opportunity — T must still clear the €0.50 bar under spot_minus_tax).
_ASSUMED_HOUSE_LOAD_W = 800.0


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
    assumed_house_load_w: float = _ASSUMED_HOUSE_LOAD_W


def trading_config_from_settings(s: dict) -> TradingConfig:
    """Build TradingConfig from the runtime settings store (canonical planner.* keys)."""
    mode = str(s.get("planner.export_mode", "peak_slice") or "peak_slice")
    if mode not in _EXPORT_MODES:
        mode = "peak_slice"
    export_model = str(s.get("prices.export_price_model", "spot_minus_tax") or "spot_minus_tax")
    if export_model not in _EXPORT_PRICE_MODELS:
        export_model = "spot_minus_tax"
    return TradingConfig(
        enabled=bool(s.get("planner.trading_enabled", False)),
        min_extra_eur=float(s.get("planner.trading_min_extra_eur", 0.50)),
        max_export_kwh_per_day=float(s.get("planner.max_export_kwh_per_day", 0.0)),
        min_export_kwh=float(s.get("planner.min_export_kwh", 0.5)),
        export_mode=mode,
        max_cycles_per_day=float(s.get("planner.max_cycles_per_day", 1.5)),
        daily_min_savings_eur=float(s.get("planner.daily_min_savings_eur", 0.20)),
        export_price_model=export_model,
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


def _export_credit(spot: float, cfg: TradingConfig) -> float:
    """Feed-in credit from the raw spot (never import-adjusted)."""
    return economics.export_value(
        spot,
        model=cfg.export_price_model,
        energy_tax_eur_per_kwh=cfg.energy_tax_eur_per_kwh,
        fixed_feed_in_eur_per_kwh=cfg.fixed_feed_in_eur_per_kwh,
    )


def _slot_export_ac_kwh(cfg: TradingConfig) -> float:
    return max(0.0, cfg.max_discharge_w) * _DH / 1000.0


def _slot_charge_dc_kwh(cfg: TradingConfig) -> float:
    return stored_kwh_per_slot(cfg.max_charge_w, cfg.round_trip_efficiency)


def _eta_discharge(cfg: TradingConfig) -> float:
    return math.sqrt(max(1e-6, min(1.0, cfg.round_trip_efficiency)))


def _house_kwh_at(
    start: datetime,
    cfg: TradingConfig,
    load_w_by: dict[datetime, float] | None,
) -> float:
    if load_w_by is not None and start in load_w_by:
        return max(0.0, float(load_w_by[start])) * _DH / 1000.0
    if load_w_by is not None:
        # Profile present but this slot missing — no house demand assumed.
        return 0.0
    return max(0.0, cfg.assumed_house_load_w) * _DH / 1000.0


def score_z_house_opportunity(
    prices: list[PriceSlot],
    cfg: TradingConfig,
    *,
    existing_ac_kwh: float,
    load_w_by: dict[datetime, float] | None,
    exclude_starts: set | None = None,
) -> float:
    """€ value of serving the house with already-stored energy (path Z opportunity cost).

    Ranks slots by import price and serves up to `existing_ac_kwh` into house load. Wear is
    subtracted so T/Z compare on the same delivered-energy basis. Buy-to-sell kWh is NOT included —
    Z would not buy that energy.
    """
    if existing_ac_kwh <= 1e-9:
        return 0.0
    exclude = exclude_starts or set()
    slot_ac = _slot_export_ac_kwh(cfg)
    ranked = sorted(
        (p for p in prices if p.start not in exclude),
        key=lambda p: (-_import_price(p, cfg), p.start),
    )
    remaining = existing_ac_kwh
    total = 0.0
    wear = cfg.degradation_eur_per_kwh
    for p in ranked:
        if remaining <= 1e-9:
            break
        house = _house_kwh_at(p.start, cfg, load_w_by)
        if house <= 1e-9:
            continue
        take = min(remaining, house, slot_ac)
        if take <= 1e-9:
            continue
        # Avoided import minus wear (charge cost is sunk for already-stored energy).
        total += (_import_price(p, cfg) - wear) * take
        remaining -= take
    return total


def score_z_plan(
    z_plan: Plan,
    prices: list[PriceSlot],
    cfg: TradingConfig,
    *,
    existing_ac_kwh: float = 0.0,
    load_w_by: dict[datetime, float] | None = None,
) -> float:
    """Path Z €: max of seasonal load-arb intents and house-serve on stored kWh."""
    price_by = {p.start: _import_price(p, cfg) for p in prices}
    charge = [s for s in z_plan.slots if s.intent is BatteryIntent.GRID_CHARGE_TO_TARGET]
    discharge = [s for s in z_plan.slots if s.intent is BatteryIntent.DISCHARGE_FOR_LOAD]
    arb = 0.0
    if discharge:
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
        for s in discharge:
            kwh = float(s.target_kwh) if s.target_kwh is not None else slot_ac
            benefit = price_by.get(s.start, 0.0) - be
            if benefit > 0:
                arb += benefit * kwh
    house = score_z_house_opportunity(
        prices, cfg, existing_ac_kwh=existing_ac_kwh, load_w_by=load_w_by,
    )
    return max(arb, house)


def score_t_plan(
    charge_starts: set,
    export_starts: set,
    spot_by: dict,
    import_by: dict,
    cfg: TradingConfig,
    *,
    export_ac_kwh: float,
    charge_dc_kwh: float,
) -> float:
    """Projected net € for path T (buy-to-sell + export) after wear.

    Export credits use raw spot; charge cost uses import-adjusted prices.
    """
    if not export_starts or export_ac_kwh <= 1e-9:
        return 0.0
    if charge_starts:
        avg_charge = sum(import_by[t] for t in charge_starts) / len(charge_starts)
    else:
        avg_charge = min(import_by.values(), default=0.0)
    be = economics.breakeven(
        avg_charge,
        round_trip_efficiency=cfg.round_trip_efficiency,
        degradation_eur_per_kwh=cfg.degradation_eur_per_kwh,
        risk_margin_eur_per_kwh=cfg.risk_margin_eur_per_kwh,
    )
    per_slot = export_ac_kwh / len(export_starts)
    revenue = sum(_export_credit(spot_by[t], cfg) * per_slot for t in export_starts)
    # Bought portion priced at breakeven; already-stored portion only pays wear (sunk charge).
    eta = _eta_discharge(cfg)
    bought_ac = min(export_ac_kwh, charge_dc_kwh * eta)
    from_store_ac = max(0.0, export_ac_kwh - bought_ac)
    cost = be * bought_ac + cfg.degradation_eur_per_kwh * from_store_ac
    return revenue - cost


def _all_auto_plan(prices: list[PriceSlot], now, cfg: TradingConfig, *, reason: str) -> Plan:
    """Whole-day no-trade AUTO (decision 9) — no load-arbitrage, no export."""
    horizon = [p for p in prices if p.start + SLOT > now][: cfg.horizon_slots]
    floor = cfg.reserve_soc_pct
    slots = tuple(
        PlanSlot(
            p.start, BatteryIntent.ALLOW_SELF_CONSUMPTION,
            f"{reason} (€{p.eur_per_kwh:.2f}/kWh)",
            floor_soc=floor,
        )
        for p in horizon
    )
    return Plan(created_at=now, slots=slots, strategy="auto")


def _build_path_t(
    prices: list[PriceSlot],
    now,
    cfg: TradingConfig,
    *,
    soc_pct: float,
) -> tuple[Plan | None, float, float, str]:
    """Build a candidate trading plan.

    Returns (plan|None, projected_T_eur, existing_ac_exported, skip_reason).
    `existing_ac_exported` is the portion of export from already-stored energy (Z opportunity).
    """
    horizon = [p for p in prices if p.start + SLOT > now][: cfg.horizon_slots]
    if not horizon:
        return None, 0.0, 0.0, "Geen handel: geen prijsvensters over."

    spot_by = {p.start: p.eur_per_kwh for p in horizon}
    import_by = {p.start: _import_price(p, cfg) for p in horizon}
    eta = _eta_discharge(cfg)
    reserve_kwh = cfg.reserve_soc_pct / 100.0 * cfg.usable_kwh
    avail_now = max(0.0, soc_pct / 100.0 * cfg.usable_kwh - reserve_kwh)
    headroom = max(0.0, cfg.usable_kwh - reserve_kwh - avail_now)

    slot_export_ac = _slot_export_ac_kwh(cfg)
    slot_charge_dc = _slot_charge_dc_kwh(cfg)
    if slot_export_ac <= 1e-9 or cfg.max_discharge_w <= 0:
        return None, 0.0, 0.0, "Geen handel: geen ontlaadvermogen (probe)."

    by_cheap = sorted(horizon, key=lambda p: (import_by[p.start], p.start))
    charge_pool = by_cheap[: cfg.charge_slots]
    charge_price = max((import_by[p.start] for p in charge_pool), default=0.0)
    be = economics.breakeven(
        charge_price,
        round_trip_efficiency=cfg.round_trip_efficiency,
        degradation_eur_per_kwh=cfg.degradation_eur_per_kwh,
        risk_margin_eur_per_kwh=cfg.risk_margin_eur_per_kwh,
    )

    def export_net(p: PriceSlot) -> float:
        return _export_credit(p.eur_per_kwh, cfg) - be

    by_export = sorted(horizon, key=lambda p: (-export_net(p), p.start))
    profitable = [
        p for p in by_export
        if export_net(p) > 1e-9 and _export_credit(p.eur_per_kwh, cfg) > 0
    ]
    if not profitable:
        return None, 0.0, 0.0, (
            f"Geen handel: feed-in na model ({cfg.export_price_model}) ≤ break-even "
            f"€{be:.2f}/kWh."
        )

    if cfg.export_mode == "full_dump":
        export_candidates = profitable
    else:
        export_candidates = profitable[: cfg.discharge_slots]

    cycle_kwh_budget = cfg.max_cycles_per_day * 2.0 * cfg.usable_kwh
    max_export_from_cycles = cycle_kwh_budget / 2.0
    day_cap = (
        cfg.max_export_kwh_per_day
        if cfg.max_export_kwh_per_day and cfg.max_export_kwh_per_day > 0
        else float("inf")
    )

    export_starts: list = []
    export_ac = 0.0
    for p in export_candidates:
        if export_ac + slot_export_ac > day_cap + 1e-9:
            break
        if export_ac + slot_export_ac > max_export_from_cycles + 1e-9:
            break
        needed_dc = (export_ac + slot_export_ac) / eta
        if needed_dc > avail_now + headroom + 1e-9:
            break
        export_starts.append(p.start)
        export_ac += slot_export_ac

    if export_ac < cfg.min_export_kwh - 1e-9:
        return None, 0.0, 0.0, (
            f"Geen handel: exportbare surplus {export_ac:.1f} kWh < drempel "
            f"{cfg.min_export_kwh:.1f} kWh."
        )

    needed_dc = export_ac / eta
    buy_dc = max(0.0, min(needed_dc - avail_now, headroom))
    n_charge = math.ceil(buy_dc / slot_charge_dc) if slot_charge_dc > 1e-9 and buy_dc > 1e-9 else 0
    first_export = min(export_starts)
    charge_pool_pre = [p for p in charge_pool if p.start < first_export]
    charge_starts = {p.start for p in charge_pool_pre[:n_charge]}
    stored_dc = min(buy_dc, len(charge_starts) * slot_charge_dc) if charge_starts else 0.0
    total_dc = avail_now + stored_dc
    deliverable_ac = total_dc * eta
    if deliverable_ac < cfg.min_export_kwh - 1e-9:
        return None, 0.0, 0.0, (
            f"Geen handel: onvoldoende energie boven reserve "
            f"(~{deliverable_ac:.1f} kWh leverbaar)."
        )
    while export_starts and len(export_starts) * slot_export_ac > deliverable_ac + 1e-9:
        export_starts.pop()
    if not export_starts:
        return None, 0.0, 0.0, "Geen handel: geen exportslots na energiebudget."
    export_ac = len(export_starts) * slot_export_ac
    export_set = set(export_starts)

    # Trim to cycle budget instead of abandoning (Grok nit).
    while export_starts and cfg.usable_kwh > 0:
        efc = (stored_dc + export_ac / eta) / (2.0 * cfg.usable_kwh)
        if efc <= cfg.max_cycles_per_day + 1e-9:
            break
        export_starts.pop()
        export_ac = len(export_starts) * slot_export_ac
        export_set = set(export_starts)
    if not export_starts or export_ac < cfg.min_export_kwh - 1e-9:
        return None, 0.0, 0.0, (
            f"Geen handel: cycle-budget op (>{cfg.max_cycles_per_day:.1f} EFC) "
            f"na trim onder min export."
        )

    # Re-size buy after trim.
    needed_dc = export_ac / eta
    buy_dc = max(0.0, min(needed_dc - avail_now, headroom))
    n_charge = math.ceil(buy_dc / slot_charge_dc) if slot_charge_dc > 1e-9 and buy_dc > 1e-9 else 0
    charge_starts = {p.start for p in charge_pool_pre[:n_charge]}
    stored_dc = min(buy_dc, len(charge_starts) * slot_charge_dc) if charge_starts else 0.0

    t_eur = score_t_plan(
        charge_starts, export_set, spot_by, import_by, cfg,
        export_ac_kwh=export_ac, charge_dc_kwh=stored_dc,
    )
    existing_ac = min(export_ac, avail_now * eta)

    if t_eur < cfg.daily_min_savings_eur - 1e-9:
        return None, t_eur, existing_ac, (
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

    plan = Plan(
        created_at=now, slots=tuple(out), strategy="trading",
        target_soc=target_soc, deadline=first_export,
    )
    return plan, t_eur, existing_ac, ""


def maybe_apply_trading(
    z_plan: Plan,
    prices: list[PriceSlot],
    now,
    cfg: TradingConfig | None,
    *,
    soc_pct: float,
    load_w_by: dict[datetime, float] | None = None,
) -> Plan:
    """Overlay trading when enabled and path T beats path Z by ≥ min_extra_eur.

    On `daily_min` failure → whole-day AUTO (decision 9). On T-vs-Z loss / unbuildable T
    (except daily_min) → keep seasonal Z unchanged.
    """
    if cfg is None or not cfg.enabled:
        return z_plan
    t_plan, t_eur, existing_ac, skip = _build_path_t(prices, now, cfg, soc_pct=soc_pct)
    if t_plan is None:
        if "hele dag no-trade" in skip:
            return _all_auto_plan(prices, now, cfg, reason=skip)
        return z_plan
    z_eur = score_z_plan(
        z_plan, prices, cfg, existing_ac_kwh=existing_ac, load_w_by=load_w_by,
    )
    extra = t_eur - z_eur
    if extra < cfg.min_extra_eur - 1e-9:
        return z_plan
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
    load_w_by: dict[datetime, float] | None = None,
) -> dict:
    """Diagnostics for tests / UI: whether T would win and why not."""
    if not cfg.enabled:
        return {
            "enabled": False, "would_trade": False,
            "reason": "Geen handel: trading staat uit (alleen huis/zelfconsumptie).",
            "t_eur": 0.0, "z_eur": 0.0, "extra_eur": 0.0,
        }
    t_plan, t_eur, existing_ac, skip = _build_path_t(prices, now, cfg, soc_pct=soc_pct)
    z_eur = score_z_plan(
        z_plan, prices, cfg, existing_ac_kwh=existing_ac, load_w_by=load_w_by,
    )
    if t_plan is None:
        return {
            "enabled": True, "would_trade": False, "reason": skip,
            "t_eur": t_eur, "z_eur": z_eur, "extra_eur": t_eur - z_eur,
            "whole_day_auto": "hele dag no-trade" in skip,
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
