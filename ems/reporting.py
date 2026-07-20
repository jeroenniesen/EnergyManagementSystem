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
from ems.perf import timed
from ems.retrospect import _floor, _parse
from ems.scores import (
    DEFAULT_GAS_CO2,
    DEFAULT_GRID_CO2,
    Score,
    best_price_score,
    co2_score,
    co2_score_from_totals,
    self_consumption_score,
    self_consumption_score_from_totals,
)
from ems.timeseries import observed_segments

PERIODS = ("day", "week", "month", "year")


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
    raw_rows: list[dict], prices: list, start: datetime, end: datetime,
    *, sample_interval_seconds: float = 900.0, max_hold_seconds: float | None = None,
) -> list[tuple[float, float | None]]:
    """Per 15-min slot in the window: (grid-import kWh, €/kWh active then). Import = positive net
    grid only; price = the slot whose interval contains the slot start (bisect on sorted starts)."""
    start_utc, end_utc = start.astimezone(UTC), end.astimezone(UTC)
    segments = observed_segments(
        raw_rows, start=start_utc, end=end_utc, fields=("grid_power_w",),
        nominal_interval_seconds=sample_interval_seconds, max_hold_seconds=max_hold_seconds,
    )

    price_pts = sorted((p.start.astimezone(UTC), p.eur_per_kwh) for p in prices)
    starts = [s for s, _ in price_pts]

    out: list[tuple[float, float | None]] = []
    for segment in segments:
        import_kwh = (
            max(0.0, segment.values["grid_power_w"])
            * segment.duration_seconds / 3_600_000.0
        )
        i = bisect.bisect_right(starts, segment.start) - 1
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


# Dutch 'bovenwaarde' (higher/gross calorific value) for natural gas — the standard NL conversion
# used on energy bills to translate metered m³ into kWh-equivalent.
GAS_KWH_PER_M3 = 9.77


def gas_summary(
    gas_rows: list[dict], *, price_eur_per_m3: float, co2_factor: float
) -> dict | None:
    """Make gas VISIBLE for the Insights gas panel: m³ consumed this window → kWh-equivalent → €
    → kg CO₂. None with fewer than two readings (no delta to show) — the panel hides rather than
    show a false zero."""
    if len(gas_rows) < 2:
        return None
    m3 = gas_m3_consumed(gas_rows)
    return {
        "m3": round(m3, 2),
        "kwh_eq": round(m3 * GAS_KWH_PER_M3, 2),
        "eur": round(m3 * price_eur_per_m3, 2),
        "co2_kg": round(m3 * co2_factor, 2),
    }


def build_series(
    raw_rows: list[dict],
    derived_rows: list[dict],
    *,
    period: str,
    start: datetime,
    end: datetime,
    tz: ZoneInfo,
    sample_interval_seconds: float = 900.0,
    max_hold_seconds: float | None = None,
) -> list[dict]:
    """How P1 (grid ±), house, car and solar behaved over the window (spec 2026-07-03 A), as a
    STABLE axis of buckets — every bucket in the window is present (sampled or not) so charts
    don't shift as data arrives. `day` → 96×15-min slots; `week`/`month` → local days; `year` →
    local months. Same math as the flow report: 15-min slot means → kWh, summed into buckets."""
    with timed("report.build"):
        return _build_series_impl(
            raw_rows, derived_rows, period=period, start=start, end=end, tz=tz,
            sample_interval_seconds=sample_interval_seconds, max_hold_seconds=max_hold_seconds,
        )


