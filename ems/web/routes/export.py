"""Data-export routes (BACKLOG B-40 slice, extracted from create_app).

GET /api/export (single-table CSV/JSON download) · GET /api/export/package (the full support ZIP).
Both are read-only and open like reads. The package backfills `daily_finance` for the window via
the shared `ctx.ensure_day_finance` so the export covers days no one ever viewed; the manifest
carries only the replay-safe settings subset (`ctx.replay_setting_keys`) — no tokens, IPs or
coordinates. It does include the timezone and the car's weekly schedule (needed to replay the
planner); delete manifest.json before sharing if you prefer (privacy §12).
"""
from __future__ import annotations

import asyncio
import csv
import io
import logging
import os
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse, Response

from ems import export_package as expkg
from ems.analysis import (
    forecast_error,
    load_baseline_error,
    plan_execution_error,
    recommend_solar_confidence,
)
from ems.ev_schedule import parse_schedule
from ems.ev_session import detect_sessions
from ems.settings import SECRET_KEYS
from ems.storage.history import DERIVED_COLUMNS, RAW_COLUMNS
from ems.web.context import AppContext

_log = logging.getLogger("ems.web.export")
# daily_energy is never purged (B-13) and stays small — it rides along IN FULL regardless of the
# `days` window, so the year-over-year story is always in the export, not just the requested slice.
_FULL_DATE_RANGE = ("0000", "9999")
# server_log_tail.txt: bound how much of the app's own log rides along (B-40's noted diagnosis gap).
_SERVER_LOG_MAX_LINES = 400


