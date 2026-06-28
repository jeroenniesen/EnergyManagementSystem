"""Backtest harness for the charging algorithm (research/validation, SPEC §8 / §14 acceptance).

A `Scenario` bundles a realised day in the Netherlands — the solar that actually arrives, the house
load, the day-ahead prices, and (separately) the P10/P50/P90 *forecast* the planner gets to see. The
planner plans on the forecast; `simulate` then runs the resulting plan through the energy model
(`project_energy`) against the REALISED solar/load and scores it: grid cost, self-sufficiency, did
it run the battery below reserve, how many cycles. This lets us compare charging algorithms on the
same days instead of guessing. Pure — no I/O, no hardware.
"""
from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from ems.domain import BatteryIntent
from ems.planner.projection import BatteryModel, project_energy
from ems.planner.schedule import SLOT, Plan, PlanSlot
from ems.sources.forecast import ForecastSlot
from ems.sources.prices import PriceSlot

_DH = 0.25  # hours per 15-min slot
_DEFAULT_MODEL = BatteryModel(
    usable_kwh=10.8, max_charge_w=4000.0, max_discharge_w=4000.0,
    round_trip_efficiency=0.90, reserve_soc_pct=10.0,
)


@dataclass(frozen=True)
class Scenario:
    name: str
    now: datetime
    prices: list[PriceSlot]
    forecast: list[ForecastSlot]  # what the planner sees (P10/P50/P90)
    actual_solar_w: dict[datetime, float]  # what really arrives
    load_w: dict[datetime, float]
    start_soc_pct: float


@dataclass(frozen=True)
class SimResult:
    name: str
    grid_cost_eur: float
    import_kwh: float
    export_kwh: float
    solar_kwh: float
    self_sufficiency_pct: float
    soc_min_pct: float
    soc_end_pct: float
    cycles: float
    night_ok: bool  # never discharged below the reserve floor


# --- realistic NL curves (3 kWp array, typical household) ---

def _bell(h: float, peak_w: float, width: float = 3.4, centre: float = 13.3) -> float:
    if h <= 5.0 or h >= 21.5:
        return 0.0
    return max(0.0, math.exp(-((h - centre) / width) ** 2) * peak_w)


def _price_eur(h: float) -> float:
    """A typical NL dynamic (Tibber) all-in €/kWh shape: cheap night dip, midday softening from
    solar glut, a hard early-evening peak."""
    base = 0.23
    if 1.0 <= h < 5.0:
        base -= 0.05  # overnight dip
    if 11.0 <= h < 15.0:
        base -= 0.03  # midday solar glut
    if 17.0 <= h < 21.0:
        base += 0.13  # evening peak
    elif 7.0 <= h < 9.0:
        base += 0.04  # morning bump
    return round(base, 3)


def _load_w(h: float) -> float:
    """Non-EV household load (W): overnight base, daytime, morning + evening peaks. ~10 kWh/day."""
    if 17.0 <= h < 22.0:
        return 1400.0
    if 7.0 <= h < 9.0:
        return 750.0
    if 9.0 <= h < 17.0:
        return 480.0
    return 260.0  # overnight


_PEAK_W = {"bad": 360.0, "average": 1250.0, "good": 2150.0, "extreme": 2700.0}


def nl_scenarios(
    tz: ZoneInfo,
    *,
    start_hour: int = 6,
    start_soc_pct: float = 45.0,
    forecast_bias: float = 1.0,
) -> list[Scenario]:
    """Four NL weather days (bad / average / good / extreme), 24h from dawn. The forecast P50 = the
    realised solar × `forecast_bias` (1.0 = perfect; <1 = sun over-delivers vs the forecast)."""
    base = datetime(2026, 6, 28, start_hour, 0, tzinfo=tz)
    starts = [base + i * SLOT for i in range(96)]
    out: list[Scenario] = []
    for name, peak in _PEAK_W.items():
        actual = {s: _bell(s.astimezone(tz).hour + s.minute / 60.0, peak) for s in starts}
        fc = []
        for s in starts:
            p50 = actual[s] * forecast_bias
            fc.append(ForecastSlot(start=s, p10_w=0.65 * p50, p50_w=p50, p90_w=1.2 * p50))
        prices = [PriceSlot(s, _price_eur(s.astimezone(tz).hour + s.minute / 60.0)) for s in starts]
        load = {s: _load_w(s.astimezone(tz).hour + s.minute / 60.0) for s in starts}
        out.append(Scenario(name, base, prices, fc, actual, load, start_soc_pct))
    return out


