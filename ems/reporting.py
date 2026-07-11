"""Insights report assembly — see docs/superpowers/specs/2026-07-01-insights-reporting-design.md.

Pure — the API resolves the window and hands in stored rows + price slots + the CO₂ factors; this
builds the EnergyFlows window and the three scores. Window resolution (day/week/month/year) lives
here too, so the API just parses params. No I/O.
"""
from __future__ import annotations

import bisect
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from datetime import date as date_cls
from zoneinfo import ZoneInfo

from ems.energy_flow import build_flows
from ems.retrospect import _floor, _mean, _parse
from ems.scores import (
    DEFAULT_GAS_CO2,
    DEFAULT_GRID_CO2,
    best_price_score,
    co2_score,
    self_consumption_score,
)

PERIODS = ("day", "week", "month", "year")
_DH = 15 / 60.0  # hours per 15-min slot


@dataclass(frozen=True)
class Report:
    """A window's insights: the energy-flow distribution + the three scores, ready for the API."""
    period: str
    window_start: str  # ISO UTC
    window_end: str    # ISO UTC
    label: str         # human window label
    partial: bool      # window still in progress
    flows: dict
    scores: list[dict]

    def to_dict(self) -> dict:
        return {
            "period": self.period, "window_start": self.window_start,
            "window_end": self.window_end, "label": self.label, "partial": self.partial,
            "flows": self.flows, "scores": self.scores,
        }


def resolve_window(
    period: str, anchor: date_cls, tz: ZoneInfo, now_local: datetime
) -> tuple[datetime, datetime, str, bool]:
    """[start, end) in `tz` for the period containing `anchor`, its label, and whether it's still in
    progress. `anchor` is any local date inside the desired window (default: today)."""
    if period == "week":
        monday = anchor - timedelta(days=anchor.weekday())
        start = datetime(monday.year, monday.month, monday.day, tzinfo=tz)
        end = start + timedelta(days=7)
        label = f"Week of {monday.isoformat()}"
    elif period == "month":
        start = datetime(anchor.year, anchor.month, 1, tzinfo=tz)
        nm_year, nm_month = (anchor.year + 1, 1) if anchor.month == 12 else (anchor.year,
                                                                             anchor.month + 1)
        end = datetime(nm_year, nm_month, 1, tzinfo=tz)
        label = f"{anchor.year}-{anchor.month:02d}"
    elif period == "year":
        start = datetime(anchor.year, 1, 1, tzinfo=tz)
        end = datetime(anchor.year + 1, 1, 1, tzinfo=tz)
        label = str(anchor.year)
    else:  # "day"
        start = datetime(anchor.year, anchor.month, anchor.day, tzinfo=tz)
        end = start + timedelta(days=1)
        label = anchor.isoformat()
    partial = start <= now_local < end
    return start, end, label, partial


def _import_price_slots(
    raw_rows: list[dict], prices: list, start: datetime, end: datetime
) -> list[tuple[float, float | None]]:
    """Per 15-min slot in the window: (grid-import kWh, €/kWh active then). Import = positive net
    grid only; price = the slot whose interval contains the slot start (bisect on sorted starts)."""
    start_utc, end_utc = start.astimezone(UTC), end.astimezone(UTC)
    grid_by: dict[datetime, list[float]] = defaultdict(list)
    for r in raw_rows:
        dt = _parse(r.get("ts"))
        if dt is None or dt < start_utc or dt >= end_utc:
            continue
        grid_by[_floor(dt)].append(float(r.get("grid_power_w", 0.0)))

    price_pts = sorted((p.start.astimezone(UTC), p.eur_per_kwh) for p in prices)
    starts = [s for s, _ in price_pts]

    out: list[tuple[float, float | None]] = []
    for slot in sorted(grid_by):
        import_kwh = max(0.0, _mean(grid_by[slot])) * _DH / 1000.0
        i = bisect.bisect_right(starts, slot) - 1
        price = price_pts[i][1] if i >= 0 else None
        out.append((import_kwh, price))
    return out


def gas_m3_consumed(gas_rows: list[dict]) -> float:
    """Window gas consumption (m3) from cumulative meter readings: last reading minus first,
    floored at 0 (a meter reset/rollover must never report negative use). 0.0 with fewer than two
    readings — a single point (or none) carries no delta."""
    if len(gas_rows) < 2:
        return 0.0
    first = float(gas_rows[0]["total_gas_m3"])
    last = float(gas_rows[-1]["total_gas_m3"])
    return max(0.0, last - first)


