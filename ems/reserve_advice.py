"""Advice-only night-reserve recommendation (BACKLOG B-67 / #74).

Computes a suggested overnight carry SoC from expected sunset→sunrise demand and explains why it
differs from the configured night reserve. Never writes settings, never calls ModeController, and
never touches the battery — adoption is a later issue (#130 / B-78).
"""
from __future__ import annotations

import math
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta

from ems.planner.charge_need import compute_charge_need
from ems.timeutil import require_aware

SLOT = timedelta(minutes=15)
SLOT_HOURS = 0.25
INSUFFICIENT_DATA_LABEL = "onvoldoende data"


def iter_quarter_hours(start: datetime, end: datetime) -> Iterator[datetime]:
    """Yield 15-minute slot starts covering `[start, end)` in real elapsed time.

    Iteration advances in UTC so spring-forward nights emit 92-quarter-hour-class spans and
    fall-back nights emit 100-quarter-hour-class spans — never a fixed 96-slot assumption.
    """
    require_aware(start, "start")
    require_aware(end, "end")
    if end <= start:
        return
    t = start.astimezone(UTC)
    # Floor to the enclosing UTC quarter-hour so partial leading minutes are not dropped/doubled.
    minute = (t.minute // 15) * 15
    t = t.replace(minute=minute, second=0, microsecond=0)
    if t < start.astimezone(UTC):
        t += SLOT
    end_utc = end.astimezone(UTC)
    while t < end_utc:
        yield t
        t += SLOT


def night_demand_kwh(
    load_w: Callable[[datetime], float],
    sunset: datetime,
    sunrise: datetime,
) -> float:
    """Expected AC house load (kWh) from sunset to next sunrise.

    DST-correct via `iter_quarter_hours`.
    """
    total_w_h = 0.0
    for slot in iter_quarter_hours(sunset, sunrise):
        w = load_w(slot)
        if not math.isfinite(w) or w < 0:
            continue
        total_w_h += w * SLOT_HOURS
    return total_w_h / 1000.0


def _clamp_soc(value: float, floor: float) -> float:
    return max(floor, min(100.0, value))


def _standard_target_soc(
    *,
    usable_kwh: float,
    min_reserve_soc: float,
    night_reserve_kwh: float,
    overnight_load_kwh: float,
    round_trip_efficiency: float,
) -> float:
    """SoC implied by configured overnight load + night reserve (standaardreserve)."""
    need = compute_charge_need(
        soc_pct=0.0,
        usable_kwh=usable_kwh,
        min_reserve_soc=min_reserve_soc,
        night_reserve_kwh=night_reserve_kwh,
        overnight_load_kwh=overnight_load_kwh,
        round_trip_efficiency=round_trip_efficiency,
    )
    return _clamp_soc(need.target_soc_pct, min_reserve_soc)


def recommend_night_reserve(
    *,
    night_demand_kwh: float | None,
    usable_kwh: float,
    min_reserve_soc: float,
    night_reserve_kwh: float,
    overnight_load_kwh: float,
    round_trip_efficiency: float = 0.9,
) -> dict:
    """Recommend an overnight carry SoC. Advice only — callers must not persist the result.

    When `night_demand_kwh` is None (no usable forecast / sun window), falls back to the SoC implied
    by the configured overnight load + night reserve and labels the result `onvoldoende data`.
    The recommendation is always clamped to `[min_reserve_soc, 100]`.
    """
    floor = max(0.0, min(100.0, float(min_reserve_soc)))
    capacity = float(usable_kwh)
    current_kwh = float(night_reserve_kwh)
    configured_load = float(overnight_load_kwh)
    standard = _standard_target_soc(
        usable_kwh=capacity if capacity > 0 else 1.0,
        min_reserve_soc=floor,
        night_reserve_kwh=current_kwh,
        overnight_load_kwh=configured_load,
        round_trip_efficiency=round_trip_efficiency,
    )

    if night_demand_kwh is None or not math.isfinite(night_demand_kwh) or capacity <= 0:
        return {
            "recommended_soc_pct": round(standard, 1),
            "min_reserve_soc": round(floor, 1),
            "current_night_reserve_kwh": round(current_kwh, 2),
            "current_target_soc_pct": round(standard, 1),
            "night_demand_kwh": None,
            "label": INSUFFICIENT_DATA_LABEL,
            "reason": (
                "Not enough forecast data for tonight — showing your usual reserve "
                f"({standard:.0f}% from the configured overnight load and night buffer)."
            ),
            "automatic": False,
        }

    eta = math.sqrt(max(1e-6, min(1.0, float(round_trip_efficiency))))
    # Deliver night demand from the pack, then keep the hard floor — same composition as charge_need
    # with the measured overnight load and the user's comfort buffer still applied on top.
    demand = max(0.0, float(night_demand_kwh))
    target_kwh = min(capacity, (demand + current_kwh) / eta + capacity * floor / 100.0)
    recommended = _clamp_soc(target_kwh / capacity * 100.0, floor)

    delta = recommended - standard
    if abs(delta) < 0.5:
        why = (
            f"Tonight's expected overnight use is about {demand:.1f} kWh — close to your "
            f"configured plan, so the advice matches your usual {standard:.0f}% carry."
        )
    elif delta > 0:
        why = (
            f"Tonight's expected overnight use is about {demand:.1f} kWh, above your "
            f"configured overnight load, so a {recommended:.0f}% carry is safer than "
            f"your usual {standard:.0f}%."
        )
    else:
        why = (
            f"Tonight's expected overnight use is about {demand:.1f} kWh, below your "
            f"configured overnight load, so {recommended:.0f}% is enough versus your "
            f"usual {standard:.0f}%."
        )

    return {
        "recommended_soc_pct": round(recommended, 1),
        "min_reserve_soc": round(floor, 1),
        "current_night_reserve_kwh": round(current_kwh, 2),
        "current_target_soc_pct": round(standard, 1),
        "night_demand_kwh": round(demand, 2),
        "label": None,
        "reason": why,
        "automatic": False,
    }
