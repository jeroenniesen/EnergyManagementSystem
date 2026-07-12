"""Pure EV-charging planner — the "math core" of the EV feature (design 2026-07-12).

Advisory/visual only in v1 (the current charger has no API); this module NEVER commands anything.
Given a weekly schedule already materialized into ascending deadlines `D_1 < … < D_k` (each an
`{ready_by, min_pct, day}`), the day-ahead price curve, and the solar P50 forecast, it decides the
cheapest way to add enough energy to meet every deadline's minimum SoC *by its ready-by time*.

It is deliberately pure: no clock reads, no I/O, no imports beyond stdlib + `ems.planner.economics`.
`now` is supplied by the caller.

Model (design doc "math core"):
  * Battery-side energy need at deadline i:  E_i = max(0, (min_pct_i − soc)/100 × C).
  * SoC is non-decreasing in v1 (no driving model), so the binding cumulative requirement is
    R_i = max(E_1 … E_i). Energy charged in slots ending ≤ D_i must be ≥ R_i for every i.
  * A slot delivers c = P·0.25·η_c battery-kWh and draws P·0.25 kWh on the AC side; its effective
    €/kWh is the full price normally, or `economics.export_value(price)` when the solar forecast
    shows a surplus (P50 ≥ threshold) — under `net_metering` the two are equal (pre-2027 behaviour
    falls out for free); post-2027 a sunny slot can be far cheaper (even negative) than its sticker.
  * Allocation is earliest-deadline-first, cheapest-usable-slots-first, with a fractional marginal
    slot. Because the requirements are nested and the usable-slot sets are nested (a slot usable for
    D_i is usable for every later deadline), the greedy is cost-optimal — an exchange argument the
    brute-force cross-check in the tests pins empirically. Total energy delivered equals R_k
    exactly: we never over-charge, even when a slot's effective price is negative.
  * Honesty at the horizon: a requirement that can't be covered by *priced* slots before its
    deadline is reported as `pending_kwh` (never silently assumed). A separate, price-independent
    physical-feasibility check flags a deadline `feasible: false` (with the `shortfall_kwh`) when
    even charging continuously from `now` at full power cannot deliver R_i in time.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from ems.planner.economics import export_value

SLOT = timedelta(minutes=15)
SLOT_HOURS = 0.25
_TOL = 1e-9


def _slot_fields(slot: Any) -> tuple[datetime, float]:
    """Accept either a `PriceSlot`-like object (`.start`/`.eur_per_kwh`) or a `(start, price)`
    tuple — matching `ev_advisor`'s input convention."""
    start = getattr(slot, "start", None)
    if start is None:
        return slot[0], float(slot[1])
    return start, float(slot.eur_per_kwh)


def _fmt_pct(pct: float) -> str:
    return f"{pct:g}"


