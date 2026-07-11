"""Rule-based winter-arbitrage planner (SPEC §8.3, simplified first cut).

Charge the cheapest window, discharge the expensive peaks — but ONLY when the spread beats
round-trip losses + degradation + a risk margin (the profitability test). On a flat/low-spread
day it returns no-trade (all ALLOW_SELF_CONSUMPTION). M-later will add target-SoC, deadlines,
the projected-SoC curve, and the ML planner behind the same Plan interface.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import datetime

from ems.domain import BatteryIntent
from ems.planner import economics
from ems.planner.schedule import SLOT, Plan, PlanSlot
from ems.sources.prices import PriceSlot

_log = logging.getLogger("ems.planner.rule_based")
_DH = 0.25  # hours per 15-min slot


@dataclass(frozen=True)
class PlannerConfig:
    round_trip_efficiency: float = 0.90
    degradation_eur_per_kwh: float = 0.05
    risk_margin_eur_per_kwh: float = 0.02
    charge_slots: int = 12  # ~3h of the cheapest slots
    discharge_slots: int = 24  # up to ~6h of the most expensive slots
    horizon_slots: int = 96  # next ~24h


def _all_auto(prices: list[PriceSlot], now: datetime, note: str) -> Plan:
    slots = tuple(
        PlanSlot(
            p.start,
            BatteryIntent.ALLOW_SELF_CONSUMPTION,
            f"{note} (€{p.eur_per_kwh:.2f}/kWh)",
        )
        for p in prices
    )
    return Plan(created_at=now, slots=slots, strategy="winter")


def plan_rule_based(
    prices: list[PriceSlot],
    now: datetime,
    cfg: PlannerConfig | None = None,
    *,
    soc_pct: float = 0.0,
    load_w_by: dict[datetime, float] | None = None,
    usable_kwh: float = 10.0,
    reserve_soc_pct: float = 10.0,
    max_charge_w: float = 4000.0,
) -> Plan:
    """Winter arbitrage: charge the cheap window, discharge the profitable peaks. When a load
    profile + battery sizing are supplied it is **demand-sized** (energy review P1.2): the cheap-
    window charge is sized to the energy the expensive (discharge) window will actually need above
    the reserve already in the pack, and the charge slots carry a target SoC + deadline. Without a
    load profile it falls back to the original fixed-count behaviour (no target)."""
    cfg = cfg or PlannerConfig()
    horizon = [p for p in prices if p.start + SLOT > now][: cfg.horizon_slots]
    if not horizon:
        return Plan(created_at=now, slots=(), strategy="winter")

    by_price = sorted(horizon, key=lambda p: p.eur_per_kwh)
    charge_candidates = by_price[: cfg.charge_slots]
    charge_price = max((p.eur_per_kwh for p in charge_candidates), default=0.0)
    # A discharge slot only pays if it beats the cost of the energy we'd store to serve it.
    breakeven = economics.breakeven(
        charge_price,
        round_trip_efficiency=cfg.round_trip_efficiency,
        degradation_eur_per_kwh=cfg.degradation_eur_per_kwh,
        risk_margin_eur_per_kwh=cfg.risk_margin_eur_per_kwh,
    )

    by_price_desc = sorted(horizon, key=lambda p: -p.eur_per_kwh)
    discharge_set = {
        p.start for p in by_price_desc[: cfg.discharge_slots] if p.eur_per_kwh > breakeven
    }
    if not discharge_set:
        # No profitable peak -> no-trade: never cycle the battery for nothing (SPEC §8.3).
        return _all_auto(horizon, now, "no-trade: spread below break-even")

    first_peak = min(discharge_set)
    eta = math.sqrt(max(1e-6, min(1.0, cfg.round_trip_efficiency)))
    target_soc: float | None = None
    floor = reserve_soc_pct if load_w_by is not None else None
    per_slot_kwh = round(max_charge_w * _DH / 1000.0 * eta, 3) if load_w_by is not None else None
    if load_w_by is not None:
        # Demand-sized: the energy the expensive window needs from the battery, above what's already
        # stored over reserve. Size the cheap-window charge (pre-peak) to exactly that shortfall.
        reserve_kwh = reserve_soc_pct / 100.0 * usable_kwh
        avail_now_kwh = max(0.0, soc_pct / 100.0 * usable_kwh - reserve_kwh)
        peak_load_kwh = sum(load_w_by.get(d, 0.0) for d in discharge_set) * _DH / 1000.0
        # Sizing to LOAD: if the peak window has no house load to serve, there's nothing to shave —
        # don't discharge for price alone (this system doesn't export). Treat as no-trade.
        if peak_load_kwh <= 1e-9:
            return _all_auto(horizon, now, "no-trade: no house load in the expensive window")
        shortfall_dc = max(0.0, peak_load_kwh / eta - avail_now_kwh)
        slot_kwh = max_charge_w * _DH / 1000.0 * eta
        n_charge = math.ceil(shortfall_dc / slot_kwh) if slot_kwh > 0 and shortfall_dc > 1e-9 else 0
        # Pool = every slot before the LAST profitable peak that is strictly worth buying — i.e.
        # charge + round-trip losses + wear + risk still undercut the cheapest peak it would
        # displace. NOT just the window before the FIRST peak: replanned while a peak is already
        # in progress that window is empty, and a profitable valley BETWEEN peaks (buy €0.14
        # midday, cover the €0.30 evening) would be skipped entirely (B-30, seen live 2026-07-02).
        # n_charge caps the result; cheapest-first keeps the buys in the valley floor.
        last_need = max(discharge_set)
        peak_min = min(p.eur_per_kwh for p in horizon if p.start in discharge_set)
        # Inverse of economics.breakeven: solved for the highest charge price that still undercuts
        # `peak_min` after losses + wear + risk. Kept local — it's the reverse direction (sizing the
        # buy pool), not the forward break-even gate above.
        max_buy = (peak_min - cfg.degradation_eur_per_kwh
                   - cfg.risk_margin_eur_per_kwh) * cfg.round_trip_efficiency
        pool = sorted((p for p in horizon if p.start < last_need and p.eur_per_kwh <= max_buy),
                      key=lambda p: (p.eur_per_kwh, p.start))
        charge_set = {p.start for p in pool[:n_charge]}
        if len(pool) < n_charge:  # not enough cheap room before the last peak → will under-charge
            _log.warning("winter planner under-charge: need %d cheap pre-peak slots, only %d "
                         "available (shortfall %.2f kWh) — battery may enter the peak short",
                         n_charge, len(pool), shortfall_dc)
        target_soc = min(100.0, (reserve_kwh + avail_now_kwh + shortfall_dc) / usable_kwh * 100.0)
    else:
        charge_set = {p.start for p in charge_candidates}

    out: list[PlanSlot] = []
    for p in horizon:
        has_later_discharge = any(d > p.start for d in discharge_set)
        if p.start in charge_set and has_later_discharge:
            # Deadline = the peak this charge actually feeds (the next discharge after it) — the
            # first peak may already be in the past when replanning mid-peak.
            next_peak = min(d for d in discharge_set if d > p.start)
            out.append(PlanSlot(
                p.start, BatteryIntent.GRID_CHARGE_TO_TARGET,
                f"charge: cheap window €{p.eur_per_kwh:.2f}/kWh", target_soc=target_soc,
                target_kwh=per_slot_kwh,
                power_w=(max_charge_w if load_w_by is not None else None),
                floor_soc=floor, deadline=next_peak,
            ))
            continue
        if p.start in charge_set:
            # Cheap, but no profitable peak remains to discharge into -> don't cycle for nothing.
            intent = BatteryIntent.ALLOW_SELF_CONSUMPTION
            reason = f"self-consumption: cheap but no peak ahead (€{p.eur_per_kwh:.2f}/kWh)"
        elif p.start in discharge_set:
            intent = BatteryIntent.DISCHARGE_FOR_LOAD
            reason = f"discharge: €{p.eur_per_kwh:.2f}/kWh > break-even €{breakeven:.2f}"
        elif has_later_discharge and any(c < p.start for c in charge_set):
            intent = BatteryIntent.HOLD_RESERVE
            reason = f"hold cheap energy for the coming peak (now €{p.eur_per_kwh:.2f}/kWh)"
        else:
            intent = BatteryIntent.ALLOW_SELF_CONSUMPTION
            reason = f"self-consumption (€{p.eur_per_kwh:.2f}/kWh)"
        out.append(PlanSlot(p.start, intent, reason, floor_soc=floor))
    return Plan(created_at=now, slots=tuple(out), strategy="winter", target_soc=target_soc,
                deadline=first_peak)
