"""Evidence-gated household recommendations. No settings or device writes."""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Callable
from datetime import datetime, timedelta
from statistics import mean, median
from zoneinfo import ZoneInfo

from ems.calibration import valid_rows
from ems.planner.load_profile import LoadProfile
from ems.sources.forecast import ForecastSlot
from ems.sources.prices import PriceSlot

SLOT = timedelta(minutes=15)


def appliance_window(
    prices: list[PriceSlot],
    forecast: list[ForecastSlot],
    load_by: dict[datetime, float],
    *,
    now: datetime,
    deadline: datetime,
    duration_minutes: int,
    energy_kwh: float,
    export_price: Callable[[float], float] | None = None,
    import_price: Callable[[float], float] | None = None,
    export_by: dict[datetime, float] | None = None,
) -> dict:
    """Constant-power contiguous run, priced at import cost or forgone export revenue."""
    if (
        not math.isfinite(energy_kwh)
        or not 0 < energy_kwh <= 50
        or duration_minutes % 15
        or not 15 <= duration_minutes <= 1440
    ):
        raise ValueError("Use 15–1440 minutes in quarter hours and 0–50 kWh.")
    if (
        now.tzinfo is None
        or deadline.tzinfo is None
        or not now < deadline <= now + timedelta(days=2)
    ):
        raise ValueError("Deadline must be within the next 48 hours and include a timezone.")
    count = duration_minutes // 15
    by = {
        p.start: p.eur_per_kwh
        for p in prices
        if p.start >= now and p.start + SLOT <= deadline and math.isfinite(p.eur_per_kwh)
    }
    solar = {f.start: max(0.0, f.p10_w) for f in forecast}
    per_slot = energy_kwh / count
    candidates = []
    for start in sorted(by):
        times = [start + i * SLOT for i in range(count)]
        if not all(t in by for t in times):
            continue
        cost = 0.0
        for t in times:
            surplus = max(0.0, solar.get(t, 0.0) - load_by.get(t, 0.0)) / 4000
            own = min(per_slot, surplus)
            imp = import_price(by[t]) if import_price else by[t]
            exp = export_price(by[t]) if export_price else imp
            if export_by is not None:
                exp = export_by[t]
            cost += own * exp + (per_slot - own) * imp
        candidates.append((cost, start))
    if not candidates:
        return {"available": False, "reason": "No complete priced window before the deadline."}
    cost, start = min(candidates)
    earliest = min(candidates, key=lambda item: item[1])
    return {
        "available": True,
        "start": start.isoformat(),
        "end": (start + duration_minutes * timedelta(minutes=1)).isoformat(),
        "cost_eur": round(cost, 4),
        "saving_vs_now_eur": round(earliest[0] - cost, 4),
        "baseline_start": earliest[1].isoformat(),
        "automatic": False,
        "reason": "Estimated constant-power run; solar uses the conservative forecast. "
        "Savings compare with the earliest complete window; battery response is excluded.",
    }


def reserve_advice(
    prices: list[PriceSlot],
    forecast: list[ForecastSlot],
    profile: LoadProfile,
    *,
    now: datetime,
    settings: dict,
    fresh: bool,
) -> dict:
    unknown = {
        "available": False,
        "automatic": False,
        "reason": "Fresh prices, solar and household history are needed for a target.",
    }
    start = now.replace(minute=now.minute // 15 * 15, second=0, microsecond=0)
    if start < now:
        start += SLOT
    future = sorted(
        (p for p in prices if start <= p.start < start + timedelta(hours=24)), key=lambda p: p.start
    )
    if not fresh or len(future) != 96 or future[0].start != start:
        return unknown
    if any(b.start - a.start != SLOT for a, b in zip(future, future[1:], strict=False)):
        return {**unknown, "reason": "Price coverage has gaps; keep the current reserve."}
    capacity = float(settings.get("battery.usable_kwh", 10.8))
    floor = float(settings.get("battery.min_reserve_soc", 10))
    eta = math.sqrt(float(settings.get("planner.round_trip_efficiency", 0.9)))
    if not capacity > 0 or not 0 < eta <= 1 or not 0 <= floor <= 100:
        return unknown
    wear = float(settings.get("planner.degradation_eur_per_kwh", 0.05))
    risk = float(settings.get("planner.risk_margin_eur_per_kwh", 0.02))
    cheapest = min(p.eur_per_kwh for p in future)
    threshold = cheapest / eta**2 + wear + risk
    solar = {f.start: max(0.0, f.p10_w) for f in forecast}
    peak = [p for p in future if p.eur_per_kwh > threshold]
    expected = sum(max(0, profile.expected_w(p.start) - solar.get(p.start, 0)) / 4000 for p in peak)
    conservative = sum(
        max(0, profile.band_w(p.start)[1] - solar.get(p.start, 0)) / 4000 for p in peak
    )
    room = capacity * (100 - floor) / 100
    required = min(room, conservative / eta)
    expected_dc = min(room, expected / eta)
    target = floor + required / capacity * 100
    return {
        "available": True,
        "automatic": False,
        "hard_floor_soc_pct": floor,
        "target_soc_pct": round(target, 1),
        "required_stored_kwh": round(required, 3),
        "extra_buffer_kwh": round(required - expected_dc, 3),
        "extra_buffer_cost_eur": round((required - expected_dc) / eta * cheapest, 3),
        "capacity_limited": conservative / eta > room,
        "deadline": peak[0].start.isoformat() if peak else None,
        "reason": "Operating target for forecast expensive demand, above your unchanged hard "
        "floor. Buffer uses observed load spread, not a guaranteed probability. "
        "Cost assumes charging at the cheapest known rate; this is not a charge order.",
    }


def consumption_advice(
    rows: list[dict],
    *,
    now: datetime,
    tz: ZoneInfo,
    price_eur_per_kwh: float,
) -> list[dict]:
    hours = defaultdict(list)
    today = now.astimezone(tz).date()
    for ts, values in valid_rows(rows, ("non_ev_load_w",)):
        local = ts.astimezone(tz)
        age = (today - local.date()).days
        if 1 <= age <= 21 and 0 <= values["non_ev_load_w"] <= 50000:
            hours[(local.date(), local.hour)].append(values["non_ev_load_w"])
    daily = defaultdict(dict)
    for (day, hour), values in hours.items():
        daily[day][hour] = mean(values)
    out = []
    for kind, required_hours in (
        ("night_baseload", set(range(1, 5))),
        ("household_use", set(range(24))),
    ):
        before, recent = [], []
        for day, hour_values in daily.items():
            # Require observed hours throughout the window, not a short burst.
            if not required_hours.issubset(hour_values):
                continue
            value = mean(hour_values[h] for h in required_hours)
            (recent if (today - day).days <= 7 else before).append(value)
        if len(recent) < 5 or len(before) < 10:
            continue
        delta = median(recent) - median(before)
        if delta < max(50, median(before) * 0.2):
            continue
        kwh = delta * len(required_hours) / 1000
        out.append(
            {
                "kind": kind,
                "extra_kwh_per_day": round(kwh, 3),
                "estimated_extra_eur_per_day": round(kwh * price_eur_per_kwh, 3),
                "recent_days": len(recent),
                "baseline_days": len(before),
                "reason": (
                    "Nighttime demand has stayed above the previous two weeks."
                    if kind == "night_baseload"
                    else "Household demand has stayed above the previous two weeks."
                ),
                "action": "Check always-on equipment and recent routine changes. "
                "These overlapping estimates must not be added together.",
            }
        )
    return out
