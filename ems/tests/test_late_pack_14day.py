"""AC4 (#328): 14-day average grid-charge €/kWh, earliest-first versus late-pack.

The price fixture (``fixtures/late_pack_14d_prices.json``) is synthetic and was fixed
before this test read a planner result. Dull sky and a flat 400 W house load keep the
charged kWh identical across price shapes, so the window choice is the only difference.

Before: pre-#328 cheapest-first marks (``sort(key=(price, start))[:n]``). The controller
then starts at the earliest mark and charges at ``max_charge_w`` until that day's kWh
are in — including slots that were never marked.

After: ``GRID_CHARGE`` slots on the real adaptive plan and the real summer plan
(``allow_grid_topup``). Net import kWh of a full slot is ``power_w × 0.25 h``.

Average €/kWh = Σ(net import kWh × slot price) / Σ(net import kWh).
"""
import json
import math
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from ems.domain import BatteryIntent
from ems.planner import economics
from ems.planner.adaptive import AdaptiveConfig, plan_adaptive
from ems.planner.schedule import SLOT
from ems.planner.summer import SummerConfig, plan_summer
from ems.sources.forecast import ForecastSlot
from ems.sources.prices import PriceSlot

_FIXTURE = Path(__file__).parent / "fixtures" / "late_pack_14d_prices.json"
_TZ = ZoneInfo("Europe/Amsterdam")
_DH = 0.25
_TOL = 1e-9
_LOAD_W = 400.0
_SOC_PCT = 20.0
_MAX_CHARGE_W = 4000.0

_ADAPTIVE = AdaptiveConfig(
    usable_kwh=10.0,
    reserve_soc_pct=10.0,
    round_trip_efficiency=0.90,
    max_charge_w=_MAX_CHARGE_W,
    solar_confidence=0.8,
    horizon_slots=96,
    negative_price_soak=False,
)
_SUMMER = SummerConfig(
    usable_kwh=10.0,
    target_soc_pct=80.0,
    round_trip_efficiency=0.90,
    max_charge_w=_MAX_CHARGE_W,
    expected_load_w=0.0,
    solar_confidence=0.8,
    allow_grid_topup=True,
    max_topup_price_eur_per_kwh=0.30,
    horizon_slots=96,
    negative_price_soak=False,
)


def _load_fixture() -> dict:
    return json.loads(_FIXTURE.read_text(encoding="utf-8"))


def _day_slots(day: dict) -> tuple[datetime, list[PriceSlot], list[ForecastSlot], dict]:
    year, month, dom = (int(part) for part in day["date"].split("-"))
    now = datetime(year, month, dom, 0, 0, tzinfo=_TZ)
    prices = [
        PriceSlot(now + i * SLOT, float(eur))
        for i, eur in enumerate(day["eur_per_kwh"])
    ]
    forecast = [ForecastSlot(p.start, 0.0, 0.0, 0.0) for p in prices]
    load = {p.start: _LOAD_W for p in prices}
    return now, prices, forecast, load


def _grid_kwh(power_w: float) -> float:
    """Grid import of one full slot at ``power_w`` (the kWh the slot price is paid on)."""
    return power_w * _DH / 1000.0


def _average(pairs: list[tuple[float, float]]) -> tuple[float, float]:
    """Return (weighted €/kWh, total kWh) for ``(price, kwh)`` pairs."""
    total = sum(kwh for _, kwh in pairs)
    if total <= 0.0:
        return 0.0, 0.0
    cost = sum(price * kwh for price, kwh in pairs)
    return cost / total, total


