"""Cost-optimal charge/discharge schedule by dynamic programming (SPEC §8 — the 'out-of-the-box'
optimizer). Instead of a greedy heuristic, this finds the GLOBALLY cheapest plan over the horizon
given the price + solar + load forecast, respecting SoC bounds, power limits and round-trip losses.

Why DP not an ML model: the objective (minimise grid cost, never go below reserve) is known and
exact, the state is 1-D (SoC) and small, and we have no logged forecast/outcome pairs to train on
yet. DP gives the optimum, is interpretable, and needs no training data. It doubles as a yardstick:
how close is the fast heuristic to optimal? Pure + unit-tested.

State: SoC discretised to `soc_step_kwh`. Stage: each 15-min slot. Action: a battery AC power from
−max_charge..+max_discharge. Backward induction minimises Σ slot-cost; forward pass reads the plan.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime

from ems.domain import BatteryIntent
from ems.planner.schedule import SLOT, Plan, PlanSlot
from ems.sources.forecast import ForecastSlot
from ems.sources.prices import PriceSlot

_DH = 0.25
_INF = float("inf")


@dataclass(frozen=True)
class OptimalConfig:
    usable_kwh: float
    reserve_soc_pct: float = 10.0
    round_trip_efficiency: float = 0.90
    max_charge_w: float = 4000.0
    max_discharge_w: float = 4000.0
    degradation_eur_per_kwh: float = 0.05  # wear charged against stored throughput
    export_factor: float = 1.0  # export credited at price × this (1.0 = net, <1 = feed-in haircut)
    soc_step_kwh: float = 0.3  # SoC discretisation
    power_step_w: float = 500.0  # action discretisation
    horizon_slots: int = 96


def plan_optimal(
    prices: list[PriceSlot],
    forecast: list[ForecastSlot],
    now: datetime,
    *,
    soc_pct: float,
    load_w_by: dict[datetime, float],
    cfg: OptimalConfig,
) -> Plan:
    future = [p for p in prices if p.start + SLOT > now][: cfg.horizon_slots]
    if not future:
        return Plan(created_at=now, slots=())

    p50 = {f.start: f.p50_w for f in forecast}
    eta = math.sqrt(max(1e-6, min(1.0, cfg.round_trip_efficiency)))
    usable = cfg.usable_kwh
    reserve_kwh = cfg.reserve_soc_pct / 100.0 * usable
    step = cfg.soc_step_kwh
    n = max(2, round(usable / step) + 1)  # SoC levels 0..usable
    reserve_lvl = max(0, math.ceil(reserve_kwh / step))

    def lvl_kwh(i: int) -> float:
        return i * step

    # Candidate battery AC powers (+ discharge / − charge), incl. idle.
    powers = [0.0]
    p = cfg.power_step_w
    while p <= cfg.max_discharge_w + 1e-6:
        powers.append(p)
        p += cfg.power_step_w
    p = cfg.power_step_w
    while p <= cfg.max_charge_w + 1e-6:
        powers.append(-p)
        p += cfg.power_step_w

    def step_cost(slot: PriceSlot, battery_w: float) -> float:
        grid = load_w_by.get(slot.start, 0.0) - p50.get(slot.start, 0.0) - battery_w
        imp = max(0.0, grid) * _DH / 1000.0
        exp = max(0.0, -grid) * _DH / 1000.0
        charge_kwh = max(0.0, -battery_w) * _DH / 1000.0
        return (imp * slot.eur_per_kwh - exp * slot.eur_per_kwh * cfg.export_factor
                + charge_kwh * cfg.degradation_eur_per_kwh)

    def next_lvl(i: int, battery_w: float) -> int | None:
        soc = lvl_kwh(i)
        if battery_w < 0:  # charging: store gains AC·eta
            soc += (-battery_w) * eta * _DH / 1000.0
        elif battery_w > 0:  # discharging: store loses AC/eta
            soc -= battery_w / eta * _DH / 1000.0
        if soc < reserve_kwh - 1e-9 or soc > usable + 1e-9:
            return None  # forbid crossing reserve / capacity
        return min(n - 1, max(0, round(soc / step)))

    # Backward induction. value[i] = min cost-to-go from this slot at SoC level i.
    value = [0.0] * n
    policy: list[list[float]] = []  # per slot: best battery_w for each level
    for slot in reversed(future):
        nxt = value
        value = [_INF] * n
        best = [0.0] * n
        for i in range(reserve_lvl, n):
            for bw in powers:
                nl = next_lvl(i, bw)
                if nl is None:
                    continue
                c = step_cost(slot, bw) + nxt[nl]
                if c < value[i]:
                    value[i] = c
                    best[i] = bw
        policy.append(best)
    policy.reverse()

    # Forward pass from the current SoC.
    cur = min(n - 1, max(reserve_lvl, round((soc_pct / 100.0 * usable) / step)))
    out: list[PlanSlot] = []
    for idx, slot in enumerate(future):
        bw = policy[idx][cur] if value else 0.0
        if bw < -1e-6:
            intent, reason = BatteryIntent.GRID_CHARGE_TO_TARGET, \
                f"optimal: charge at €{slot.eur_per_kwh:.2f}/kWh"
        elif bw > 1e-6:
            intent, reason = BatteryIntent.DISCHARGE_FOR_LOAD, \
                f"optimal: discharge into €{slot.eur_per_kwh:.2f}/kWh"
        else:
            intent, reason = BatteryIntent.ALLOW_SELF_CONSUMPTION, "optimal: hold / self-consume"
        out.append(PlanSlot(slot.start, intent, reason))
        nl = next_lvl(cur, bw)
        cur = nl if nl is not None else cur
    return Plan(created_at=now, slots=tuple(out))
