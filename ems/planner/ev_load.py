"""Expected EV exogenous load for the winter planner (#181 / SPEC §4.5).

The learned house baseline deliberately excludes EV charging (`non_ev_load_w`). When the planner
*knows* the car will charge today, SPEC §4.5 says to re-add that energy as a **separately known**
quantity. This module estimates that quantity — advice/forecast only. It never commands a charger
or the car (B-17 / #105 stays blocked; see `docs/v2-ev-control.md`).

Priority (first match wins), fail-soft to 0 kWh when nothing is known:

1. Manual `expected_day_kwh` override (> 0)
2. Manual `day_hint` → typical `charge_kwh`
3. Day-type heuristic: today's weekday is enabled on the EV schedule → typical `charge_kwh`
4. Same-weekday history median from `daily_energy.ev_kwh` (when enough significant samples)

Pure + unit-testable — callers supply history rows and settings; no I/O here.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

from ems.ev_schedule import DAYS, parse_schedule

# Days with less EV than this are treated as non-EV for the same-weekday heuristic.
_SIGNIFICANT_EV_KWH = 5.0
_MIN_WEEKDAY_SAMPLES = 2


@dataclass(frozen=True)
class EvExogenousEstimate:
    """How much EV import (AC kWh) the winter planner should size against today."""

    expected_kwh: float
    source: str  # "manual" | "day_hint" | "schedule" | "same_weekday_history" | "none"
    reason: str

    def to_dict(self) -> dict:
        return {
            "expected_kwh": round(self.expected_kwh, 2),
            "source": self.source,
            "reason": self.reason,
        }


def _none(reason: str = "no EV exogenous estimate") -> EvExogenousEstimate:
    return EvExogenousEstimate(expected_kwh=0.0, source="none", reason=reason)


def _weekday_key(d: date) -> str:
    return DAYS[d.weekday()]


def _median_same_weekday_ev(
    daily_rows: list[dict],
    *,
    today: date,
    lookback_days: int = 28,
    min_samples: int = _MIN_WEEKDAY_SAMPLES,
    significant_kwh: float = _SIGNIFICANT_EV_KWH,
) -> float | None:
    """Median `ev_kwh` on the same weekday over recent history, or None if too sparse."""
    want = _weekday_key(today)
    values: list[float] = []
    for row in daily_rows:
        raw_date = row.get("date")
        raw_ev = row.get("ev_kwh")
        if not isinstance(raw_date, str) or raw_ev is None:
            continue
        try:
            d = date.fromisoformat(raw_date[:10])
            ev = float(raw_ev)
        except (TypeError, ValueError):
            continue
        if (today - d).days < 0 or (today - d).days > lookback_days:
            continue
        if d == today:  # don't use an incomplete today as a prior for itself
            continue
        if _weekday_key(d) != want:
            continue
        if ev >= significant_kwh:
            values.append(ev)
    if len(values) < min_samples:
        return None
    return float(statistics.median(values))


def estimate_ev_exogenous_kwh(
    *,
    now: datetime,
    tz: ZoneInfo,
    expected_day_kwh: float | None = None,
    day_hint: bool = False,
    typical_charge_kwh: float = 20.0,
    schedule_raw: str | dict | None = None,
    daily_ev_rows: list[dict] | None = None,
) -> EvExogenousEstimate:
    """Estimate today's expected EV import (AC kWh) for winter demand sizing.

    Never raises — corrupt inputs fail soft to 0 kWh with an explanatory `source="none"`.
    """
    try:
        local_today = now.astimezone(tz).date()
    except Exception:
        return _none("invalid now/tz for EV exogenous estimate")

    typical = max(0.0, float(typical_charge_kwh or 0.0))

    # 1) Explicit manual kWh override (highest priority when positive).
    if expected_day_kwh is not None:
        try:
            manual = float(expected_day_kwh)
        except (TypeError, ValueError):
            manual = 0.0
        if manual > 0.0:
            return EvExogenousEstimate(
                expected_kwh=manual,
                source="manual",
                reason=f"EV load expected ~{manual:.0f} kWh (manual day override)",
            )

    # 2) Manual "EV day" toggle → typical top-up size.
    if day_hint and typical > 0.0:
        return EvExogenousEstimate(
            expected_kwh=typical,
            source="day_hint",
            reason=f"EV load expected ~{typical:.0f} kWh (EV day hint)",
        )

    # 3) Day-type heuristic from the weekly charge schedule (enabled today ⇒ expect a charge).
    try:
        schedule = parse_schedule(schedule_raw)
        day = schedule.get(_weekday_key(local_today)) or {}
        if bool(day.get("enabled")) and typical > 0.0:
            return EvExogenousEstimate(
                expected_kwh=typical,
                source="schedule",
                reason=f"EV load expected ~{typical:.0f} kWh (schedule day)",
            )
    except Exception:
        pass  # fail-soft — schedule is advisory only

    # 4) Same-weekday history from daily_energy.ev_kwh.
    if daily_ev_rows:
        try:
            median = _median_same_weekday_ev(daily_ev_rows, today=local_today)
        except Exception:
            median = None
        if median is not None and median > 0.0:
            return EvExogenousEstimate(
                expected_kwh=median,
                source="same_weekday_history",
                reason=f"EV load expected ~{median:.0f} kWh (same-weekday history)",
            )

    return _none("no EV day signal (hint/schedule/history)")


def format_ev_load_reason(expected_kwh: float) -> str:
    """Canonical short reason fragment for plan slots / UI advice."""
    return f"EV load expected ~{expected_kwh:.0f} kWh"
