"""Read-only load-forecast calibration evidence (B-64 / #81).

Sliced from draft PR #65 (`ems/calibration.py`): held-out MAE of the enhanced household load
model vs. the naive hour-of-day recent-average baseline. Estimates never change battery settings.
Battery/bill calibration from that PR is intentionally NOT carried over — out of scope for #81.
"""
from __future__ import annotations

import math
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from ems.planner.load_profile import build_load_profile


def _valid_load_rows(rows: list[dict], field: str = "non_ev_load_w") -> list[dict]:
    """Dedupe by timestamp, keep finite loads (+ optional solar for the weather feature)."""
    out: dict[datetime, dict] = {}
    for row in rows:
        try:
            ts = datetime.fromisoformat(row["ts"])
            ts = ts.replace(tzinfo=UTC) if ts.tzinfo is None else ts
            load = float(row[field])
            if not math.isfinite(load):
                continue
            payload = {"ts": ts.isoformat(), field: load}
            solar = row.get("solar_power_w")
            if solar is not None:
                solar_w = float(solar)
                if math.isfinite(solar_w) and solar_w >= 0.0:
                    payload["solar_power_w"] = solar_w
            out[ts] = payload
        except (KeyError, TypeError, ValueError):
            continue
    return [out[ts] for ts in sorted(out)]


def rows_from_raw_samples(raw_rows: list[dict], *, field: str = "non_ev_load_w") -> list[dict]:
    """Rebuild learnable load rows from raw meter samples (grid + solar + battery − EV).

    Used by `/api/accuracy` so weather (daytime solar) is available next to the load without a
    separate weather history store.
    """
    out: list[dict] = []
    for r in raw_rows:
        try:
            grid = float(r.get("grid_power_w", 0.0))
            solar = float(r.get("solar_power_w", 0.0))
            battery = float(r.get("battery_power_w", 0.0))
            ev = float(r.get("ev_power_w", 0.0))
            house = grid + solar + battery
            non_ev = house - (ev if ev > 200.0 else 0.0)
            if not math.isfinite(non_ev) or non_ev < 0.0:
                continue
            row = {"ts": r["ts"], field: non_ev, "solar_power_w": max(0.0, solar)}
            out.append(row)
        except (KeyError, TypeError, ValueError):
            continue
    return out


def attach_solar(derived_rows: list[dict], raw_rows: list[dict]) -> list[dict]:
    """Copy `solar_power_w` from raw samples onto matching derived rows (same `ts`).

    Keeps the derived load values that already passed reconstruction quarantine, while giving the
    enhanced profile its weather feature without inventing a new weather history table.
    """
    solar_by_ts: dict[str, float] = {}
    for r in raw_rows:
        try:
            solar = float(r.get("solar_power_w", 0.0))
        except (TypeError, ValueError):
            continue
        if math.isfinite(solar) and solar >= 0.0:
            ts = r.get("ts")
            if isinstance(ts, str):
                solar_by_ts[ts] = solar
    return [
        {**row, "solar_power_w": solar_by_ts[row["ts"]]}
        if isinstance(row.get("ts"), str) and row["ts"] in solar_by_ts
        else dict(row)
        for row in derived_rows
    ]


def load_accuracy(rows: list[dict], *, now: datetime, tz: ZoneInfo,
                  field: str = "non_ev_load_w") -> dict:
    """Compare models on completed held-out hours, training only on previous days.

    Baseline = naive hour-of-day recent average (`build_load_profile` without enhancement).
    Enhanced = weekday/weekend + season + weather proxy + recency weights (B-64).
    """
    rows = _valid_load_rows(rows, field=field)
    rows = [r for r in rows if datetime.fromisoformat(r["ts"]) < now]
    buckets: dict[datetime, list[float]] = defaultdict(list)
    solar_by_hour: dict[datetime, list[float]] = defaultdict(list)
    for row in rows:
        ts = datetime.fromisoformat(row["ts"]).replace(minute=0, second=0, microsecond=0)
        buckets[ts].append(float(row[field]))
        if "solar_power_w" in row:
            solar_by_hour[ts].append(float(row["solar_power_w"]))
    base_errors: list[float] = []
    errors: list[float] = []
    today = now.astimezone(tz).replace(hour=0, minute=0, second=0, microsecond=0)
    for days_ago in range(7, 0, -1):
        day = today - timedelta(days=days_ago)
        prior = [r for r in rows if datetime.fromisoformat(r["ts"]) < day]
        dates = {datetime.fromisoformat(r["ts"]).astimezone(tz).date() for r in prior}
        if len(dates) < 7:
            continue
        baseline = build_load_profile(prior, tz, as_of=day, field=field)
        enhanced = build_load_profile(prior, tz, as_of=day, enhanced=True, field=field)
        for ts, values in buckets.items():
            if ts.astimezone(tz).date() != day.date():
                continue
            actual = sum(values) / len(values)
            daytime_solar = None
            # Same calendar day's daytime solar mean as the weather feature at score time.
            day_local = ts.astimezone(tz).date()
            day_solars = [
                sum(v) / len(v)
                for hour_ts, v in solar_by_hour.items()
                if hour_ts.astimezone(tz).date() == day_local and v
                and hour_ts.astimezone(tz).hour in range(8, 17)
            ]
            if day_solars:
                daytime_solar = sum(day_solars) / len(day_solars)
            base_errors.append(abs(actual - baseline.expected_w(ts)))
            errors.append(abs(actual - enhanced.expected_w(ts, daytime_solar_w=daytime_solar)))
    if len(errors) < 24:
        return {
            "available": False,
            "hours_scored": len(errors),
            "baseline_mae_w": None,
            "enhanced_mae_w": None,
            "improves_on_baseline": None,
            "features": [],
            "reason": "Need seven training days and at least 24 subsequent observed hours.",
        }
    baseline_mae = round(sum(base_errors) / len(errors), 1)
    enhanced_mae = round(sum(errors) / len(errors), 1)
    # Report the feature set the last scored enhanced model would expose (same builder).
    probe = build_load_profile(rows, tz, as_of=now, enhanced=True, field=field)
    return {
        "available": True,
        "hours_scored": len(errors),
        "baseline_mae_w": baseline_mae,
        "enhanced_mae_w": enhanced_mae,
        "improves_on_baseline": enhanced_mae < baseline_mae,
        "features": list(probe.features),
        "reason": "Held-out completed hours; each model trains only on earlier days.",
    }