def _legacy_adaptive_marks(
    prices: list[PriceSlot],
    forecast: list[ForecastSlot],
    now: datetime,
    *,
    soc_pct: float,
    load_w_by: dict[datetime, float],
    cfg: AdaptiveConfig,
) -> set[datetime]:
    """Pre-#328 adaptive marks: cheapest-first on the same pool and the same ``n``.

    Slot *placement* is the only difference from ``plan_adaptive``. Sizing matches
    ``ems/planner/adaptive.py`` so both sides charge the same kWh.
    """
    future = [p for p in prices if p.start + SLOT > now][: cfg.horizon_slots]
    p50 = {f.start: f.p50_w for f in forecast}
    confidence = max(0.0, min(1.0, cfg.solar_confidence))
    eta = math.sqrt(max(1e-6, min(1.0, cfg.round_trip_efficiency)))
    usable = cfg.usable_kwh
    reserve_kwh = cfg.reserve_soc_pct / 100.0 * usable
    avail_now_kwh = max(0.0, soc_pct / 100.0 * usable - reserve_kwh)
    ranked = sorted(p.eur_per_kwh for p in future)
    charge_price = ranked[len(ranked) // 4]
    breakeven = economics.breakeven(
        charge_price,
        round_trip_efficiency=cfg.round_trip_efficiency,
        degradation_eur_per_kwh=cfg.degradation_eur_per_kwh,
        risk_margin_eur_per_kwh=cfg.risk_margin_eur_per_kwh,
    )

    def net_w(p: PriceSlot) -> float:
        return load_w_by.get(p.start, 0.0) - p50.get(p.start, 0.0)

    total_deficit_kwh = sum(max(0.0, net_w(p)) * _DH / 1000.0 for p in future)
    coverable_kwh = min(total_deficit_kwh / eta, usable - reserve_kwh)
    solar_to_batt_kwh = sum(
        max(0.0, confidence * p50.get(p.start, 0.0) - load_w_by.get(p.start, 0.0))
        * _DH / 1000.0 * eta
        for p in future
    )
    shortfall_kwh = max(0.0, coverable_kwh - avail_now_kwh - solar_to_batt_kwh)
    expensive = [p.start for p in future if net_w(p) > 0 and p.eur_per_kwh > breakeven]
    last_need = max(expensive) if expensive else None
    per_slot_kwh = cfg.max_charge_w * _DH / 1000.0 * eta
    n_charge = (
        math.ceil(shortfall_kwh / per_slot_kwh)
        if per_slot_kwh > 0 and shortfall_kwh > 1e-9 else 0
    )
    if expensive:
        peak_min = min(p.eur_per_kwh for p in future if p.start in set(expensive))
        pool = [p for p in future if p.start < last_need and p.eur_per_kwh < peak_min]
    else:
        pool = [p for p in future if p.eur_per_kwh <= breakeven]
    pool.sort(key=lambda p: (p.eur_per_kwh, p.start))
    return {p.start for p in pool[:n_charge]}


def _legacy_summer_marks(
    prices: list[PriceSlot],
    forecast: list[ForecastSlot],
    now: datetime,
    *,
    soc_pct: float,
    cfg: SummerConfig,
) -> set[datetime]:
    """Pre-#328 summer top-up marks: cheapest-first under the same cap and shortfall."""
    future = [p for p in prices if p.start + SLOT > now][: cfg.horizon_slots]
    p50_by = {f.start: f.p50_w for f in forecast}
    confidence = max(0.0, min(1.0, cfg.solar_confidence))
    eta = math.sqrt(max(1e-6, min(1.0, cfg.round_trip_efficiency)))
    dh = SLOT.total_seconds() / 3600.0
    daytime: list[PriceSlot] = []
    for p in future:
        if p50_by.get(p.start, 0.0) > 20.0:
            if daytime and p.start - daytime[-1].start > SLOT:
                break
            daytime.append(p)
        elif daytime:
            break
    sunrise = daytime[0].start if daytime else None
    day_now = sunrise is not None and sunrise <= now + SLOT
    usable = cfg.usable_kwh
    current_kwh = max(0.0, min(100.0, soc_pct)) / 100.0 * usable
    target_kwh = min(usable, max(0.0, cfg.target_soc_pct) / 100.0 * usable)
    if day_now:
        solar_window = daytime
        charge_deadline = daytime[-1].start
    else:
        solar_window = []
        charge_deadline = (sunrise - SLOT) if sunrise is not None else None
    solar_charge_kwh = sum(
        max(0.0, confidence * p50_by.get(p.start, 0.0) - cfg.expected_load_w) * dh / 1000.0 * eta
        for p in solar_window
    )
    shortfall_kwh = max(0.0, target_kwh - current_kwh - solar_charge_kwh)
    per_slot_kwh = cfg.max_charge_w * dh / 1000.0 * eta

    def eligible(p: PriceSlot) -> bool:
        if charge_deadline is not None and p.start > charge_deadline:
            return False
        if cfg.allow_grid_topup and p.eur_per_kwh <= cfg.max_topup_price_eur_per_kwh:
            return True
        return cfg.negative_price_soak and p.eur_per_kwh < 0.0

    if not (cfg.allow_grid_topup or cfg.negative_price_soak) or shortfall_kwh <= 1e-9:
        return set()
    needed = math.ceil(shortfall_kwh / per_slot_kwh) if per_slot_kwh > 0 else 0
    affordable = [p for p in future if eligible(p)]
    affordable.sort(key=lambda p: (p.eur_per_kwh, p.start))
    return {p.start for p in affordable[:needed]}


def _pump_from_first_mark(
    prices: list[PriceSlot],
    marked: set[datetime],
    kwh: float,
    power_w: float,
) -> list[tuple[float, float]]:
    """Charge at ``power_w`` from the earliest mark until ``kwh`` are imported."""
    if not marked or kwh <= 0.0:
        return []
    slot_kwh = _grid_kwh(power_w)
    first = min(marked)
    index = next(i for i, slot in enumerate(prices) if slot.start == first)
    pairs: list[tuple[float, float]] = []
    remaining = kwh
    cursor = index
    while remaining > 1e-12 and cursor < len(prices):
        take = min(slot_kwh, remaining)
        pairs.append((prices[cursor].eur_per_kwh, take))
        remaining -= take
        cursor += 1
    return pairs


def _plan_import_pairs(plan, price_of: dict[datetime, float]) -> list[tuple[float, float]]:
    pairs: list[tuple[float, float]] = []
    for slot in plan.slots:
        if slot.intent is not BatteryIntent.GRID_CHARGE_TO_TARGET:
            continue
        power = _MAX_CHARGE_W if slot.power_w is None else slot.power_w
        pairs.append((price_of[slot.start], _grid_kwh(power)))
    return pairs


def _evaluate_day(day: dict, planner: str) -> dict:
    now, prices, forecast, load = _day_slots(day)
    price_of = {p.start: p.eur_per_kwh for p in prices}
    if planner == "adaptive":
        plan = plan_adaptive(
            prices, forecast, now, soc_pct=_SOC_PCT, load_w_by=load, cfg=_ADAPTIVE,
        )
        marked = _legacy_adaptive_marks(
            prices, forecast, now, soc_pct=_SOC_PCT, load_w_by=load, cfg=_ADAPTIVE,
        )
    elif planner == "summer":
        plan = plan_summer(prices, forecast, now, soc_pct=_SOC_PCT, cfg=_SUMMER)
        marked = _legacy_summer_marks(prices, forecast, now, soc_pct=_SOC_PCT, cfg=_SUMMER)
    else:
        raise AssertionError(planner)
    after_pairs = _plan_import_pairs(plan, price_of)
    after_avg, after_kwh = _average(after_pairs)
    # Derive the old plan's energy from its own marks. Passing ``after_kwh`` here would make
    # the per-day equality assertion true by construction, even if a planner changed the budget.
    legacy_kwh = len(marked) * _grid_kwh(_MAX_CHARGE_W)
    before_pairs = _pump_from_first_mark(prices, marked, legacy_kwh, _MAX_CHARGE_W)
    before_avg, before_kwh = _average(before_pairs)
    planned = {
        slot.start for slot in plan.slots
        if slot.intent is BatteryIntent.GRID_CHARGE_TO_TARGET
    }
    return {
        "date": day["date"],
        "tags": list(day["tags"]),
        "before": before_avg,
        "after": after_avg,
        "before_kwh": before_kwh,
        "after_kwh": after_kwh,
        "marks": len(marked),
        "planned": len(planned),
        "same_slots": planned == marked,
    }


def _format_report(planner: str, rows: list[dict]) -> str:
    lines = [
        f"{planner}: average grid-charge EUR/kWh "
        "(before = earliest-first pump, after = plan GRID_CHARGE)",
        f"{'date':<12} {'tags':<22} {'kWh':>8} {'before':>12} {'after':>12}",
    ]
    num = den = 0.0
    for row in rows:
        tags = ",".join(row["tags"])
        lines.append(
            f"{row['date']:<12} {tags:<22} {row['after_kwh']:8.3f} "
            f"{row['before']:12.6f} {row['after']:12.6f}"
        )
        den += row["after_kwh"]
        num += row["after"] * row["after_kwh"]
    before_num = sum(row["before"] * row["before_kwh"] for row in rows)
    before_den = sum(row["before_kwh"] for row in rows)
    before_total = before_num / before_den if before_den else 0.0
    after_total = num / den if den else 0.0
    lines.append(
        f"{'TOTAL':<12} {'':<22} {den:8.3f} {before_total:12.6f} {after_total:12.6f}"
    )
    return "\n".join(lines)


def test_late_pack_14d_fixture_shape():
    """The locked design: 14×96 slots, a flat day, a negative day, and a 5 Oct day."""
    raw = _load_fixture()
    assert raw["timezone"] == "Europe/Amsterdam"
    assert raw["slot_minutes"] == 15
    assert raw["slots_per_day"] == 96
    days = raw["days"]
    assert len(days) == 14
    assert len({day["date"] for day in days}) == 14
    for day in days:
        assert day["design"].strip()
        assert len(day["eur_per_kwh"]) == 96
        expanded = [hour for hour in day["hours_eur_per_kwh"] for _ in range(4)]
        assert day["eur_per_kwh"] == expanded
    assert any("flat" in day["tags"] and len(set(day["eur_per_kwh"])) == 1 for day in days)
    assert any("negative" in day["tags"] and min(day["eur_per_kwh"]) < 0 for day in days)
    assert any("oct5" in day["tags"] for day in days)


def test_late_pack_14day_average_eur_per_kwh():
    """Late-pack average €/kWh stays <= earliest-first, at the same kWh per day.

    An earliest-first regression on a 5 Oct day restores the legacy marks and fails
    the slot-set check below. Prices in the fixture are not adjusted to this result.
    """
    days = _load_fixture()["days"]
    failures: list[str] = []
    reports: list[str] = []
    for planner in ("adaptive", "summer"):
        rows = [_evaluate_day(day, planner) for day in days]
        reports.append(_format_report(planner, rows))
        for row in rows:
            if row["after_kwh"] <= 0.0 or row["marks"] == 0:
                failures.append(f"{planner} {row['date']}: no grid charge to price")
                continue
            if abs(row["before_kwh"] - row["after_kwh"]) > _TOL:
                failures.append(
                    f"{planner} {row['date']}: kWh before {row['before_kwh']} "
                    f"!= after {row['after_kwh']}"
                )
            if row["after"] > row["before"] + _TOL:
                failures.append(
                    f"{planner} {row['date']}: after {row['after']:.6f} > "
                    f"before {row['before']:.6f} EUR/kWh "
                    f"(kWh {row['after_kwh']:.3f})"
                )
            if "oct5" in row["tags"] and row["same_slots"]:
                failures.append(
                    f"{planner} {row['date']}: GRID_CHARGE slots match earliest-first marks"
                )
        before_kwh = sum(row["before_kwh"] for row in rows)
        after_kwh = sum(row["after_kwh"] for row in rows)
        before_avg = sum(row["before"] * row["before_kwh"] for row in rows) / before_kwh
        after_avg = sum(row["after"] * row["after_kwh"] for row in rows) / after_kwh
        if abs(before_kwh - after_kwh) > _TOL:
            failures.append(f"{planner} total kWh {before_kwh} != {after_kwh}")
        if after_avg > before_avg + _TOL:
            failures.append(
                f"{planner} 14-day average after {after_avg:.6f} > before {before_avg:.6f}"
            )
    report = "\n\n".join(reports)
    print(report)
    assert not failures, report + "\n\n" + "\n".join(failures)
