"""Advisory-only "best time to charge the car" (docs/v2-ev-control.md: EV *control* is explicitly
out of scope for v2 — this module never commands anything, HA integration or otherwise). Given
the day-ahead price curve + the solar P50 forecast, it finds the cheapest CONTIGUOUS window long
enough to add the needed energy before the car's usual departure, and reports it as a plain
sentence for a dashboard card. Pure — no clock reads, no I/O; `now` is supplied by the caller.

Effective cost per slot: while the solar forecast shows an expected surplus (P50 at/above
`surplus_threshold_w`), charging the car from the grid instead of self-consuming means that solar
would otherwise have been EXPORTED — so that slot's kWh is priced at `economics.export_value`
(what the export is worth) rather than the full retail price. Under `net_metering` (today's Dutch
saldering, pre-2027) the two are equal, so the advice collapses to pure price arbitrage; once a
dynamic export model (`spot_minus_tax`/`fixed`) applies, a surplus slot can be far cheaper than its
sticker price, and the recommended window shifts toward the sunny hours — see
`ems/planner/economics.py`'s module docstring for the post-2027 context.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Protocol

from ems.planner.economics import export_value

SLOT = timedelta(minutes=15)
SLOT_HOURS = 0.25


class _PriceLike(Protocol):
    start: datetime
    eur_per_kwh: float


def _fmt_duration(hours: float) -> str:
    total_minutes = round(hours * 60)
    h, m = divmod(total_minutes, 60)
    if h and m:
        return f"{h}h{m}m"
    if h:
        return f"{h}h"
    return f"{m}m"


def advise_charge_window(
    price_slots: list[_PriceLike],
    p50_by_slot: dict[datetime, float],
    *,
    departure: datetime,
    kwh_needed: float,
    charger_kw: float,
    export_model: str = "net_metering",
    energy_tax_eur_per_kwh: float = 0.13,
    fixed_feed_in_eur_per_kwh: float = 0.01,
    surplus_threshold_w: float = 1000.0,
    now: datetime | None = None,
) -> dict | None:
    """The cheapest CONTIGUOUS window (in `price_slots`) long enough to add `kwh_needed` at
    `charger_kw`, ending before `departure`. Returns None when the inputs don't add up to a real
    window (no energy needed, no candidate slots, or not enough of them) — never raises."""
    if kwh_needed <= 0 or charger_kw <= 0:
        return None
    duration_slots = math.ceil(kwh_needed / charger_kw / SLOT_HOURS)
    if duration_slots <= 0:
        return None

    candidates = sorted(
        (s for s in price_slots if (now is None or s.start >= now) and s.start < departure),
        key=lambda s: s.start,
    )
    if len(candidates) < duration_slots:
        return None

    kwh_per_slot = kwh_needed / duration_slots

    def slot_cost(s: _PriceLike) -> tuple[float, bool]:
        p50 = p50_by_slot.get(s.start, 0.0)
        surplus = p50 >= surplus_threshold_w
        price = (
            export_value(
                s.eur_per_kwh, model=export_model,
                energy_tax_eur_per_kwh=energy_tax_eur_per_kwh,
                fixed_feed_in_eur_per_kwh=fixed_feed_in_eur_per_kwh,
            )
            if surplus else s.eur_per_kwh
        )
        return price * kwh_per_slot, surplus

    best_start_idx: int | None = None
    best_cost: float | None = None
    best_surplus_slots = 0
    n = len(candidates)
    for i in range(n - duration_slots + 1):
        window = candidates[i:i + duration_slots]
        # Only a run of back-to-back 15-min slots is an actionable "plug in at HH:MM" window.
        if any(window[k + 1].start - window[k].start != SLOT for k in range(len(window) - 1)):
            continue
        cost = 0.0
        surplus_slots = 0
        for s in window:
            c, surplus = slot_cost(s)
            cost += c
            surplus_slots += surplus
        if best_cost is None or cost < best_cost - 1e-9:  # strict: ties keep the earliest window
            best_cost, best_start_idx, best_surplus_slots = cost, i, surplus_slots

    if best_start_idx is None:
        return None

    window = candidates[best_start_idx:best_start_idx + duration_slots]
    start, end = window[0].start, window[-1].start + SLOT
    solar_share_pct = round(100 * best_surplus_slots / duration_slots)
    duration_label = _fmt_duration(duration_slots * SLOT_HOURS)
    reason = f"Cheapest {duration_label} window before your {departure.strftime('%H:%M')} departure"
    reason += (
        f" — {solar_share_pct}% overlaps expected solar surplus."
        if solar_share_pct > 0 else "."
    )
    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "est_cost_eur": round(best_cost, 2),
        "solar_share_pct": solar_share_pct,
        "slots": duration_slots,
        "reason": reason,
    }