def _allocate(
    now: datetime,
    deadlines: list[dict],
    price_slots: list[Any],
    p50_by_slot: dict[datetime, float],
    *,
    soc_pct: float,
    battery_net_kwh: float,
    charge_efficiency: float,
    power_kw: float,
    export_model: str,
    energy_tax_eur_per_kwh: float,
    fixed_feed_in_eur_per_kwh: float,
    surplus_threshold_w: float,
) -> tuple[list[dict], list[dict]]:
    """The full-precision core: price every usable slot, then run the earliest-deadline-first /
    cheapest-slot-first greedy. Returns `(states, deadline_records)` with UNROUNDED figures so the
    brute-force cross-check test can compare exact costs. `plan_car_charging` rounds for output."""
    c = power_kw * SLOT_HOURS * charge_efficiency  # battery-kWh delivered by a full slot
    deadlines = sorted(deadlines, key=lambda d: d["ready_by"])

    # Build the usable-slot working set (start >= now), pre-pricing each slot.
    states: list[dict] = []
    for raw in price_slots:
        start, price = _slot_fields(raw)
        if start < now:  # a slot must not start in the past (now <= start)
            continue
        surplus = p50_by_slot.get(start, 0.0) >= surplus_threshold_w
        eff = (
            export_value(
                price,
                model=export_model,
                energy_tax_eur_per_kwh=energy_tax_eur_per_kwh,
                fixed_feed_in_eur_per_kwh=fixed_feed_in_eur_per_kwh,
            )
            if surplus
            else price
        )
        states.append(
            {"start": start, "eff": eff, "surplus": surplus, "alloc": 0.0, "for_deadline": None}
        )
    states.sort(key=lambda s: s["start"])

    deadline_records: list[dict] = []
    allocated_cumulative = 0.0
    running_r = 0.0
    for d in deadlines:
        ready_by: datetime = d["ready_by"]
        min_pct = d["min_pct"]
        e_i = max(0.0, (min_pct - soc_pct) / 100.0 * battery_net_kwh)
        r_i = max(running_r, e_i)  # non-decreasing binding requirement
        running_r = r_i
        already_met = e_i <= _TOL

        # Deficit still to place in slots ending by this deadline (== R_i − R_{i-1} when every
        # earlier deadline was fully covered; rolls forward any earlier pending, honestly).
        deficit = max(0.0, r_i - allocated_cumulative)
        planned = 0.0
        if deficit > _TOL and c > _TOL:
            candidates = [
                s for s in states if s["start"] + SLOT <= ready_by and (c - s["alloc"]) > _TOL
            ]
            candidates.sort(key=lambda s: (s["eff"], s["start"]))  # cheapest; ties → earlier start
            remaining = deficit
            for s in candidates:
                if remaining <= _TOL:
                    break
                take = min(c - s["alloc"], remaining)
                s["alloc"] += take
                if s["for_deadline"] is None:
                    s["for_deadline"] = ready_by
                remaining -= take
                planned += take
            allocated_cumulative += planned
        pending = max(0.0, deficit - planned)

        # Physical feasibility, INDEPENDENT of prices: can R_i battery-kWh be delivered by charging
        # continuously from now to ready_by at full power? (In v1 no energy is committed before
        # `now`, so the design's `R_i − already_allocated` reduces to R_i.)
        hours = max(0.0, (ready_by - now).total_seconds() / 3600.0)
        max_deliverable = hours * power_kw * charge_efficiency
        shortfall = max(0.0, r_i - max_deliverable)

        deadline_records.append(
            {
                "ready_by": ready_by,
                "min_pct": min_pct,
                "required_kwh": deficit,
                "planned_kwh": planned,
                "pending_kwh": pending,
                "shortfall_kwh": shortfall,
                "already_met": already_met,
                "feasible": shortfall <= _TOL,
            }
        )
    return states, deadline_records


def plan_car_charging(
    now: datetime,
    deadlines: list[dict],
    price_slots: list[Any],
    p50_by_slot: dict[datetime, float],
    *,
    soc_pct: float,
    battery_net_kwh: float,
    charge_efficiency: float = 0.90,
    power_kw: float,
    export_model: str = "net_metering",
    energy_tax_eur_per_kwh: float = 0.13,
    fixed_feed_in_eur_per_kwh: float = 0.01,
    surplus_threshold_w: float = 1000.0,
) -> dict:
    """Plan car charging to meet each deadline's minimum SoC as cheaply as possible.

    `deadlines` are `{ready_by: aware dt, min_pct: number, day: str}`, ascending by `ready_by`
    (materialized elsewhere). `price_slots` are 15-min slots (see `_slot_fields`). Returns the plan
    dict documented in the design doc; money is rounded to 2 dp and energy to 2 dp at the output
    layer only (full precision is kept internally). Assumes `soc_pct` is a real number — the caller
    handles the "no SoC anchor set" case before calling."""
    states, records = _allocate(
        now,
        deadlines,
        price_slots,
        p50_by_slot,
        soc_pct=soc_pct,
        battery_net_kwh=battery_net_kwh,
        charge_efficiency=charge_efficiency,
        power_kw=power_kw,
        export_model=export_model,
        energy_tax_eur_per_kwh=energy_tax_eur_per_kwh,
        fixed_feed_in_eur_per_kwh=fixed_feed_in_eur_per_kwh,
        surplus_threshold_w=surplus_threshold_w,
    )
    min_pct_by_ready = {r["ready_by"]: r["min_pct"] for r in records}

    deadline_outputs = [
        {
            "ready_by": r["ready_by"].isoformat(),
            "min_pct": r["min_pct"],
            "required_kwh": round(r["required_kwh"], 2),
            "planned_kwh": round(r["planned_kwh"], 2),
            "pending_kwh": round(r["pending_kwh"], 2),
            "shortfall_kwh": round(r["shortfall_kwh"], 2),
            "already_met": r["already_met"],
            "feasible": r["feasible"],
        }
        for r in records
    ]

    # --- slots output (one merged row per allocated slot, chronological) ---
    allocated = sorted((s for s in states if s["alloc"] > _TOL), key=lambda s: s["start"])
    slots_out: list[dict] = []
    total_cost = 0.0
    total_battery = 0.0
    for s in allocated:
        ac_kwh = s["alloc"] / charge_efficiency  # AC side: battery = ac × η
        cost = s["eff"] * ac_kwh
        total_cost += cost
        total_battery += s["alloc"]
        slots_out.append(
            {
                "start": s["start"].isoformat(),
                "kw": power_kw,
                "ac_kwh": round(ac_kwh, 2),
                "battery_kwh": round(s["alloc"], 2),
                "eur_per_kwh_effective": round(s["eff"], 4),
                "est_cost_eur": round(cost, 2),
                "solar_surplus": s["surplus"],
                "for_deadline": s["for_deadline"].isoformat(),
            }
        )

    windows = _windows(allocated, charge_efficiency, min_pct_by_ready)
    any_required = any(r["required_kwh"] > _TOL for r in records)
    advice = _advice(windows, allocated, deadlines, min_pct_by_ready, any_required)

    return {
        "soc": soc_pct,
        "deadlines": deadline_outputs,
        "slots": slots_out,
        "windows": windows,
        "advice": advice,
        "total_est_cost_eur": round(total_cost, 2),
        "total_planned_kwh": round(total_battery, 2),
    }