def _build_series_impl(
    raw_rows: list[dict],
    derived_rows: list[dict],
    *,
    period: str,
    start: datetime,
    end: datetime,
    tz: ZoneInfo,
    sample_interval_seconds: float,
    max_hold_seconds: float | None,
) -> list[dict]:
    start_utc, end_utc = start.astimezone(UTC), end.astimezone(UTC)
    grid_by: dict[datetime, list[float]] = defaultdict(list)
    for r in raw_rows:
        dt = _parse(r.get("ts"))
        if dt is None or dt < start_utc or dt >= end_utc:
            continue
        slot = _floor(dt)
        grid_by[slot].append(float(r.get("grid_power_w", 0.0)))

    normalized_raw = [
        {**r, "grid_power_w": r.get("grid_power_w", 0.0),
         "ev_power_w": r.get("ev_power_w", 0.0),
         "solar_power_w": r.get("solar_power_w", 0.0)}
        for r in raw_rows
    ]
    raw_segments = observed_segments(
        normalized_raw, start=start_utc, end=end_utc,
        fields=("grid_power_w", "ev_power_w", "solar_power_w"),
        nominal_interval_seconds=sample_interval_seconds, max_hold_seconds=max_hold_seconds,
    )
    normalized_derived = [
        {**row, "non_ev_load_w": row.get("non_ev_load_w", row.get("house_load_w", 0.0))}
        for row in derived_rows
    ]
    house_segments = observed_segments(
        normalized_derived, start=start_utc, end=end_utc, fields=("non_ev_load_w",),
        nominal_interval_seconds=sample_interval_seconds, max_hold_seconds=max_hold_seconds,
    )

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
    for segment in raw_segments:
        b = buckets.get(_key(segment.start))
        if b is None:
            continue
        hours = segment.duration_seconds / 3600.0
        grid_w = segment.values["grid_power_w"]
        b["grid_import_kwh"] += max(0.0, grid_w) * hours / 1000.0
        b["grid_export_kwh"] += max(0.0, -grid_w) * hours / 1000.0
        b["car_kwh"] += max(0.0, segment.values["ev_power_w"]) * hours / 1000.0
        b["solar_kwh"] += max(0.0, segment.values["solar_power_w"]) * hours / 1000.0
    for segment in house_segments:
        b = buckets.get(_key(segment.start))
        if b is not None:
            b["house_kwh"] += (
                max(0.0, segment.values["non_ev_load_w"])
                * segment.duration_seconds / 3_600_000.0
            )
    for slot, samples in grid_by.items():
        b = buckets.get(_key(slot))
        if b is not None:
            b["samples"] += len(samples)
    out = []
    for bstart in axis:
        key = _key(bstart) if period == "day" else (
            (bstart.year, bstart.month) if period == "year" else bstart.date())
        row = buckets[key]
        for k in ("grid_import_kwh", "grid_export_kwh", "house_kwh", "car_kwh", "solar_kwh"):
            row[k] = round(row[k], 3)
        out.append(row)
    return out


def build_series_from_daily_energy(
    daily_rows: list[dict], *, start: datetime, end: datetime, tz: ZoneInfo,
) -> list[dict]:
    """Year-view series (spec 2026-07-03 A) built from the `daily_energy` ROLLUP (one row/day,
    B-13) instead of iterating raw/derived samples (BACKLOG B-49 §4): the same 12-month bucket
    axis as `build_series(period="year", ...)`, but O(days in the window) instead of O(raw rows in
    the window) — for a year at a 300s cadence that's ~365 rows read/summed instead of ~100k+
    iterated twice (once for the Sankey flows, once again for this series). Day/week/month keep
    the raw path (`build_series`) — daily_energy is a DAILY rollup, too coarse for those finer
    buckets, and the raw fetch is already bounded to a much smaller window there.

    Field mapping — documented honestly, not every `build_series` field has an exact rollup
    equivalent:
      * `grid_import_kwh` / `grid_export_kwh` / `solar_kwh` — direct from the rollup, exact (same
        per-slot grid+/solar aggregation `aggregate_daily_energy` and `build_series` both do).
      * `house_kwh` — the rollup's `non_ev_load_kwh` (the same quantity `build_series`' `house_by`
        sums: derived `non_ev_load_w`).
      * `car_kwh` — the rollup's `ev_kwh`, inferred as `load_kwh − non_ev_load_kwh` using the
        load_model's THRESHOLDED ev signal (`load_model.reconstruct` zeroes sub-200W phantom
        draw), NOT the raw metered `ev_power_w` that `build_series` sums directly. The two are
        very close but not bit-identical — an honest approximation for the year view, not a
        regression in the day/week/month path (which is unchanged).
      * `samples` — `build_series` counts raw rows per bucket; the rollup has no row count, so
        this reports the number of days that month with any coverage (`coverage > 0`) instead. The
        frontend (`EnergyBehavior.tsx`) only ever tests `samples > 0` as a has-data/gap gate, which
        this preserves faithfully — it never reads the count itself.
    """
    months = [datetime(start.year, m, 1, tzinfo=tz) for m in range(1, 13)]
    buckets = {
        (b.year, b.month): {
            "start": b.isoformat(),
            "grid_import_kwh": 0.0, "grid_export_kwh": 0.0, "house_kwh": 0.0,
            "car_kwh": 0.0, "solar_kwh": 0.0, "samples": 0,
        }
        for b in months
    }
    for row in daily_rows:
        try:
            d = date_cls.fromisoformat(str(row.get("date")))
        except (ValueError, TypeError):
            continue
        b = buckets.get((d.year, d.month))
        if b is None:
            continue
        b["grid_import_kwh"] += float(row.get("grid_import_kwh") or 0.0)
        b["grid_export_kwh"] += float(row.get("grid_export_kwh") or 0.0)
        b["house_kwh"] += float(row.get("non_ev_load_kwh") or 0.0)
        b["car_kwh"] += float(row.get("ev_kwh") or 0.0)
        b["solar_kwh"] += float(row.get("solar_kwh") or 0.0)
        if float(row.get("coverage") or 0.0) > 0.0:
            b["samples"] += 1
    out = []
    for b in months:
        row = buckets[(b.year, b.month)]
        for k in ("grid_import_kwh", "grid_export_kwh", "house_kwh", "car_kwh", "solar_kwh"):
            row[k] = round(row[k], 3)
        out.append(row)
    return out


