"""Advisory-only cheapest contiguous start window for appliances (#227 / epic #222).

Pure — no clock reads, no I/O, never starts a device and never touches the battery.
Mirrors the contiguous 15-min search in ``ems/ev_advisor.py`` without solar/export economics.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Protocol

SLOT = timedelta(minutes=15)
SLOT_HOURS = 0.25

# Rough cycle energy for cost display only (homeowner estimate, not metered).
_ASSUMED_KWH: dict[str, float] = {
    "washer": 1.0,
    "dryer": 2.0,
    "dishwasher": 1.2,
    "other": 1.0,
    "unknown": 1.0,
}


class _PriceLike(Protocol):
    start: datetime
    eur_per_kwh: float


def _fmt_duration(slots: int) -> str:
    total_minutes = round(slots * SLOT_HOURS * 60)
    h, m = divmod(total_minutes, 60)
    if h and m:
        return f"{h}h{m}m"
    if h:
        return f"{h}h"
    return f"{m}m"


def propose_start_window(
    price_slots: list[_PriceLike],
    *,
    duration_slots: int,
    now: datetime,
    horizon_end: datetime | None = None,
    assumed_kwh: float | None = 1.0,
) -> dict | None:
    """Cheapest contiguous ``duration_slots`` window in ``[now, horizon_end)``.

    Returns None when inputs cannot form a real window. Ties keep the earliest window.
    Never raises.
    """
    if duration_slots <= 0:
        return None
    end = horizon_end
    candidates = sorted(
        (
            s for s in price_slots
            if s.start >= now and (end is None or s.start < end)
        ),
        key=lambda s: s.start,
    )
    if len(candidates) < duration_slots:
        return None

    best_i: int | None = None
    best_sum: float | None = None
    n = len(candidates)
    for i in range(n - duration_slots + 1):
        window = candidates[i:i + duration_slots]
        if any(window[k + 1].start - window[k].start != SLOT for k in range(len(window) - 1)):
            continue
        total = sum(s.eur_per_kwh for s in window)
        if best_sum is None or total < best_sum - 1e-9:
            best_sum, best_i = total, i

    if best_i is None or best_sum is None:
        return None

    window = candidates[best_i:best_i + duration_slots]
    start, finish = window[0].start, window[-1].start + SLOT
    avg = best_sum / duration_slots
    duration_label = _fmt_duration(duration_slots)
    reason = (
        f"Cheapest {duration_label} window from the day-ahead prices "
        f"(avg €{avg:.2f}/kWh)."
    )
    out: dict = {
        "start": start.isoformat(),
        "end": finish.isoformat(),
        "slots": duration_slots,
        "avg_eur_per_kwh": round(avg, 4),
        "reason": reason,
    }
    if assumed_kwh is not None and assumed_kwh > 0:
        # Spread assumed energy evenly across the window (same as EV advisor).
        kwh_per_slot = assumed_kwh / duration_slots
        out["est_cost_eur"] = round(best_sum * kwh_per_slot, 2)
        out["assumed_kwh"] = assumed_kwh
    return out


def assumed_kwh_for_kind(kind: str) -> float:
    return _ASSUMED_KWH.get(str(kind), 1.0)