def _windows(
    allocated: list[dict], charge_efficiency: float, min_pct_by_ready: dict[datetime, float]
) -> list[dict]:
    """Merge strictly-consecutive allocated slots into plug-in windows."""
    windows: list[dict] = []
    group: list[dict] = []

    def flush(grp: list[dict]) -> None:
        if not grp:
            return
        start = grp[0]["start"]
        end = grp[-1]["start"] + SLOT
        w_battery = sum(g["alloc"] for g in grp)
        w_ac = sum(g["alloc"] / charge_efficiency for g in grp)
        w_cost = sum(g["eff"] * (g["alloc"] / charge_efficiency) for g in grp)
        n_surplus = sum(1 for g in grp if g["surplus"])
        solar_share_pct = round(100 * n_surplus / len(grp))
        target = min(g["for_deadline"] for g in grp)  # earliest deadline this window serves
        pct = _fmt_pct(min_pct_by_ready[target])
        reason = f"Cheapest slots to reach {pct}% by {target:%a %H:%M}"
        reason += (
            f" — {solar_share_pct}% overlaps expected solar surplus." if solar_share_pct else "."
        )
        windows.append(
            {
                "start": start.isoformat(),
                "end": end.isoformat(),
                "ac_kwh": round(w_ac, 2),
                "battery_kwh": round(w_battery, 2),
                "est_cost_eur": round(w_cost, 2),
                "solar_share_pct": solar_share_pct,
                "reason": reason,
            }
        )

    for s in allocated:
        if group and s["start"] - group[-1]["start"] == SLOT:
            group.append(s)
        else:
            flush(group)
            group = [s]
    flush(group)
    return windows


def _advice(
    windows: list[dict],
    allocated: list[dict],
    deadlines: list[dict],
    min_pct_by_ready: dict[datetime, float],
    any_required: bool,
) -> str:
    if windows:
        w = windows[0]  # windows are chronological; the next one to plug in for
        target: datetime = allocated[0]["for_deadline"]  # earliest allocated slot → its deadline
        start = datetime.fromisoformat(w["start"])
        end = datetime.fromisoformat(w["end"])
        return (
            f"Plug in {start:%a %H:%M}–{end:%H:%M} "
            f"({w['battery_kwh']:.1f} kWh, ≈ €{w['est_cost_eur']:.2f}) "
            f"to reach {_fmt_pct(min_pct_by_ready[target])}% by {target:%a %H:%M}."
        )
    if not deadlines:
        return "No car charging schedule set — nothing to plan."
    if any_required:  # a need exists but no priced slot can serve it yet
        return "No priced charging window yet for the car — will plan once prices arrive."
    return "The car already meets every scheduled minimum — no charging needed."