def apply_year_totals(
    report: dict, daily_rows: list[dict], *, grid_factor: float = DEFAULT_GRID_CO2,
    gas_factor: float = DEFAULT_GAS_CO2, raw_days: int = 0,
) -> dict:
    """Reconcile the YEAR view's internal contradiction (F2), mutating `report` (a `Report.to_dict`)
    in place and returning it. The `series` already comes from the never-purged daily_energy rollup
    (full year), but `flows` and the three scores were built from the raw window — which retention
    keeps for only `raw_days` — so a year view showed a full-year chart beside 90-day scores.

    For the year we recompute the two scores that CAN be derived from aggregate totals — self-
    consumption and CO₂ — from the full-year `daily_rows` rollup (via `scores.py`'s totals adapters,
    same math). best_price genuinely needs per-slot prices and flows need per-slot attribution, so
    both stay raw-window-based but are LABELED: best_price's explanation gains a note, and the flows
    block gains a `window_note` the UI captions the Sankey with. No-op if `daily_rows` is empty."""
    if not daily_rows:
        report.setdefault("flows", {})["window_note"] = None
        return report

    def _sum(key: str) -> float:
        return sum(float(r.get(key) or 0.0) for r in daily_rows)

    solar_kwh = _sum("solar_kwh")
    home_kwh = _sum("non_ev_load_kwh")
    car_kwh = _sum("ev_kwh")
    load_kwh = _sum("load_kwh")
    grid_import_kwh = _sum("grid_import_kwh")
    grid_export_kwh = _sum("grid_export_kwh")

    sc = self_consumption_score_from_totals(
        solar_kwh=solar_kwh, load_kwh=load_kwh, grid_import_kwh=grid_import_kwh,
        grid_export_kwh=grid_export_kwh).to_dict()
    # Electricity-only for the year (gas has no full-year rollup — see co2_score_from_totals).
    co2 = co2_score_from_totals(
        home_kwh=home_kwh, car_kwh=car_kwh, grid_import_kwh=grid_import_kwh,
        grid_factor=grid_factor, gas_factor=gas_factor).to_dict()

    note = f" (scored over the last {raw_days} days — slot prices needed)" if raw_days > 0 else ""
    rebuilt = []
    for s in report.get("scores", []):
        if s.get("key") == "self_consumption":
            rebuilt.append(sc)
        elif s.get("key") == "co2":
            rebuilt.append(co2)
        elif s.get("key") == "best_price" and note:
            rebuilt.append({**s, "explanation": s.get("explanation", "") + note})
        else:
            rebuilt.append(s)
    report["scores"] = rebuilt
    report.setdefault("flows", {})["window_note"] = (
        f"last {raw_days} days" if raw_days > 0 else None)
    return report


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
    grid_factor_note: str | None = None,
    sample_interval_seconds: float = 900.0,
    max_hold_seconds: float | None = None,
) -> Report:
    """Assemble the report for a resolved window from stored rows + price slots + CO₂ factors.

    `grid_factor_note` (roadmap F3): when the caller resolved `grid_factor` from a time-varying
    live signal for this window (rather than the flat setting), it passes a short human-readable
    note (e.g. " (live grid signal, avg 0.19 kg/kWh)") that's appended verbatim to the CO₂ score's
    explanation — cosmetic only, `co2_score`'s signature/math are untouched; `grid_factor` itself
    already carries the number that matters."""
    with timed("report.build"):
        flows = build_flows(
            raw_rows, derived_rows, start, end, label=label, partial=partial,
            sample_interval_seconds=sample_interval_seconds, max_hold_seconds=max_hold_seconds,
        )
        co2 = co2_score(flows, grid_factor=grid_factor, gas_factor=gas_factor, gas_m3=gas_m3)
        if grid_factor_note:
            co2 = Score(co2.key, co2.label, co2.value, co2.raw, co2.unit,
                        co2.explanation + grid_factor_note)
        scores = [
            self_consumption_score(flows),
            co2,
            best_price_score(_import_price_slots(
                raw_rows, prices, start, end,
                sample_interval_seconds=sample_interval_seconds,
                max_hold_seconds=max_hold_seconds,
            )),
        ]
        return Report(
            period=period,
            window_start=start.astimezone(UTC).isoformat(),
            window_end=end.astimezone(UTC).isoformat(),
            label=label, partial=partial,
            flows=flows.to_dict(), scores=[s.to_dict() for s in scores],
        )
