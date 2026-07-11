"""Export package: a single ZIP of analytics-ready CSVs + a JSON manifest.

Two audiences, one file: (a) the operator, to do their own analytics on the raw energy/price/finance
history; (b) a reviewer, to validate that the system is operating correctly in production (audit
trail, decision/plan snapshot, diagnostics, recorder health). Read-only, built from the local
stores. **Secrets are never included** — the manifest's config comes from `public_values`, which
masks tokens; IPs/tokens are never written to any member.

Pure assembly only: the API gathers the rows (async store reads) and hands them here as plain
dict/list data, so this module has no I/O and is fully unit-testable.
"""
from __future__ import annotations

import csv
import io
import json
import zipfile
from typing import Any

# CSV members and their columns (stable headers, ISO-UTC timestamps, SI units in the names).
RAW_COLUMNS = ("ts", "grid_power_w", "solar_power_w", "battery_power_w", "ev_power_w", "soc_pct")
DERIVED_COLUMNS = ("ts", "house_load_w", "non_ev_load_w")
PRICE_COLUMNS = ("start_ts", "eur_per_kwh")
FORECAST_COLUMNS = ("issued_date", "start", "p10_w", "p50_w", "p90_w")
FINANCE_COLUMNS = (
    "day", "has_data", "price_coverage", "grid_cost_eur", "battery_cost_eur",
    "baseline_cost_eur", "saved_eur", "grid_import_kwh", "grid_export_kwh",
    "battery_charge_kwh", "battery_discharge_kwh",
)
AUDIT_COLUMNS = ("id", "ts", "category", "summary", "detail")
PLAN_COLUMNS = ("ts", "strategy", "target_soc", "deadline", "soc_pct", "intent")
GAS_COLUMNS = ("ts", "total_gas_m3")


def rows_to_csv(rows: list[dict], columns: tuple[str, ...]) -> str:
    """Serialise dict rows to CSV with a fixed header. Unknown keys are ignored; missing keys are
    blank; any dict/list cell (e.g. an audit `detail`) is JSON-encoded so the CSV stays flat."""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(columns), extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({
            k: (json.dumps(v, ensure_ascii=False) if isinstance(v, dict | list) else v)
            for k, v in row.items() if k in columns
        })
    return buf.getvalue()


def build_zip(members: dict[str, str]) -> bytes:
    """Pack `{filename: text}` into a deterministic, DEFLATE-compressed ZIP (members sorted by
    name; fixed timestamp so identical inputs yield identical bytes)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in sorted(members):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            zf.writestr(info, members[name])
    return buf.getvalue()


def zip_names(data: bytes) -> list[str]:
    """The member filenames in a ZIP (helper for callers/tests)."""
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        return zf.namelist()


def read_member(data: bytes, name: str) -> str:
    """Read one member's text from a ZIP (helper for tests)."""
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        return zf.read(name).decode("utf-8")


# Incident classification: (type, keywords) in priority order — a row is classified by the FIRST
# type whose keyword(s) appear in its summary+detail text; a row matching none is not an incident.
_INCIDENT_TYPES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("cluster_mismatch", ("mismatch",)),
    ("command_failed", ("unconfirmed",)),
    ("fallback", ("fallback", "failsafe")),
    ("revert", ("revert",)),
)


