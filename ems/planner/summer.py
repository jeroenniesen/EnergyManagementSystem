"""Summer 'solar-first' strategy (SPEC §8.2).

Goal: run the house on the battery overnight using **your own solar**. Fill the battery from PV
surplus during the day; grid-charge only the **shortfall** needed to reach the night-carry target
(`target_soc_pct`), and only in the cheapest pre-sunset slots within a price cap. Everything else is
self-consumption — daytime soaks surplus, night discharges to serve the house.

Risk-aware sizing (SPEC §6.3): the solar we *count on* to fill the battery uses the **P10** forecast
(conservative), so a cloudy afternoon doesn't leave us short overnight. Pure + unit-tested — the
caller supplies prices, the solar forecast and the current SoC; no hardware, no I/O.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime

from ems.domain import BatteryIntent
from ems.planner.schedule import SLOT, Plan, PlanSlot
from ems.sources.forecast import ForecastSlot
from ems.sources.prices import PriceSlot

_DAYLIGHT_W = 20.0  # a slot counts as daytime above this P50 solar power


def sunset_after(forecast: list[ForecastSlot], now: datetime) -> datetime | None:
    """Start of the LAST slot of the next daylight period — the deadline to be charged for the
    night (the first contiguous run of daytime slots at/after `now`). None if no sun ahead."""
    run: list[datetime] = []
    for f in forecast:
        if f.start + SLOT <= now:
            continue
        if f.p50_w > _DAYLIGHT_W:
            if run and f.start - run[-1] > SLOT:
                break
            run.append(f.start)
        elif run:
            break
    return run[-1] if run else None


@dataclass(frozen=True)
class SummerConfig:
    usable_kwh: float
    target_soc_pct: float  # night-carry target (overnight load + reserve + floor)
    round_trip_efficiency: float = 0.90
    max_charge_w: float = 4000.0
    expected_load_w: float = 600.0  # average house load while the sun is up (solar serves it first)
    allow_grid_topup: bool = True  # may we buy the shortfall from the grid, or solar-only?
    max_topup_price_eur_per_kwh: float = 0.30  # never top up above this price
    horizon_slots: int = 96


def plan_summer(
    prices: list[PriceSlot],
    forecast: list[ForecastSlot],
    now: datetime,
    *,
    soc_pct: float,
    cfg: SummerConfig,
) -> Plan:
    future = [p for p in prices if p.start + SLOT > now][: cfg.horizon_slots]
    if not future:
        return Plan(created_at=now, slots=())

    p50_by = {f.start: f.p50_w for f in forecast}
    p10_by = {f.start: f.p10_w for f in forecast}
    eta = math.sqrt(max(1e-6, min(1.0, cfg.round_trip_efficiency)))
    dh = SLOT.total_seconds() / 3600.0

    # Only the NEXT daylight period — the first contiguous run of daytime slots (today's remaining
    # sun, or tomorrow's if it's already night). Using the whole horizon would (a) credit tomorrow's
    # solar against tonight's target and (b) let grid top-up land overnight, when the battery should
    # be discharging. Both are wrong; bound to one solar day.
    daytime: list[PriceSlot] = []
    for p in future:
        if p50_by.get(p.start, 0.0) > _DAYLIGHT_W:
            if daytime and p.start - daytime[-1].start > SLOT:
                break  # a gap — we've reached the end of this day's sun
            daytime.append(p)
        elif daytime:
            break  # the sun has set on this run
    sunrise = daytime[0].start if daytime else None
    sunset = daytime[-1].start if daytime else None
    day_now = sunrise is not None and sunrise <= now + SLOT  # the current slot is daylight

    usable = cfg.usable_kwh
    current_kwh = max(0.0, min(100.0, soc_pct)) / 100.0 * usable
    target_kwh = min(usable, max(0.0, cfg.target_soc_pct) / 100.0 * usable)

    if day_now:
        # Today's remaining sun fills the battery; aim to be full by today's sunset.
        solar_window, charge_deadline = daytime, sunset
    else:
        # Already night: no solar until the next sunrise, so we can't count on any tonight — carry
        # on what we grid-charge in the cheapest slots before the sun returns.
        solar_window = []
        charge_deadline = (sunrise - SLOT) if sunrise is not None else None

    # Conservative (P10) solar energy that can flow INTO the battery before sunset: the surplus
    # after the house takes its share, times the one-way charge efficiency.
    solar_charge_kwh = sum(
        max(0.0, p10_by.get(p.start, 0.0) - cfg.expected_load_w) * dh / 1000.0 * eta
        for p in solar_window
    )
    shortfall_kwh = max(0.0, target_kwh - current_kwh - solar_charge_kwh)

    # Buy only the shortfall, in the cheapest affordable slots before the charge deadline.
    per_slot_kwh = cfg.max_charge_w * dh / 1000.0 * eta
    grid_set: set[datetime] = set()
    if cfg.allow_grid_topup and shortfall_kwh > 1e-9:
        needed = math.ceil(shortfall_kwh / per_slot_kwh) if per_slot_kwh > 0 else 0
        affordable = [
            p for p in future
            if (charge_deadline is None or p.start <= charge_deadline)
            and p.eur_per_kwh <= cfg.max_topup_price_eur_per_kwh
        ]
        affordable.sort(key=lambda p: (p.eur_per_kwh, p.start))
        grid_set = {p.start for p in affordable[:needed]}

    target_soc_clamped = max(0.0, min(100.0, cfg.target_soc_pct))

    out: list[PlanSlot] = []
    for p in future:
        solar_w = p50_by.get(p.start, 0.0)
        if p.start in grid_set:
            intent = BatteryIntent.GRID_CHARGE_TO_TARGET
            reason = (f"grid top-up: cheap €{p.eur_per_kwh:.2f}/kWh to reach the night target "
                      f"the sun won't cover")
            # Charge to the night-carry target (NOT to full): the calculated shortfall, by sunset.
            out.append(PlanSlot(
                p.start, intent, reason, target_soc=target_soc_clamped,
                target_kwh=round(per_slot_kwh, 3), power_w=cfg.max_charge_w,
                deadline=charge_deadline,
            ))
            continue
        if solar_w > _DAYLIGHT_W:
            intent = BatteryIntent.ALLOW_SELF_CONSUMPTION
            reason = f"solar-first: charging from your panels ({solar_w:.0f} W) / self-consumption"
        else:
            intent = BatteryIntent.ALLOW_SELF_CONSUMPTION
            reason = "overnight: running the house on the battery"
        out.append(PlanSlot(p.start, intent, reason, target_soc=target_soc_clamped,
                            deadline=charge_deadline))
    return Plan(created_at=now, slots=tuple(out), strategy="summer",
                target_soc=target_soc_clamped, deadline=charge_deadline)
