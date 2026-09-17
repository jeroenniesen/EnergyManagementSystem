"""Read-only calibration evidence; estimates never change battery settings."""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from statistics import median
from zoneinfo import ZoneInfo

from ems.planner.load_profile import build_load_profile


def valid_rows(rows: list[dict], fields: tuple[str, ...]) -> list[tuple[datetime, dict]]:
    out = {}
    for row in rows:
        try:
            ts = datetime.fromisoformat(row["ts"])
            ts = ts.replace(tzinfo=UTC) if ts.tzinfo is None else ts
            values = {key: float(row[key]) for key in fields}
            if all(math.isfinite(v) for v in values.values()):
                out[ts] = values
        except (KeyError, TypeError, ValueError):
            continue
    return sorted(out.items())


def battery_calibration(rows: list[dict], *, configured_kwh: float) -> dict:
    """Infer capacity and efficiency jointly from substantial charge/discharge SoC changes.

    AC kWh per full SoC range is C/eta on charge and C*eta on discharge. Their geometric
    mean estimates capacity and their ratio estimates round-trip efficiency. The estimates
    depend on accurate vendor SoC and comparable operating conditions.
    """
    samples = valid_rows(rows, ("battery_power_w", "soc_pct"))
    segments: dict[int, list[tuple[float, float, float, float]]] = {-1: [], 1: []}
    direction = 0
    energy = delta = 0.0
    first_soc = last_soc = elapsed = 0.0

    def finish():
        if direction and abs(delta) >= 15 and energy > 0:
            capacity_ac = energy / (abs(delta) / 100)
            if 0.25 * configured_kwh <= capacity_ac <= 2 * configured_kwh:
                segments[direction].append((
                    capacity_ac, min(first_soc, last_soc), max(first_soc, last_soc),
                    energy / elapsed * 3600,
                ))

    for (ta, a), (tb, b) in zip(samples, samples[1:], strict=False):
        seconds = (tb - ta).total_seconds()
        pa, pb = a["battery_power_w"], b["battery_power_w"]
        change = b["soc_pct"] - a["soc_pct"]
        sign = 1 if pa > 100 and pb > 100 else -1 if pa < -100 and pb < -100 else 0
        plausible = (
            0 < seconds <= 600
            and sign
            and change * sign <= 0
            and abs(change) <= 10
            and all(0 <= x["soc_pct"] <= 100 for x in (a, b))
        )
        if not plausible or sign != direction:
            finish()
            energy = delta = 0.0
            elapsed = 0.0
            first_soc = a["soc_pct"]
            direction = sign if plausible else 0
        if plausible:
            energy += abs(pa + pb) / 2 * seconds / 3_600_000
            delta += change
            elapsed += seconds
            last_soc = b["soc_pct"]
    finish()
    estimates = {
        direction: [
            cap for cap, low, high, rate in values
            if any(min(high, other_high) - max(low, other_low) >= 15
                   and .5 <= rate / other_rate <= 2
                   for _, other_low, other_high, other_rate in segments[-direction])
        ]
        for direction, values in segments.items()
    }
    base = {
        "available": False,
        "automatic": False,
        "standby_w": None,
        "charge_segments": len(estimates[-1]),
        "discharge_segments": len(estimates[1]),
        "reason": "Need three charge and discharge segments across overlapping 15% SoC ranges "
        "at comparable power.",
        "standby_reason": "Standby consumption is not identifiable from these meter readings.",
    }
    if min(len(estimates[-1]), len(estimates[1])) < 3:
        return base
    charge, discharge = median(estimates[-1]), median(estimates[1])
    efficiency = discharge / charge
    stable = all(max(v) / min(v) <= 1.3 for v in estimates.values())
    if not stable or not 0.5 <= efficiency <= 1:
        return {
            **base,
            "reason": "Charge/discharge evidence is inconsistent; keep manual settings.",
        }
    return {
        **base,
        "available": True,
        "usable_kwh": round(math.sqrt(charge * discharge), 2),
        "round_trip_efficiency": round(efficiency, 3),
        "reason": "Estimate from AC energy and vendor SoC; review before changing settings.",
    }


def load_accuracy(rows: list[dict], *, now: datetime, tz: ZoneInfo) -> dict:
    """Compare models on completed held-out hours, training only on previous days."""
    rows = [
        r
        for t, r0 in valid_rows(rows, ("non_ev_load_w",))
        if t < now
        for r in [{"ts": t.isoformat(), **r0}]
    ]
    buckets = defaultdict(list)
    for row in rows:
        ts = datetime.fromisoformat(row["ts"])
        buckets[ts.replace(minute=0, second=0, microsecond=0)].append(row["non_ev_load_w"])
    base_errors, errors = [], []
    today = now.astimezone(tz).replace(hour=0, minute=0, second=0, microsecond=0)
    for days_ago in range(7, 0, -1):
        day = today - timedelta(days=days_ago)
        prior = [r for r in rows if datetime.fromisoformat(r["ts"]) < day]
        dates = {datetime.fromisoformat(r["ts"]).astimezone(tz).date() for r in prior}
        if len(dates) < 7:
            continue
        baseline = build_load_profile(prior, tz, as_of=day)
        enhanced = build_load_profile(prior, tz, as_of=day, enhanced=True)
        for ts, values in buckets.items():
            if ts.astimezone(tz).date() != day.date():
                continue
            actual = sum(values) / len(values)
            base_errors.append(abs(actual - baseline.expected_w(ts)))
            errors.append(abs(actual - enhanced.expected_w(ts)))
    if len(errors) < 24:
        return {
            "available": False,
            "hours_scored": len(errors),
            "reason": "Need seven training days and at least 24 subsequent observed hours.",
        }
    return {
        "available": True,
        "hours_scored": len(errors),
        "baseline_mae_w": round(sum(base_errors) / len(errors), 1),
        "enhanced_mae_w": round(sum(errors) / len(errors), 1),
        "reason": "Held-out completed hours; each model trains only on earlier days.",
    }