def _read_text_file(path: str) -> str | None:
    """Best-effort whole-file text read for the server-log export member: `None` when the file is
    absent or unreadable for any reason (permissions, odd encoding) — never raises. Run off the
    event loop via `asyncio.to_thread` by the caller; the file is small (size-rotated, a few MB at
    most — see `ems/logging_setup.py`), so a single blocking read is cheap."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return None


def build_router(ctx: AppContext) -> APIRouter:
    router = APIRouter()

    @router.get("/api/export")
    async def export(
        kind: str = Query(default="raw", pattern="^(raw|derived)$"),
        fmt: str = Query(default="csv", pattern="^(csv|json)$", alias="format"),
        limit: int = Query(default=1000, ge=1, le=2000),
    ) -> Response:
        # Download recent history (oldest→newest) as CSV or JSON. Read-only, open like reads.
        columns = RAW_COLUMNS if kind == "raw" else DERIVED_COLUMNS
        rows: list[dict] = []
        if ctx.store is not None:
            recent = ctx.store.recent_raw if kind == "raw" else ctx.store.recent_derived
            rows = list(reversed(await recent(limit)))  # recent_* is newest-first; export ascending
        if fmt == "json":
            return JSONResponse(rows)
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=list(columns), extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
        # Build the filename from a locally-asserted safe value (not the raw query param) so the
        # header can never carry injected bytes even if the upstream regex guard were relaxed.
        safe_kind = "raw" if kind == "raw" else "derived"
        return Response(
            content=buf.getvalue(),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="ems-{safe_kind}.csv"'},
        )

    @router.get("/api/export/package")
    async def export_package_endpoint(days: int = Query(default=90, ge=1, le=400)) -> Response:
        """One ZIP: the recorded history as analytics-ready CSVs (energy, prices, daily finance,
        audit trail) plus a manifest for validating production operation. Read-only and privacy-safe
        to share: the manifest carries only the replay-safe settings subset — no tokens, IPs or
        coordinates. It does include the timezone and the car's weekly schedule (needed to replay
        the planner); delete manifest.json before sharing if you prefer."""
        now = datetime.now(UTC)
        start = now - timedelta(days=days)
        start_iso, end_iso = start.isoformat(), now.isoformat()
        raw: list[dict] = []
        derived: list[dict] = []
        prices: list[dict] = []
        forecasts: list[dict] = []
        finance: list[dict] = []
        audit: list[dict] = []
        plan: list[dict] = []
        gas: list[dict] = []
        observations: list[dict] = []
        daily_energy: list[dict] = []
        notifications: list[dict] = []
        if ctx.store is not None:
            row_cap = min(600_000, days * 24 * 60 + 1000)  # ~one row/min ceiling over the window
            raw = await ctx.store.raw_between(start_iso, end_iso, limit=row_cap)
            derived = await ctx.store.derived_between(start_iso, end_iso, limit=row_cap)
            prices = await ctx.store.prices_between(start_iso, end_iso)
            # Canonical prediction-ledger rows (design §4.2/§4.3) — the SAME single scoring
            # source `/api/accuracy` and the solar-confidence advisor read, so forecasts.csv /
            # the "Solar forecast skill" section below can never disagree with those surfaces.
            forecasts = await ctx.store.ledger_canonical_between("solar", start_iso, end_iso)
            plan = await ctx.store.plan_history_between(start_iso, end_iso)
            gas = await ctx.store.gas_between(start_iso, end_iso)
            # Compact 15-min observation rollup (design §4.1) + the outbox — windowed the SAME as
            # raw/derived/prices above.
            observations = await ctx.store.observations_between(start_iso, end_iso)
            notifications = await ctx.store.notifications_between(
                start_iso, end_iso, limit=5000)
            # daily_energy is never purged (B-13) and stays small — export it IN FULL, not just
            # this window's slice, so the year-over-year story is always in the package.
            daily_energy = await ctx.store.daily_energy_between(*_FULL_DATE_RANGE)
            # Self-complete the window before reading it back: `daily_finance` rows are otherwise
            # only ever written when a finance view for that day was requested (/api/finance), so
            # a day nobody looked at is silently absent from the export. Backfill every COMPLETED
            # local day the export window touches (already bounded by `days` <= 400) so
            # daily_finance.csv covers the whole window, not just previously-viewed days. One bad
            # day must not fail the whole export — best-effort per day.
            today_local = now.astimezone(ctx.site_tz).date()
            backfill_day = start.astimezone(ctx.site_tz).date()
            while backfill_day < today_local:
                try:
                    await ctx.ensure_day_finance(backfill_day)
                except Exception:
                    _log.exception(
                        "export/package: failed to backfill daily_finance for %s", backfill_day)
                backfill_day += timedelta(days=1)
            fin_rows = await ctx.store.daily_finance_between(
                start.date().isoformat(), (now.date() + timedelta(days=1)).isoformat())
            finance = [r["data"] for r in fin_rows]
        if ctx.audit_store is not None:
            audit = list(reversed(await ctx.audit_store.recent(limit=5000)))  # oldest→newest
        # EV charging sessions are DETECTED on-demand from the already-fetched raw rows (no
        # recorder state machine — see ems/ev_session.py) so the algorithm can be validated/tuned
        # from production data (docs/superpowers/specs/2026-07-12-ev-charging-design.md, "Export").
        ev_sessions = detect_sessions(raw)
        ev_soc_anchor: dict[str, Any] | None = None
        if ctx.store is not None:
            anchor = await ctx.store.get_car_soc_anchor()
            if anchor is not None:
                ev_soc_anchor = {"pct": anchor[0], "ts": anchor[1]}
        # Production-validation payload — privacy-safe (only the replay-safe settings, no IPs /
        # tokens / location). Lets a reviewer see run mode, the planner knobs in effect, and live
        # health (data quality, whether the battery capability probed, recorder liveness).
        s = ctx.settings_cache
        # server_log_tail.txt (B-40's noted diagnosis gap): the last ~400 lines of the app's own
        # log file, REDACTED, when one is resolvable. The launchd install logs to
        # ems/data/server.log NEXT TO the database (EMS_LOG_FILE — see ems/logging_setup.py); the
        # path is derived from the db_path's DIRECTORY (not hardcoded) so a custom db_path still
        # finds its sibling log. Absent/unreadable (dev run, fresh install, permissions) means the
        # member is simply omitted — never a failed export; the README explains why.
        server_log_text: str | None = None
        server_log_lines = 0
        if ctx.store is not None and ctx.store.db_path:
            log_path = os.path.join(os.path.dirname(ctx.store.db_path) or ".", "server.log")
            raw_log = await asyncio.to_thread(_read_text_file, log_path)
            if raw_log is not None:
                tail = expkg.tail_lines(raw_log, _SERVER_LOG_MAX_LINES)
                # Mask the CURRENT value of every secret-type setting + the ntfy URL & topic
                # (privacy-sensitive even though they aren't schema-typed "secret") verbatim, on
                # top of the pattern-based Bearer/authorization/API-key redaction inside
                # redact_log_text. The URL as well as the topic: a self-hosted ntfy host is itself
                # identifying, and an httpx error line echoes the full URL.
                secret_values = [v for k in SECRET_KEYS if (v := s.get(k))]
                for extra_key in ("notify.ntfy_url", "notify.ntfy_topic"):
                    if extra := s.get(extra_key):
                        secret_values.append(extra)
                server_log_text = expkg.redact_log_text(tail, secret_values=secret_values)
                server_log_lines = len(tail.splitlines())
        validation = {
            "operational": {"dry_run": ctx.dry_run, "dev_mode": ctx.dev_mode,
                            "timezone": str(ctx.tz)},
            "config": {k: s.get(k) for k in ctx.replay_setting_keys if k in s},
            "health": {
                "data_quality": ctx.data_quality(now),
                "capability_present": ctx.capability_present(),
                "recorder": ctx.recorder.health() if ctx.recorder is not None else None,
            },
            "incidents": expkg.incident_rollup(audit),
            # Config needed to replay the EV charging algorithm against ev_sessions.csv — no
            # tokens/IPs/location; a % + timestamp anchor is privacy-safe and useful for replay.
            "ev": {
                "schedule": parse_schedule(s.get("ev.schedule")),
                "car_id": s.get("ev.car_id"),
                "battery_kwh": s.get("ev.battery_kwh"),
                "charger_kw": s.get("ev.charger_kw"),
                "charge_efficiency": s.get("ev.charge_efficiency"),
                "advice_enabled": s.get("ev.advice_enabled"),
                "soc_anchor": ev_soc_anchor,
            },
        }
        counts = {"raw_samples": len(raw), "derived_samples": len(derived),
                  "prices": len(prices), "forecasts": len(forecasts),
                  "daily_finance": len(finance), "audit_log": len(audit),
                  "plan_history": len(plan), "gas": len(gas), "ev_sessions": len(ev_sessions),
                  "observations": len(observations), "daily_energy": len(daily_energy),
                  "notifications": len(notifications), "server_log_lines": server_log_lines}
        saved_vals = [d["saved_eur"] for d in finance if d.get("saved_eur") is not None]
        saved_total = round(sum(saved_vals), 2) if saved_vals else None
        window = {"start": start_iso, "end": end_iso}
        fc_skill = forecast_error(forecasts, raw)
        solar_advice = recommend_solar_confidence(
            forecasts, raw,
            current_pct=float(s.get("planner.solar_confidence", 80.0)))
        ev_adherence = expkg.ev_price_adherence(ev_sessions, prices)
        # Two more forecast-accuracy tracks (B-72), scored off the SAME rows already fetched for
        # plan_history.csv / raw_samples.csv above — no extra store round-trip.
        plan_exec_error = plan_execution_error(plan, tz=ctx.site_tz)
        load_baseline = load_baseline_error(raw, tz=ctx.site_tz)
        members = {
            "raw_samples.csv": expkg.rows_to_csv(raw, expkg.RAW_COLUMNS),
            "derived_samples.csv": expkg.rows_to_csv(derived, expkg.DERIVED_COLUMNS),
            "prices.csv": expkg.rows_to_csv(prices, expkg.PRICE_COLUMNS),
            "forecasts.csv": expkg.rows_to_csv(forecasts, expkg.FORECAST_COLUMNS),
            "daily_finance.csv": expkg.rows_to_csv(finance, expkg.FINANCE_COLUMNS),
            "audit_log.csv": expkg.rows_to_csv(audit, expkg.AUDIT_COLUMNS),
            "plan_history.csv": expkg.rows_to_csv(plan, expkg.PLAN_COLUMNS),
            "gas.csv": expkg.rows_to_csv(gas, expkg.GAS_COLUMNS),
            "ev_sessions.csv": expkg.rows_to_csv(ev_sessions, expkg.EV_SESSION_COLUMNS),
            "observations.csv": expkg.rows_to_csv(observations, expkg.OBSERVATION_COLUMNS),
            "daily_energy.csv": expkg.rows_to_csv(daily_energy, expkg.DAILY_ENERGY_COLUMNS),
            "notifications.csv": expkg.rows_to_csv(notifications, expkg.NOTIFICATION_COLUMNS),
            "manifest.json": expkg.build_manifest(
                generated_at=now.isoformat(), app_version=expkg.app_version(),
                window_start=start_iso, window_end=end_iso, counts=counts, extra=validation,
            ),
            "README.md": expkg.readme_text(),
            "validation_summary.txt": expkg.validation_summary(
                generated_at=now.isoformat(), app_version=expkg.app_version(), window=window,
                counts=counts, validation=validation, saved_total_eur=saved_total,
                forecast_skill=fc_skill, solar_confidence_advice=solar_advice,
                ev_price_adherence=ev_adherence,
                plan_execution_error=plan_exec_error, load_baseline_error=load_baseline,
            ),
        }
        if server_log_text is not None:
            members["server_log_tail.txt"] = server_log_text
        data = expkg.build_zip(members)
        fname = f"ems-export-{now.strftime('%Y%m%d')}.zip"
        return Response(content=data, media_type="application/zip",
                        headers={"Content-Disposition": f'attachment; filename="{fname}"'})

    return router
