"""Summer 'solar-first' strategy (SPEC §8.2).

Goal: run the house on the battery overnight using **your own solar**. Fill the battery from PV
surplus during the day; grid-charge only the **shortfall** needed to reach the night-carry target
(`target_soc_pct`), and only in the cost-minimal contiguous pre-deadline slots within a price
cap (tie → later; cheapest-n if no contiguous window fits). Everything else is
self-consumption — daytime soaks surplus, night discharges to serve the house.

Risk-aware sizing (SPEC §6.3): the solar we *count on* to fill the battery is a configurable
fraction of the expected (P50) forecast — `solar_confidence` (0..1). Lower = more cautious (counts
on less sun, so a cloudy afternoon doesn't leave us short overnight, at the cost of buying more grid
power); higher = trusts the forecast and buys less. Pure + unit-tested — the caller supplies prices,
the solar forecast and the current SoC; no hardware, no I/O.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo

from ems.domain import BatteryIntent
from ems.planner import economics
from ems.planner.budgets import ac_grid_kwh_per_slot, clamp_grid_charge
from ems.planner.rule_based import late_pack_charge_starts
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


def _topup_price_limit(
    cfg: SummerConfig,
    future: list[PriceSlot],
    charge_deadline: datetime | None,
) -> float:
    """Configured top-up cap, tightened by the shared buy ceiling when a later night exists.

    `peak` is the dearest slot after the charge deadline — the import the top-up is covering.
    No deadline, or nothing after it, means there is no separate night price to undercut, so
    the configured cap stays the ceiling (a flat dark horizon is not an arbitrage peak).
    """
    cap = cfg.max_topup_price_eur_per_kwh
    if charge_deadline is None:
        return cap
    overnight = [p.eur_per_kwh for p in future if p.start > charge_deadline]
    if not overnight:
        return cap
    return min(cap, economics.max_buy_for_planner_config(max(overnight), cfg))


@dataclass(frozen=True)
class SummerConfig:
    usable_kwh: float
    target_soc_pct: float  # night-carry target (overnight load + reserve + floor)
    round_trip_efficiency: float = 0.90
    max_charge_w: float = 4000.0
    expected_load_w: float = 600.0  # average house load while the sun is up (solar serves it first)
    # Fraction of the EXPECTED (P50) forecast we count on filling the battery (0..1). 1.0 = trust
    # the forecast; lower = more cautious. Replaces the old hard-coded P10 (=0.6×P50) haircut.
    solar_confidence: float = 0.8
    allow_grid_topup: bool = True  # may we buy the shortfall from the grid, or solar-only?
    max_topup_price_eur_per_kwh: float = 0.30  # never top up above this price
    # Same wear/risk the other planners already use. Defaults match settings.py so a caller
    # that does not thread them (service.summer_cfg is out of scope for #327) still prices
    # the shared buy ceiling at the product default.
    degradation_eur_per_kwh: float = 0.05
    risk_margin_eur_per_kwh: float = 0.02
    horizon_slots: int = 96
    negative_price_soak: bool = False  # opt-in: sub-zero slots become top-up candidates
    max_daily_grid_charge_kwh: float = 12.0  # #317 hard daily grid-buy cap
    already_bought_today_kwh: float | None = 0.0
    site_tz: ZoneInfo = field(default_factory=lambda: ZoneInfo("Europe/Amsterdam"))


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
    confidence = max(0.0, min(1.0, cfg.solar_confidence))
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

    # Solar energy we count on flowing INTO the battery before sunset: the surplus (the
    # confidence-scaled expected forecast, after the house takes its share) times the one-way
    # charge efficiency. `solar_confidence` is the single conservatism knob (was a hard P10).
    solar_charge_kwh = sum(
        max(0.0, confidence * p50_by.get(p.start, 0.0) - cfg.expected_load_w) * dh / 1000.0 * eta
        for p in solar_window
    )
    shortfall_kwh = max(0.0, target_kwh - current_kwh - solar_charge_kwh)

    # Buy only the shortfall before the charge deadline. Eligibility (price cap, or a sub-zero soak
    # slot when `negative_price_soak` is on — even with `allow_grid_topup` off) is unchanged.
    # Within that pool, late-pack picks the cost-minimal contiguous window (tie → later). No full
    # window → cheapest n. Same shortfall-bounded slot count — no new sizing math.
    soak = cfg.negative_price_soak
    per_slot_kwh = cfg.max_charge_w * dh / 1000.0 * eta
    grid_set: set[datetime] = set()
    topup_limit = _topup_price_limit(cfg, future, charge_deadline)

    def _eligible(p: PriceSlot) -> bool:
        if charge_deadline is not None and p.start > charge_deadline:
            return False
        if cfg.allow_grid_topup and p.eur_per_kwh <= topup_limit:
            return True
        return soak and p.eur_per_kwh < 0.0

    if (cfg.allow_grid_topup or soak) and shortfall_kwh > 1e-9:
        needed = math.ceil(shortfall_kwh / per_slot_kwh) if per_slot_kwh > 0 else 0
        affordable = [p for p in future if _eligible(p)]
        # The opt-in soak policy gives sub-zero slots priority, even when an isolated negative
        # slot cannot belong to a full contiguous window. Keep the same shortfall-sized budget.
        negative = (sorted((p for p in affordable if p.eur_per_kwh < 0.0),
                           key=lambda p: (p.eur_per_kwh, p.start))[:needed]
                    if soak else [])
        grid_set = {p.start for p in negative}
        grid_set = late_pack_charge_starts(
            [p for p in affordable if p.start not in grid_set], needed - len(grid_set),
            price_of=lambda p: p.eur_per_kwh,
            max_buy=max((p.eur_per_kwh for p in affordable), default=0.0),
        ) | grid_set

    target_soc_clamped = max(0.0, min(100.0, cfg.target_soc_pct))

    out: list[PlanSlot] = []
    for p in future:
        solar_w = p50_by.get(p.start, 0.0)
        if p.start in grid_set:
            intent = BatteryIntent.GRID_CHARGE_TO_TARGET
            if soak and p.eur_per_kwh < 0.0:
                reason = (f"grid top-up: price €{p.eur_per_kwh:.2f}/kWh below €0 — "
                          f"you are paid to charge")
            else:
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
    plan = Plan(created_at=now, slots=tuple(out), strategy="summer",
                target_soc=target_soc_clamped, deadline=charge_deadline)
    return clamp_grid_charge(
        plan, cfg.already_bought_today_kwh, cfg.max_daily_grid_charge_kwh,
        ac_grid_kwh_per_slot(cfg.max_charge_w),
        tz=cfg.site_tz,
    )
