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
