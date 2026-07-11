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
FINANCE_COLUMNS = (
    "day", "has_data", "price_coverage", "grid_cost_eur", "battery_cost_eur",
    "baseline_cost_eur", "saved_eur", "grid_import_kwh", "grid_export_kwh",
    "battery_charge_kwh", "battery_discharge_kwh",
)
AUDIT_COLUMNS = ("id", "ts", "category", "summary", "detail")


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
- **daily_finance.csv** — measured money per local day:
  `day, has_data, price_coverage, grid_cost_eur, battery_cost_eur, baseline_cost_eur,
  saved_eur, grid_import_kwh, grid_export_kwh, battery_charge_kwh, battery_discharge_kwh`.
  `saved_eur` = no-battery baseline grid cost − actual grid cost − battery wear;
  `price_coverage` (0..1) is how much of the day had a known price.
- **audit_log.csv** — every decision, config change, override and AI check the system made:
  `id, ts, category, summary, detail` (detail is a JSON object).
- **manifest.json** — what/when/window, row counts, and a privacy-safe validation block
  (run mode, planner settings, data quality, recorder health). No tokens, IPs or location.
- **validation_summary.txt** — the same health read in plain language.

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


def validation_summary(
    *,
    generated_at: str,
    app_version: str,
    window: dict[str, str],
    counts: dict[str, int],
    validation: dict[str, Any],
    saved_total_eur: float | None,
) -> str:
    """A one-screen, plain-language health read derived from the manifest data, so a reviewer (or
    the operator) can see at a glance whether the system is collecting data and operating sanely."""
    op = validation.get("operational", {})
    health = validation.get("health", {})
    rec = health.get("recorder") or {}
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