def simulate(
    scenario: Scenario,
    plan_fn: Callable[[Scenario], Plan],
    *,
    model: BatteryModel = _DEFAULT_MODEL,
    charge_target_soc_pct: float | None = None,
) -> SimResult:
    """Run `plan_fn` on the scenario (it sees prices + forecast + start SoC), then score the plan
    against the REALISED solar/load via the energy model."""
    plan = plan_fn(scenario)
    projected = project_energy(
        plan.slots, start_soc_pct=scenario.start_soc_pct,
        solar_w_by=scenario.actual_solar_w, load_w_by=scenario.load_w,
        model=model, charge_target_soc_pct=charge_target_soc_pct,
    )
    price_by = {p.start: p.eur_per_kwh for p in scenario.prices}

    def kwh(w: float) -> float:
        return w * _DH / 1000.0

    imp = sum(kwh(max(0.0, p.grid_w)) for p in projected)
    exp = sum(kwh(max(0.0, -p.grid_w)) for p in projected)
    solar = sum(kwh(p.solar_w) for p in projected)
    load = sum(kwh(p.load_w) for p in projected)
    charge = sum(kwh(max(0.0, -p.battery_w)) for p in projected)
    discharge = sum(kwh(max(0.0, p.battery_w)) for p in projected)
    cost = sum((kwh(max(0.0, p.grid_w)) - kwh(max(0.0, -p.grid_w))) * price_by.get(p.start, 0.0)
               for p in projected)
    socs = [p.soc_pct for p in projected]
    soc_min = min(socs) if socs else scenario.start_soc_pct
    return _score(scenario.name, imp, exp, solar, load, charge, discharge, cost, socs, soc_min,
                  model)


def _score(name, imp, exp, solar, load, charge, discharge, cost, socs, soc_min, model) -> SimResult:
    return SimResult(
        name=name,
        grid_cost_eur=round(cost, 3),
        import_kwh=round(imp, 2), export_kwh=round(exp, 2), solar_kwh=round(solar, 2),
        self_sufficiency_pct=round(max(0.0, (load - imp) / load * 100.0), 1) if load > 0 else 0.0,
        soc_min_pct=round(soc_min, 1),
        soc_end_pct=round(socs[-1], 1) if socs else 0.0,
        cycles=round((charge + discharge) / (2 * model.usable_kwh), 2) if model.usable_kwh else 0.0,
        night_ok=soc_min >= model.reserve_soc_pct - 0.5,
    )


def simulate_rolling(
    scenario: Scenario,
    replan: Callable[[datetime, float], Plan],
    *,
    model: BatteryModel = _DEFAULT_MODEL,
) -> SimResult:
    """The realistic test: re-plan every slot with the CURRENT SoC (as the live loop does), apply
    only that slot's action, step forward on the realised solar/load. This captures the system's
    adaptivity — it commits little early and tops up late as the day's true solar lands."""
    price_by = {p.start: p.eur_per_kwh for p in scenario.prices}
    starts = sorted(scenario.actual_solar_w)

    def kwh(w: float) -> float:
        return w * _DH / 1000.0

    soc = scenario.start_soc_pct
    imp = exp = solar = load = charge = discharge = cost = 0.0
    socs: list[float] = []
    for t in starts:
        plan = replan(t, soc)
        slot = plan.intent_at(t)
        intent = slot.intent if slot else BatteryIntent.ALLOW_SELF_CONSUMPTION
        step = project_energy(
            [PlanSlot(t, intent, "")], start_soc_pct=soc,
            solar_w_by={t: scenario.actual_solar_w[t]}, load_w_by={t: scenario.load_w[t]},
            model=model,
        )[0]
        soc = step.soc_pct
        socs.append(soc)
        imp += kwh(max(0.0, step.grid_w))
        exp += kwh(max(0.0, -step.grid_w))
        solar += kwh(step.solar_w)
        load += kwh(step.load_w)
        charge += kwh(max(0.0, -step.battery_w))
        discharge += kwh(max(0.0, step.battery_w))
        cost += (kwh(max(0.0, step.grid_w)) - kwh(max(0.0, -step.grid_w))) * price_by.get(t, 0.0)
    soc_min = min(socs) if socs else scenario.start_soc_pct
    return _score(scenario.name, imp, exp, solar, load, charge, discharge, cost, socs, soc_min,
                  model)