def build_series(
    raw_rows: list[dict],
    derived_rows: list[dict],
    *,
    period: str,
    start: datetime,
    end: datetime,
    tz: ZoneInfo,
) -> list[dict]:
    """How P1 (grid ±), house, car and solar behaved over the window (spec 2026-07-03 A), as a
    STABLE axis of buckets — every bucket in the window is present (sampled or not) so charts
    don't shift as data arrives. `day` → 96×15-min slots; `week`/`month` → local days; `year` →
    local months. Same math as the flow report: 15-min slot means → kWh, summed into buckets."""
    start_utc, end_utc = start.astimezone(UTC), end.astimezone(UTC)
    grid_by: dict[datetime, list[float]] = defaultdict(list)
    ev_by: dict[datetime, list[float]] = defaultdict(list)
    solar_by: dict[datetime, list[float]] = defaultdict(list)
    house_by: dict[datetime, list[float]] = defaultdict(list)
    for r in raw_rows:
        dt = _parse(r.get("ts"))
        if dt is None or dt < start_utc or dt >= end_utc:
            continue
        slot = _floor(dt)
        grid_by[slot].append(float(r.get("grid_power_w", 0.0)))
        ev_by[slot].append(float(r.get("ev_power_w", 0.0)))
        solar_by[slot].append(float(r.get("solar_power_w", 0.0)))
    for d in derived_rows:
        dt = _parse(d.get("ts"))
        if dt is None or dt < start_utc or dt >= end_utc:
            continue
        house_by[_floor(dt)].append(float(d.get("non_ev_load_w", 0.0)))

    def _bucket_starts() -> list[datetime]:
        if period == "day":
            n = int((end_utc - start_utc).total_seconds() // 900)
            return [start_utc + timedelta(minutes=15 * i) for i in range(n)]
        if period == "year":
            return [datetime(start.year, m, 1, tzinfo=tz) for m in range(1, 13)]
        days, cur = [], start
        while cur < end:
            days.append(cur)
            cur += timedelta(days=1)
        return days

    def _key(slot: datetime) -> object:
        if period == "day":
            return slot
        local = slot.astimezone(tz)
        return (local.year, local.month) if period == "year" else local.date()

    axis = _bucket_starts()
    buckets = {(_key(b) if period == "day" else
                ((b.year, b.month) if period == "year" else b.date())):
               {"start": b.astimezone(UTC).isoformat() if period == "day" else b.isoformat(),
                "grid_import_kwh": 0.0, "grid_export_kwh": 0.0, "house_kwh": 0.0,
                "car_kwh": 0.0, "solar_kwh": 0.0, "samples": 0}
               for b in axis}
    for slot in set(grid_by) | set(house_by):
        b = buckets.get(_key(slot))
        if b is None:
            continue
        grid_w = _mean(grid_by[slot]) if slot in grid_by else 0.0
        b["grid_import_kwh"] += max(0.0, grid_w) * _DH / 1000.0
        b["grid_export_kwh"] += max(0.0, -grid_w) * _DH / 1000.0
        b["house_kwh"] += (_mean(house_by[slot]) if slot in house_by else 0.0) * _DH / 1000.0
        b["car_kwh"] += (_mean(ev_by[slot]) if slot in ev_by else 0.0) * _DH / 1000.0
        b["solar_kwh"] += (_mean(solar_by[slot]) if slot in solar_by else 0.0) * _DH / 1000.0
        b["samples"] += len(grid_by.get(slot, ()))
    out = []
    for bstart in axis:
        key = _key(bstart) if period == "day" else (
            (bstart.year, bstart.month) if period == "year" else bstart.date())
        row = buckets[key]
        for k in ("grid_import_kwh", "grid_export_kwh", "house_kwh", "car_kwh", "solar_kwh"):
            row[k] = round(row[k], 3)
        out.append(row)
    return out


def build_report(
    raw_rows: list[dict],
    derived_rows: list[dict],
    prices: list,
    *,
    period: str,
    start: datetime,
    end: datetime,
    label: str,
    partial: bool,
    grid_factor: float = DEFAULT_GRID_CO2,
    gas_factor: float = DEFAULT_GAS_CO2,
    gas_m3: float = 0.0,
) -> Report:
    """Assemble the report for a resolved window from stored rows + price slots + CO₂ factors."""
    flows = build_flows(raw_rows, derived_rows, start, end, label=label, partial=partial)
    scores = [
        self_consumption_score(flows),
        co2_score(flows, grid_factor=grid_factor, gas_factor=gas_factor, gas_m3=gas_m3),
        best_price_score(_import_price_slots(raw_rows, prices, start, end)),
    ]
    return Report(
        period=period,
        window_start=start.astimezone(UTC).isoformat(),
        window_end=end.astimezone(UTC).isoformat(),
        label=label, partial=partial,
        flows=flows.to_dict(), scores=[s.to_dict() for s in scores],
    )
