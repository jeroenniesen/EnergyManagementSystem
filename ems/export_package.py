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