def incident_rollup(audit_rows: list[dict]) -> dict:
    """Roll hundreds of audit rows up into a control-health incident summary: command failures,
    cluster mismatches, fallbacks and reverts — the operational problems worth seeing at a glance,
    as opposed to the routine decisions/config changes that make up most of the audit log.

    Each row is classified by scanning its `summary` + `detail` text (case-insensitive) against
    `_INCIDENT_TYPES`, in order; a row counts once, under the first type that matches. Rows
    matching none are not incidents and are ignored. Pure — no clock, no I/O; `last_7_days` is
    computed relative to the newest incident found in the data, not wall-clock time.
    """
    by_type: dict[str, int] = {}
    by_day: dict[str, int] = {}
    incident_ts: list[str] = []
    for row in audit_rows:
        summary = row.get("summary") or ""
        detail = row.get("detail") or ""
        if isinstance(detail, dict | list):
            detail_text = json.dumps(detail, ensure_ascii=False)
        else:
            detail_text = str(detail)
        # Hyphen-insensitive so the runtime text "fail-safe" matches the "failsafe" keyword.
        text = f"{summary} {detail_text}".lower().replace("-", "")
        for itype, keywords in _INCIDENT_TYPES:
            if any(kw in text for kw in keywords):
                by_type[itype] = by_type.get(itype, 0) + 1
                ts = row.get("ts")
                if ts:
                    by_day[ts[:10]] = by_day.get(ts[:10], 0) + 1
                    incident_ts.append(ts)
                break
    if not incident_ts:
        return {"total": 0, "by_type": {}, "by_day": {}, "most_recent": None, "last_7_days": 0}
    most_recent = max(incident_ts)
    newest_day = most_recent[:10]
    last_7_days = sum(1 for ts in incident_ts if _days_between(ts[:10], newest_day) <= 7)
    return {
        "total": len(incident_ts),
        "by_type": by_type,
        "by_day": by_day,
        "most_recent": most_recent,
        "last_7_days": last_7_days,
    }


def _days_between(day: str, newest_day: str) -> int:
    from datetime import date
    return (date.fromisoformat(newest_day) - date.fromisoformat(day)).days


def readme_text() -> str:
    """A self-contained guide to the package: what each CSV holds, units, sign conventions, and
    how to load it. Static — the per-package window/counts live in manifest.json."""
    return """# EMS export package

A snapshot of your home energy manager's recorded history, for your own analytics and for a
health check of production operation. All timestamps are **UTC, ISO-8601**. All power is in
**watts (W)**, energy in **kWh**, money in **EUR**.

## Sign conventions (important)
- `grid_power_w`: **+ = importing** from the grid, **− = exporting** to the grid.
- `battery_power_w`: **+ = discharging** (battery powering the house), **− = charging**.
- `solar_power_w`, `ev_power_w`: ≥ 0 (production / car charging).

## Files
- **raw_samples.csv** — the meters, one row per recorder sample:
  `ts, grid_power_w, solar_power_w, battery_power_w, ev_power_w, soc_pct`.
- **derived_samples.csv** — reconstructed load (P1 is net grid, not house load):
  `ts, house_load_w` (total incl. car), `non_ev_load_w` (house only).
- **prices.csv** — the electricity price that was active in each 15-min slot:
  `start_ts, eur_per_kwh`.
- **forecasts.csv** — the solar forecast recorded for each 15-min slot:
  `issued_date, start, p10_w, p50_w, p90_w`. Join `start` to raw_samples' `solar_power_w` to
  measure forecast error; `p50_w` is the expected case, `p10_w`/`p90_w` the confidence band;
  `issued_date` is the day the forecast was made (the first snapshot per slot is kept, so this is
  the day-ahead forecast, not a later same-day nowcast).
- **daily_finance.csv** — measured money per local day:
  `day, has_data, price_coverage, grid_cost_eur, battery_cost_eur, baseline_cost_eur,
  saved_eur, grid_import_kwh, grid_export_kwh, battery_charge_kwh, battery_discharge_kwh`.
  `saved_eur` = no-battery baseline grid cost − actual grid cost − battery wear;
  `price_coverage` (0..1) is how much of the day had a known price.
- **audit_log.csv** — every decision, config change, override and AI check the system made:
  `id, ts, category, summary, detail` (detail is a JSON object).
- **plan_history.csv** — what the planner intended each cycle:
  `ts, strategy, target_soc, deadline, soc_pct, intent`. `target_soc` is the SoC the planner
  aimed for at that moment, `strategy` is the resolved summer/winter strategy, `intent` is the
  battery mode it was pursuing, and `soc_pct` is the SoC observed at that same moment. Compare
  `target_soc` against the achieved `soc_pct` in raw_samples (by `ts`) to see how well the plan
  tracked reality over time.
- **gas.csv** — cumulative gas meter (m³), one row per recorder cycle a gas meter is paired:
  `ts, total_gas_m3`. It's a running total, not a per-cycle volume — a day's use is that day's
  last reading minus its first. Folds into the CO₂ footprint (Insights' CO₂ score) alongside
  electricity.
- **manifest.json** — what/when/window, row counts, and a privacy-safe validation block
  (run mode, planner settings, data quality, recorder health). No tokens, IPs or location.
  `manifest.incidents` summarises control-health events from the audit log (command failures,
  cluster mismatches, fallbacks, reverts) — a rollup, not a replacement for `audit_log.csv`.
- **validation_summary.txt** — the same health read in plain language, plus a "Solar forecast
  skill" section (bias, MAE, band coverage, actual vs forecast kWh) measuring how well the
  day-ahead forecast tracked reality — see forecasts.csv for the raw data behind it.

## Loading (Python / pandas)
```python
import pandas as pd, zipfile
z = zipfile.ZipFile("ems-export-YYYYMMDD.zip")
raw = pd.read_csv(z.open("raw_samples.csv"), parse_dates=["ts"])
```
"""


def _run_mode(dry_run: bool) -> str:
    if dry_run:
        return "DRY-RUN (watching only, no battery writes)"
    return "LIVE (battery writes armed)"


def _forecast_skill_lines(
    forecast_skill: dict[str, Any] | None,
    solar_confidence_advice: dict[str, Any] | None = None,
) -> list[str]:
    """The 'Solar forecast skill' section — omitted entirely when no forecast-error dict is
    given (older callers), and reduced to a one-liner when there's no matched-slot overlap yet.
    `solar_confidence_advice` is the optional `ems.analysis.recommend_solar_confidence(...)`
    result — when given (non-None), one extra suggestion line is appended. Purely informational:
    the export never changes the setting, it only reports the evidence-based suggestion."""
    if forecast_skill is None:
        return []
    n = forecast_skill.get("n_slots", 0)
    if not n:
        return ["", "Solar forecast skill", "  No matched forecast/actual slots yet."]
    bias = forecast_skill.get("bias_w")
    mae = forecast_skill.get("mae_w")
    coverage = forecast_skill.get("band_coverage_pct")
    actual_kwh = forecast_skill.get("actual_solar_kwh")
    forecast_kwh = forecast_skill.get("forecast_p50_kwh")
    if bias is None:
        read = "not enough data yet"
    elif bias < 0:
        read = f"forecast over-predicted solar by {abs(bias):.0f} W on average"
    elif bias > 0:
        read = f"forecast under-predicted solar by {bias:.0f} W on average"
    else:
        read = "forecast tracked actual solar almost exactly, on average"
    lines = [
        "",
        "Solar forecast skill",
        f"  Matched slots:   {n}",
        f"  Bias (mean):     {bias} W" if bias is not None else "  Bias (mean):     —",
        f"  MAE:             {mae} W" if mae is not None else "  MAE:             —",
        f"  Band coverage:   {coverage}% within [p10, p90]" if coverage is not None
        else "  Band coverage:   —",
        f"  Actual vs P50:   {actual_kwh} kWh vs {forecast_kwh} kWh"
        if actual_kwh is not None else "  Actual vs P50:   —",
        f"  Read: {read}.",
    ]
    if solar_confidence_advice is not None:
        rec = solar_confidence_advice.get("recommended_pct")
        cur = solar_confidence_advice.get("current_pct")
        cur_text = f"{cur:g}%" if cur is not None else "—"
        lines.append(f"  Suggested solar_confidence: {rec:g}% (currently {cur_text})")
    return lines


def validation_summary(
    *,
    generated_at: str,
    app_version: str,
    window: dict[str, str],
    counts: dict[str, int],
    validation: dict[str, Any],
    saved_total_eur: float | None,
    forecast_skill: dict[str, Any] | None = None,
    solar_confidence_advice: dict[str, Any] | None = None,
) -> str:
    """A one-screen, plain-language health read derived from the manifest data, so a reviewer (or
    the operator) can see at a glance whether the system is collecting data and operating sanely.
    `forecast_skill` is the optional `ems.analysis.forecast_error(...)` result — when given, a
    'Solar forecast skill' section is appended (omitted for older callers that don't pass one).
    `solar_confidence_advice` is the optional `ems.analysis.recommend_solar_confidence(...)`
    result — when given, one extra suggestion line is appended to that section. Advisory only:
    this never changes `planner.solar_confidence`, it only reports the evidence-based suggestion."""
    op = validation.get("operational", {})
    health = validation.get("health", {})
    rec = health.get("recorder") or {}
    incidents = validation.get("incidents") or {}
    by_type = incidents.get("by_type") or {}
    by_type_text = ", ".join(f"{k}={v}" for k, v in by_type.items()) if by_type else "none"
    saved = "—" if saved_total_eur is None else f"€{saved_total_eur:.2f}"
    lines = [
        "EMS export — validation summary",
        f"Generated: {generated_at}   App version: {app_version}",
        f"Window: {window.get('start')} → {window.get('end')}",
        "",
        "Data collected",
        f"  raw samples      {counts.get('raw_samples', 0)}",
        f"  derived samples  {counts.get('derived_samples', 0)}",
        f"  price slots      {counts.get('prices', 0)}",
        f"  finance days     {counts.get('daily_finance', 0)}",
        f"  audit entries    {counts.get('audit_log', 0)}",
        "",
        "Operation",
        f"  Run mode:       {_run_mode(bool(op.get('dry_run')))}",
        f"  Timezone:       {op.get('timezone', '?')}",
        f"  Data quality:   {health.get('data_quality', '?')}",
        f"  Battery probed: {'yes' if health.get('capability_present') else 'no'}",
        f"  Recorder:       last success {rec.get('last_success_at', '—')}, "
        f"{rec.get('consecutive_failures', '?')} consecutive failures",
        "",
        "Incidents",
        f"  Total:          {incidents.get('total', 0)} "
        f"(last 7 days: {incidents.get('last_7_days', 0)})",
        f"  Most recent:    {incidents.get('most_recent') or '—'}",
        f"  By type:        {by_type_text}",
        *_forecast_skill_lines(forecast_skill, solar_confidence_advice),
        "",
        "Result",
        f"  Measured savings over the window: {saved}",
        "",
        "See README.md for column definitions, units and sign conventions.",
    ]
    return "\n".join(lines) + "\n"


def app_version() -> str:
    """The installed app version for the manifest; 'unknown' if metadata isn't available."""
    try:
        from importlib.metadata import version
        return version("ems")
    except Exception:
        return "unknown"


def build_manifest(
    *,
    generated_at: str,
    app_version: str,
    window_start: str,
    window_end: str,
    counts: dict[str, int],
    extra: dict[str, Any] | None = None,
) -> str:
    """The manifest.json text: what this package is, when, over what window, and how much of each
    dataset it holds. `extra` carries the production-validation payload (redacted config,
    diagnostics, capability, recorder health, decision/plan snapshot) — see the API layer."""
    manifest: dict[str, Any] = {
        "kind": "ems-export-package",
        "schema_version": 1,
        "generated_at": generated_at,
        "app_version": app_version,
        "window": {"start": window_start, "end": window_end},
        "counts": counts,
    }
    if extra:
        manifest.update(extra)
    return json.dumps(manifest, indent=2, ensure_ascii=False, default=str)
