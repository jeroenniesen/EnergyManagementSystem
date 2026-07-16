"""Read-only status API (SPEC §9.1). No device writes in M0a."""
from __future__ import annotations

import asyncio
import glob
import hashlib
import json
import logging
import os
import re
import secrets
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from datetime import date as date_cls
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Query, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from ems import export_package as expkg
from ems.alerts import data_quality, derive_alerts
from ems.analysis import (
    forecast_error,
    recommend_solar_confidence,
)
from ems.cars import by_id as car_by_id
from ems.confidence import plan_confidence
from ems.control.mode_controller import ModeController
from ems.control.override import (
    MAX_MINUTES,
    MIN_MINUTES,
    Override,
)
from ems.control.override import (
    NONE as OVERRIDE_NONE,
)
from ems.control.override import (
    from_stored as override_from_stored,
)
from ems.control.service import (
    _CAR_SESSION_MAX_COMMANDS,  # noqa: F401 — re-exported for the closure-testing tests
    _LABEL_TO_MODE,  # noqa: F401 — re-exported for the closure-testing tests
    HYSTERESIS_KEY,
    ControlContext,
    ControlService,
    _commanded_family,  # noqa: F401 — re-exported for the closure-testing tests
    _commit_hysteresis_state,  # noqa: F401 — re-exported for the closure-testing tests
    _decide_car_command,  # noqa: F401 — re-exported for the closure-testing tests
    _decide_car_session_end,  # noqa: F401 — re-exported for the closure-testing tests
    _decide_grace_action,  # noqa: F401 — re-exported for the closure-testing tests
    _tower_family,  # noqa: F401 — re-exported for the closure-testing tests
)
from ems.detectors import (
    ev_plug_in_reminder,
    evening_peak_risk,
    low_solar_tomorrow,
    price_opportunity,
    typical_daily_solar_kwh,
)
from ems.diagnostics import build_diagnostics, overall_status
from ems.domain import BatteryIntent, PhysicalMode
from ems.energy_flow import build_daily_flows
from ems.ev_advisor import advise_charge_window
from ems.finance import day_finance, price_rows_by_local_day, raw_rows_by_local_day
from ems.freshness import FreshnessTracker
from ems.load_model import reconstruct
from ems.notify import Notifier
from ems.planner.charge_need import compute_charge_need
from ems.planner.explain import (
    ExternalLlmExplainer,
    TemplateExplainer,
    build_plan_detail,
    make_openai_chat_post,
    plan_metrics,
    summarize_projection,
)
from ems.planner.load_profile import build_load_profile
from ems.planner.projection import BatteryModel, project_energy
from ems.planner.recovery import check_charge_completion, recover_if_needed
from ems.planner.rule_based import plan_rule_based
from ems.planner.strategy import HysteresisState
from ems.planner.summer import sunset_after
from ems.planner.validator import PlanValidation, validate_plan
from ems.readiness import Readiness, compute_readiness, home_state
from ems.reporting import (
    apply_year_totals,
    build_report,
    build_series,
    build_series_from_daily_energy,
    gas_m3_consumed,
    gas_summary,
    resolve_window,
)
from ems.retrospect import build_past_story, past_headline
from ems.savings import estimate_daily_savings_eur
from ems.sense import Recorder
from ems.settings import (
    SECRET_KEYS,
    SETTINGS_BY_KEY,
    effective_settings,
    public_values,
    schema_json,
    validate_settings,
)
from ems.sky import sun_times
from ems.sources.base import Source
from ems.sources.battery import BatteryDriver
from ems.sources.forecast import SolarForecastSource, day_kwh_p50
from ems.sources.indevolt import aggregate_soc
from ems.sources.prices import PriceSlot, PriceSource, current_price
from ems.storage.audit import AuditStore
from ems.storage.cache import CacheStore
from ems.storage.history import (
    OBSERVATION_RETENTION_DAYS,
    HistoryStore,
    materialize_daily_energy,
    materialize_observations,
)
from ems.storage.settings import SettingsStore
from ems.weather import cloud_cover_pct
from ems.web.context import AppContext, history_row_cap
from ems.web.routes.accuracy import build_router as build_accuracy_router
from ems.web.routes.car import build_router as build_car_router
from ems.web.routes.car import gather_car_plan
from ems.web.routes.digest import (
    _last_completed_week_monday,  # noqa: F401 — re-exported for tests (test_digest_api)
    _run_weekly_digest,
    gather_digest,
)
from ems.web.routes.digest import (
    build_router as build_digest_router,
)
from ems.web.routes.export import build_router as build_export_router
from ems.web.routes.notify import build_router as build_notify_router
from ems.web.routes.whatif import build_router as build_whatif_router

_log = logging.getLogger("ems.recorder")


def _task_died(name: str):
    def _cb(task: asyncio.Task) -> None:
        # Background tasks are awaited only at shutdown; surface an unexpected death immediately.
        if not task.cancelled() and (exc := task.exception()) is not None:
            _log.error("%s task exited unexpectedly: %s", name, exc, exc_info=exc)

    return _cb


async def _run_backup(store: HistoryStore, db_path: str, keep: int,
                      state: dict[str, Any], notifier: Notifier | None = None) -> None:
    """Once-per-maintenance-cycle online DB backup + rotation (SPEC §11 durability). Writes
    `<db_dir>/backups/ems-YYYYMMDD.sqlite` (one snapshot per UTC day; skipped if today's already
    exists, so restarts don't re-snapshot), then prunes so only the newest `keep` snapshots remain
    — filenames sort lexicographically = chronologically. `keep <= 0` disables backups entirely.

    Best-effort: on ANY failure it logs loudly, records last_backup_ok=False in `state`, and NEVER
    raises — a durability hiccup must not kill the maintenance loop that also runs retention + WAL
    truncation. `state` is mutated in place (surfaced via /api/diagnostics).

    B-20 (first notification source, proving the outbox rails end-to-end): a failure pushes a
    calm `backup_failed` notification, deduped per UTC calendar day — repeated failures the SAME
    day are silent after the first, but a failure on a NEW day notifies again. A successful backup
    never sends anything (`notifier` itself is best-effort and never raises)."""
    if keep <= 0:
        return  # backups disabled by config (history.backup_keep = 0)
    now = datetime.now(UTC)
    backups_dir = os.path.join(os.path.dirname(db_path) or ".", "backups")
    dest = os.path.join(backups_dir, f"ems-{now:%Y%m%d}.sqlite")
    try:
        if not os.path.exists(dest):
            size = await store.backup_to(dest)
            state["last_backup_ts"] = now.isoformat()
            state["last_backup_ok"] = True
            state["last_backup_size"] = size
            _log.info("history backup: wrote %s (%d bytes)", dest, size)
        # Rotate: keep the newest `keep` by name (ems-YYYYMMDD sorts chronologically). Runs every
        # cycle even when today's snapshot was skipped, so an in-day restart still trims backlog.
        existing = sorted(glob.glob(os.path.join(backups_dir, "ems-*.sqlite")))
        stale = existing[:-keep]
        for path in stale:
            os.remove(path)
        state["backups_kept"] = len(existing) - len(stale)
    except Exception as exc:
        state["last_backup_ts"] = now.isoformat()
        state["last_backup_ok"] = False
        _log.warning("history backup failed (%s: %s); retrying next cycle",
                     type(exc).__name__, exc)
        if notifier is not None:
            await notifier.send(
                "backup_failed", "Backup failed",
                "Today's scheduled backup of your energy history didn't complete. Your data is "
                "safe — nothing has been lost, and EMS will try again automatically tomorrow. "
                "Check available disk space if this keeps happening.",
                dedupe_key=f"backup_failed:{now:%Y-%m-%d}",
            )


async def _run_store_health_check(
    recorder: Recorder | None, notifier: Notifier | None, now: datetime, tz,
    state: dict[str, Any], *, threshold: int = 10,
) -> None:
    """B-49 escalation: when the recorder has failed to persist for `threshold` consecutive cycles,
    the history store may be wedged (a dead long-lived connection, a full disk). Alert the operator
    ONCE per LOCAL day via `notifier.send`. Module-level + injected (like `_run_backup`) so it's
    directly unit-testable.

    Two dedupe layers, because the store itself may be the thing that's broken:
    * `state` is an in-memory ``{'alerted_date': iso|None}`` box — robust even when the store can't
      dedupe (a dead store can't check `dedupe_key`).
    * `push_even_if_store_fails=True` on the send, so the ntfy push still goes out even though the
      in-app write is exactly what's failing (ntfy needs no store).

    A no-op when recorder/notifier is absent or the streak is below `threshold`. Never raises here —
    `Notifier.send` is itself best-effort — but the caller still wraps it fail-safe."""
    if recorder is None or notifier is None:
        return
    streak = recorder.health().get("consecutive_failures", 0)
    if streak < threshold:
        return
    today = now.astimezone(tz).date().isoformat()
    if state.get("alerted_date") == today:
        return  # already alerted today
    state["alerted_date"] = today  # set BEFORE sending so a per-day alert fires at most once
    await notifier.send(
        "store_unhealthy", "Samples aren't being stored",
        "EMS hasn't been able to save new energy samples for a little while. Your battery is still "
        "being controlled safely, and everything already saved is intact. If this is still showing "
        "in an hour, restart the app to clear it.",
        dedupe_key=f"store_unhealthy:{today}",
        push_even_if_store_fails=True,
    )


async def _run_detectors(
    store: HistoryStore | None,
    notifier: Notifier | None,
    now: datetime,
    *,
    p50_by_slot_tomorrow: dict[datetime, float] | None = None,
    typical_daily_kwh: float | None = None,
    car_plan: dict[str, Any] | None = None,
    car_charging_now: bool = False,
    projected_soc_at_peak: float | None = None,
    needed_soc: float | None = None,
    confidence_level: str | None = None,
    price_slots_tomorrow: list[Any] | None = None,
) -> None:
    """Forecast-driven notifications (BACKLOG B-75): run the four pure detectors in
    `ems.detectors` against already-gathered PLAIN data and hand any that trigger to
    `notifier.send()`. Mirrors `_run_backup`'s shape — a plain, directly-testable function, NOT a
    closure — so all the live gathering (price_source/solar_forecast/store reads, the car-plan
    internals, the charge-need/projection for peak risk) happens in the caller
    (`create_app`'s `_run_detector_cycle`) and this stays pure glue, easy to unit-test with canned
    inputs the same way `test_backup.py` exercises `_run_backup`.

    Each detector call is individually wrapped: one detector raising (bad/unexpected data, a
    coding slip) must never block the others or escape to the caller — the same fail-safe
    convention as every other optional step in this codebase (`ems/sources/carbon.py`,
    `Notifier.send` itself, `_run_backup` above). A no-op when `store`/`notifier` isn't
    configured — there is nowhere to persist a notification."""
    if store is None or notifier is None:
        return
    checks: list[tuple[str, Any, tuple]] = [
        ("low_solar_tomorrow", low_solar_tomorrow,
         (p50_by_slot_tomorrow or {}, typical_daily_kwh)),
        ("ev_plug_in_reminder", ev_plug_in_reminder, (car_plan, car_charging_now)),
        ("evening_peak_risk", evening_peak_risk,
         (projected_soc_at_peak, needed_soc, confidence_level)),
        ("price_opportunity", price_opportunity, (price_slots_tomorrow or [],)),
    ]
    for name, fn, args in checks:
        try:
            result = fn(*args, now=now)
            if result is not None:
                await notifier.send(**result)
        except Exception:
            _log.warning("%s detector failed (non-fatal)", name, exc_info=True)


# One recovery per committed window (its deadline) per day; the KV key IS the deadline so it is
# inherently per-window — the TTL only bounds table growth past the window it protects.
_RECOVERY_DEDUPE_TTL_SECONDS = 24 * 3600


async def _run_recovery(
    plan,
    now: datetime,
    *,
    soc_pct: float,
    prices: list[Any],
    usable_kwh: float,
    reserve_soc_pct: float,
    max_charge_w: float,
    round_trip_efficiency: float,
    enabled: bool,
    tz: ZoneInfo,
    cache_store: CacheStore | None,
    notifier: Notifier | None,
    audit_store: AuditStore | None,
    validate_fn: Any,
    precomputed_catch: Any = None,
    precomputed_status: Any = None,
    margin_pp: float = 5.0,
    dedupe_ttl_seconds: float = _RECOVERY_DEDUPE_TTL_SECONDS,
) -> dict | None:
    """Missed-window recovery side effects (SPEC §8.12 / BACKLOG B-16).

    Uses the FRESH (pre-recovery) plan's charge-completion diagnosis and, on a MISSED window,
    runs the SAME §8.11 validator over the catch-up plan (`validate_fn` — recovery bypasses
    NOTHING) then
    AUDITs ("plan recovered: …") and sends a calm, B-37-style notification — on a full catch-up and
    on an impossible/partial one. Rate-limited to ONE recovery per committed window per day via the
    KV cache. The plan the controller acts on is reshaped separately in `_current_plan` (a pure,
    deterministic fold), so this stays a plain, directly-testable function — like `_run_detectors`.
    Returns a summary dict for tests/observability, or None when nothing was done. Never raises."""
    if not enabled or plan is None:
        return None
    if precomputed_catch is None:
        _reshaped, status, catch = recover_if_needed(
            plan, now, soc_pct=soc_pct, prices=prices, usable_kwh=usable_kwh,
            reserve_soc_pct=reserve_soc_pct, max_charge_w=max_charge_w,
            round_trip_efficiency=round_trip_efficiency, enabled=True, margin_pp=margin_pp,
        )
    else:
        catch = precomputed_catch
        status = (precomputed_status
                  or check_charge_completion(plan, now, soc_pct, margin_pp=margin_pp))
    if catch is None:  # on-pace / behind (within margin) / complete / not-applicable → nothing
        return None

    dedupe_key = f"recovery:{plan.deadline.isoformat()}"
    if cache_store is not None:
        try:
            if await asyncio.to_thread(cache_store.get, dedupe_key) is not None:
                return None  # this window already handled today (rate-limit)
        except Exception:
            _log.debug("recovery dedupe read failed (non-fatal)", exc_info=True)

    # Recovery NEVER bypasses the validator: the catch-up plan passes through the SAME §8.11 gate
    # (incl. the B-22 projection_short_of_target check). A rejected plan is not acted on — the
    # controller then holds AUTO exactly as it does for any invalid plan.
    accepted = False
    finding = "failed validation"
    try:
        val = validate_fn(catch.plan, now)
        accepted = val.ok
        if not accepted and val.findings:
            finding = val.findings[0].message
    except Exception:
        _log.warning("recovery plan validation failed (non-fatal); holding plan as-is",
                     exc_info=True)

    ts = now.isoformat()
    summary = (f"plan recovered: {catch.reason}" if accepted
               else f"plan recovery rejected (holding self-consumption): {finding}")
    if audit_store is not None:
        try:
            await audit_store.append(ts, "plan_recovery", summary, {
                "status": status.to_dict(), "feasible": catch.feasible,
                "target_soc": round(catch.target_soc, 1), "kwh_short": catch.kwh_short,
                "slots_used": catch.slots_used, "accepted": accepted, "reason": catch.reason,
            })
        except Exception:
            _log.warning("failed to write recovery audit (non-fatal)", exc_info=True)

    day = now.astimezone(tz).date().isoformat()
    if notifier is not None and accepted:
        # Calm B-37 shape (what happened + battery is safe + what EMS does / nothing to do). One
        # message on a full catch-up, one on an impossible/partial one.
        title = ("Catching up on a missed charge window" if catch.feasible
                 else "Only a partial catch-up is possible")
        try:
            await notifier.send(
                key="plan_recovery", title=title, body=catch.note,
                confidence="medium" if catch.feasible else "high",
                dedupe_key=f"recovery:{day}:{plan.deadline.isoformat()}",
            )
        except Exception:
            _log.debug("recovery notification failed (non-fatal)", exc_info=True)

    if cache_store is not None:  # mark handled today whether accepted or rejected (no churn)
        try:
            await asyncio.to_thread(cache_store.set, dedupe_key, ts, dedupe_ttl_seconds)
        except Exception:
            _log.debug("recovery dedupe write failed (non-fatal)", exc_info=True)

    return {"accepted": accepted, "feasible": catch.feasible, "status": status.status,
            "summary": summary}


# Canonical day-ahead snapshot dedupe TTL: keyed by the target DATE, so a couple of days comfortably
# outlives the one evening it guards (a new day is a new key regardless).
_CANONICAL_DEDUPE_TTL_SECONDS = 48 * 3600.0
# The learned hour-of-day load profile has no native low/high band, so canonical LOAD rows use a
# deliberately broad, honest placeholder: expected ±30% (clamped ≥ 0). A calibrated load-forecast
# band replaces this in a later batch (design §5.2); documented here so the default is explicit.
_LOAD_BAND_FRACTION = 0.30


def _canonical_ledger_rows(
    *, issued_at: str, tomorrow: date_cls, tz: ZoneInfo, solar_slots: list[Any],
    solar_source_name: str, load_profile: Any, band: float = _LOAD_BAND_FRACTION,
) -> list[tuple]:
    """Pure builder for the canonical (`canonical=1`) day-ahead ledger rows covering EVERY 15-min
    slot of the NEXT local calendar day (design §4.3).

    The slot grid is generated in UTC across the local day's TRUE span, so it is DST-correct: 96
    slots normally, 92 on the spring-forward day, 100 on the fall-back day. For each slot we emit a
    LOAD row from the learned baseline profile (`expected_w`; low/high = expected ±`band`, clamped
    ≥ 0) and, when the live solar forecast has that exact slot, a SOLAR row (p10/p50/p90). Both
    carry the true `issued_at`; quality/model_version are NULL (no scorer in this batch)."""
    day_start = datetime(tomorrow.year, tomorrow.month, tomorrow.day, tzinfo=tz)
    start_utc = day_start.astimezone(UTC)
    end_utc = (day_start + timedelta(days=1)).astimezone(UTC)  # DST-correct next local midnight
    solar_by = {s.start.astimezone(UTC): s for s in solar_slots}
    rows: list[tuple] = []
    slot = start_utc
    while slot < end_utc:
        slot_iso = slot.isoformat()
        exp = max(0.0, float(load_profile.expected_w(slot)))
        low = max(0.0, exp * (1.0 - band))
        high = exp * (1.0 + band)
        rows.append(
            (issued_at, "load", slot_iso, low, exp, high, "baseline_profile", None, None, 1))
        fc = solar_by.get(slot)
        if fc is not None:
            rows.append((issued_at, "solar", slot_iso, float(fc.p10_w), float(fc.p50_w),
                         float(fc.p90_w), solar_source_name, None, None, 1))
        slot += timedelta(minutes=15)
    return rows


# Calm B-37 shape for the "the 18:00 job never ran/never succeeded" notification: what happened +
# the battery is safe + what EMS still does automatically. Sent at most once per missed day.
_CANONICAL_MISSED_BODY = (
    "Tonight's day-ahead solar + load snapshot (18:00-20:00) didn't complete, so tomorrow has no "
    "canonical forecast to score against — nothing else is affected: your plan still runs on the "
    "live forecast as normal, and the battery is unaffected. If this keeps happening, check the "
    "System page for recent errors."
)


async def _run_canonical_forecast(
    store: HistoryStore | None,
    cache_store: CacheStore | None,
    now: datetime,
    tz: ZoneInfo,
    *,
    solar_slots: list[Any],
    solar_source_name: str,
    load_profile: Any,
    load_band: float = _LOAD_BAND_FRACTION,
    state: dict[str, Any] | None = None,
    notifier: Notifier | None = None,
) -> int | None:
    """Persist the canonical day-ahead solar + load forecast for tomorrow (design §4.3). Mirrors
    `_run_weekly_digest`'s shape — a plain, directly-testable gate + dedupe + write, NOT a closure.

    GATE: only fires when local time is in [18:00, 20:00). At/after 18:00 it writes; if that write
    FAILS it retries every cycle (the dedupe key is set only on success) until 20:00, after which
    the target day gets NO canonical forecast — it is excluded, never backfilled with hindsight.

    DEDUPE: `ledger:canonical:<tomorrow>` in `cache_store` — once tomorrow's snapshot is written it
    is skipped for the rest of the evening. `ledger_append`'s first-write-wins is a second safety
    net. Returns the row count written, or None when it didn't fire (gate closed, deduped, or no
    store). Never raises — a snapshot hiccup must not take down the notify loop.

    OBSERVABILITY (mirrors `_run_backup`'s `state` box — this job is otherwise invisible when
    dead): when `state` is given, every write attempt inside the window records `last_attempt_iso`,
    and either outcome (success, a cache-dedupe hit that proves an earlier attempt this evening
    already succeeded, or a raised exception) sets `ok` + (on success) `last_success_date` (the
    target day's ISO date). Once the window has closed (>= 20:00) with no recorded success for
    tomorrow, `ok` is set `False` and — best-effort, via `notifier` — a calm `canonical_missed`
    notification is sent, deduped per target day so a job that's been dead for a while doesn't spam
    (mirrors `_run_backup`'s `backup_failed`). Both `state` and `notifier` default to `None` so
    existing callers/tests are unaffected."""
    if store is None:
        return None
    now_local = now.astimezone(tz)
    tomorrow = now_local.date() + timedelta(days=1)
    if now_local.hour < 18:
        return None
    if now_local.hour >= 20:  # window closed — did tomorrow ever get a successful snapshot?
        if state is not None and state.get("last_success_date") != tomorrow.isoformat():
            state["ok"] = False
            if notifier is not None:
                await notifier.send(
                    "canonical_missed", "Tomorrow's forecast snapshot is missing",
                    _CANONICAL_MISSED_BODY,
                    dedupe_key=f"canonical_missed:{tomorrow.isoformat()}",
                )
        return None
    if state is not None:
        state["last_attempt_iso"] = now.isoformat()
    dedupe_key = f"ledger:canonical:{tomorrow.isoformat()}"
    if cache_store is not None:
        try:
            if await asyncio.to_thread(cache_store.get, dedupe_key) is not None:
                if state is not None:
                    state["ok"] = True
                    state["last_success_date"] = tomorrow.isoformat()
                return None
        except Exception:
            _log.debug("canonical forecast dedupe read failed (non-fatal)", exc_info=True)
    # Ledger-level idempotency (F3): the cache dedupe key can be lost even after a successful
    # ledger write (e.g. an earlier cache `.set` failed), and a naive retry would then append a
    # SECOND canonical set with a fresh `issued_at` — duplicates the scorer double-counts. So before
    # building, check whether tomorrow already has canonical solar rows; if so, treat the run as
    # already-done: (re)set the cache key so future cycles dedupe cheaply, mark state, skip writes.
    day_start = datetime(tomorrow.year, tomorrow.month, tomorrow.day, tzinfo=tz)
    tomorrow_start = day_start.astimezone(UTC).isoformat()
    tomorrow_end = (day_start + timedelta(days=1)).astimezone(UTC).isoformat()
    try:
        already = await store.ledger_canonical_between("solar", tomorrow_start, tomorrow_end)
    except Exception:
        already = []
        _log.debug("canonical forecast ledger idempotency check failed (non-fatal)", exc_info=True)
    if already:
        if cache_store is not None:
            try:
                await asyncio.to_thread(
                    cache_store.set, dedupe_key, "ledger-present", _CANONICAL_DEDUPE_TTL_SECONDS)
            except Exception:
                _log.debug("canonical forecast dedupe write failed (non-fatal)", exc_info=True)
        if state is not None:
            state["ok"] = True
            state["last_success_date"] = tomorrow.isoformat()
        _log.info("canonical forecast: tomorrow (%s) already has canonical rows — skipping write",
                  tomorrow.isoformat())
        return None
    issued_at = now.astimezone(UTC).isoformat()
    rows = _canonical_ledger_rows(
        issued_at=issued_at, tomorrow=tomorrow, tz=tz, solar_slots=solar_slots,
        solar_source_name=solar_source_name, load_profile=load_profile, band=load_band)
    try:
        await store.ledger_append(rows)
    except Exception:
        # Write failed — do NOT set the dedupe key, so the next cycle retries (until 20:00).
        if state is not None:
            state["ok"] = False
        _log.warning("canonical forecast write failed; will retry until 20:00 local (fail-safe)",
                     exc_info=True)
        return None
    if cache_store is not None:
        try:
            await asyncio.to_thread(
                cache_store.set, dedupe_key, issued_at, _CANONICAL_DEDUPE_TTL_SECONDS)
        except Exception:
            _log.warning("canonical forecast dedupe write failed (non-fatal)", exc_info=True)
    if state is not None:
        state["ok"] = True
        state["last_success_date"] = tomorrow.isoformat()
    _log.info("canonical forecast: wrote %d day-ahead rows (solar+load) for %s",
              len(rows), tomorrow.isoformat())
    return len(rows)


_recorder_died = _task_died("Recorder")


def _spawn_tracked(coro, name: str, task_set: set[asyncio.Task]) -> asyncio.Task:
    """Fire-and-forget a coroutine while keeping a STRONG ref to the task (the event loop keeps
    only a weak one — without this the task can be GC'd mid-run, so a "charge now" control cycle
    silently no-ops). Drop the ref on completion and surface a crash loudly (like the lifespan
    tasks)."""
    task = asyncio.create_task(coro)
    task_set.add(task)
    task.add_done_callback(task_set.discard)
    task.add_done_callback(_task_died(name))
    return task


def _explain_cache_key(reason: str, language: str, model: str) -> str:
    """Stable persistent-cache key for a phrased explanation. Includes model + language so changing
    either refreshes; the (possibly long) reason is hashed to a fixed-size key. No secrets enter it
    — only the deterministic reason, language and model name."""
    raw = f"{model}|{language}|{reason}".encode()
    return "explain:" + hashlib.sha256(raw).hexdigest()


# The in-memory explanation cache only coalesces concurrent/in-flight polls now that the persistent
# store handles reuse across restarts. Bound it so a long-running process can't grow it without
# limit; an evicted entry just costs one cheap persistent-cache lookup next time it's asked for.
_EXPLAIN_MEM_CACHE_MAX = 256


def _bounded_put(cache: dict, key, value, maxn: int) -> None:
    """Insert into an insertion-ordered dict, evicting oldest entries past `maxn` (simple FIFO)."""
    cache[key] = value
    while len(cache) > maxn:
        cache.pop(next(iter(cache)))


# Live meter/SoC reads are deliberately NEVER served from the persistent external cache (Tibber /
# Forecast.Solar / AI) — they must always reflect the hardware. The only caching applied to a live
# read is this short *in-memory* coalescing window, so a single dashboard refresh that fans out to
# several endpoints reads the (slow) battery cluster + meters once instead of many times. It is lost
# on restart (so the first read after a restart is always fresh) and is intentionally brief.
_LIVE_SAMPLE_COALESCE_SECONDS = 30.0

# How much recorded history to show as "actuals" leading into the next-24h plan, so the operator can
# see whether reality is following the plan ("am I on track?"). 3 hours = 12 quarter-hour slots.
RECENT_HOURS = 3

# Plan-provenance line (CLAUDE.md honesty ask): humanized forecast-source class names for
# /api/battery-plan's `provenance.forecast_source`. Keyed on `type(solar_forecast).__name__` — an
# unknown/future adapter class falls back to a plain word-split of the name (see
# _forecast_source_label) rather than leaking raw CamelCase to the UI.
_FORECAST_SOURCE_LABEL: dict[str, str] = {
    "ForecastSolarSource": "Forecast.Solar",
    "MockSolarForecastSource": "Built-in model",
}

# The scenario/ML "intelligence" layer's honest status for the plan-provenance line: ems/
# intelligence/planning.py (pessimistic/expected/optimistic scenario planning, E-08) is BUILT and
# VALIDATING against real outcomes, but it is NOT wired into live planning — it never steers a plan.
# "shadow" is the only value this can be today. The frontend's single source of truth for this
# value is THIS constant via /api/battery-plan's `provenance.intelligence` (BatteryPlan.tsx); where
# a view can't fetch that (System.tsx's static row), it mirrors the copy from
# ems/web/frontend/src/labels.ts's `INTELLIGENCE_COPY` — flip both together the day a mode actually
# starts steering a plan.
INTELLIGENCE_MODE = "shadow"

# The cluster per-tower mode LABEL→PhysicalMode map + the mode-FAMILY helpers (`_tower_family`,
# `_commanded_family`) moved to ems/control/service.py (B-46, control domain). Imported at the top
# and re-exported here so `_current_mode` and the closure-testing tests keep their import paths.

# --- Unified energy-story slot/totals (shared by the past + next windows so they never drift) ---
_INTENT_ACTION = {
    "grid_charge_to_target": "grid_charge",
    "discharge_for_load": "discharge",
    "hold_reserve": "hold",
    "allow_self_consumption": "self_consume",
}


def _charge_kind(battery_w: float, solar_w: float, load_w: float) -> str:
    """Label a CHARGING slot by its DOMINANT source — the same solar-first split the Sankey uses
    (energy_flow._allocate_slot). The grid only counts as charging the battery to the extent the
    grid covered the charge AFTER solar served the house; if more of the charge came from the roof
    than the grid, it's a SOLAR charge. This is what stops a sunny slot — battery filling from solar
    while the house draws a little grid for its own load — from being mislabelled "grid charge"."""
    charge = -battery_w
    solar_to_batt = min(charge, max(0.0, solar_w - load_w))  # solar left after the house
    grid_to_batt = charge - solar_to_batt
    return "grid_charge" if grid_to_batt > solar_to_batt else "solar_charge"


def _action_from_intent(intent: object, battery_w: float) -> str:
    action = _INTENT_ACTION.get(str(intent), "self_consume")
    # In self-consumption the battery only ever charges from solar surplus (the vendor never
    # grid-charges in this mode — that needs GRID_CHARGE_TO_TARGET), so a charging slot here is a
    # SOLAR charge. Surface it as its own block instead of the generic "use solar first".
    if action == "self_consume" and battery_w < -50.0:
        return "solar_charge"
    return action


def _action_from_battery(battery_w: float, solar_w: float, load_w: float) -> str:
    # What the battery actually did this slot (+discharge / −charge); a small dead-band = idle.
    # A charge is split by its dominant source (grid import vs solar surplus), NOT by whether the
    # grid happened to be importing for the house at the time.
    if battery_w < -50.0:
        return _charge_kind(battery_w, solar_w, load_w)
    if battery_w > 50.0:
        return "discharge"
    return "idle"


def _uslot(start, soc, grid, solar, batt, load, price, action) -> dict:
    return {
        "start": start.isoformat(),
        "soc_pct": round(soc, 1) if soc is not None else None,
        "grid_w": round(grid, 1), "solar_w": round(solar, 1), "battery_w": round(batt, 1),
        "load_w": round(load, 1), "eur_per_kwh": price, "action": action,
    }


def _uslot_totals(slots: list[dict]) -> dict:
    """Integrate the unified slots into kWh totals + cost + self-sufficiency (zero-order hold)."""
    def kwh(power_w: float) -> float:
        return power_w * 0.25 / 1000.0

    imp = sum(kwh(max(0.0, s["grid_w"])) for s in slots)
    exp = sum(kwh(max(0.0, -s["grid_w"])) for s in slots)
    load = sum(kwh(s["load_w"]) for s in slots)
    priced = [s for s in slots if s["eur_per_kwh"] is not None]
    cost = sum(
        (kwh(max(0.0, s["grid_w"])) - kwh(max(0.0, -s["grid_w"]))) * s["eur_per_kwh"]
        for s in priced
    )
    ss = min(100.0, (load - imp) / load * 100.0) if load > 0 and load >= imp else None
    cost_eur = round(cost, 2) + 0.0  # +0.0 collapses -0.0 to 0.0 (no "€-0.00")
    socs = [s["soc_pct"] for s in slots if s["soc_pct"] is not None]
    return {
        "import_kwh": round(imp, 2), "export_kwh": round(exp, 2),
        "solar_kwh": round(sum(kwh(s["solar_w"]) for s in slots), 2),
        "charge_kwh": round(sum(kwh(max(0.0, -s["battery_w"])) for s in slots), 2),
        # The charge total, split by source (grid top-up vs solar surplus) using the slot action.
        "grid_charge_kwh": round(
            sum(kwh(max(0.0, -s["battery_w"])) for s in slots if s["action"] == "grid_charge"), 2),
        "solar_charge_kwh": round(
            sum(kwh(max(0.0, -s["battery_w"])) for s in slots if s["action"] == "solar_charge"), 2),
        "discharge_kwh": round(sum(kwh(max(0.0, s["battery_w"])) for s in slots), 2),
        "load_kwh": round(load, 2),
        "grid_cost_eur": cost_eur if priced else None,
        "self_sufficiency_pct": round(ss, 1) if ss is not None else None,
        "soc_start_pct": socs[0] if socs else None,
        "soc_end_pct": socs[-1] if socs else None,
        "soc_min_pct": min(socs) if socs else None,
        "soc_max_pct": max(socs) if socs else None,
    }


# Bump when the finance math changes so completed-day rows cached under the OLD formula are
# recomputed instead of served stale (finding 4). v2 = same-window wear (dis_priced) + price-gate;
# v3 = export credited via the configurable feed-in model (B-05), not always the full spot price.
_FINANCE_CALC_VERSION = 3


# The car-charging discharge-session constants + the PURE decision helpers (`_decide_car_command`,
# `_decide_car_session_end`, `_decide_grace_action`) and the thread-safe hysteresis committer
# (`_commit_hysteresis_state`) moved to ems/control/service.py (B-46). They are imported at the top
# and re-exported here (with `_CAR_SESSION_MAX_COMMANDS`) so the car-session tests keep their
# `ems.web.api` import path.


def create_app(
    source: Source,
    *,
    dry_run: bool,
    dev_mode: str,
    tz: ZoneInfo | None = None,
    store: HistoryStore | None = None,
    freshness: FreshnessTracker | None = None,
    recorder: Recorder | None = None,
    price_source: PriceSource | None = None,
    solar_forecast: SolarForecastSource | None = None,
    battery: BatteryDriver | None = None,
    controller: ModeController | None = None,
    settings_store: SettingsStore | None = None,
    override_store: SettingsStore | None = None,
    audit_store: AuditStore | None = None,
    cache_store: CacheStore | None = None,
    control_cycle_seconds: float = 300.0,
    history_retention_days: int = 90,
    history_backup_keep: int = 7,
    web_auth_token: str | None = None,
    static_dir: str | Path | None = None,
) -> FastAPI:
    def _effective_web_token() -> str | None:
        """The access token that must be presented for writes, or None if writes are open. The
        UI-set token (settings store, web.auth_token) takes precedence over the EMS_WEB_TOKEN env
        seed — so access can be configured entirely from the UI. Read at request time so a
        just-saved token takes effect without a restart."""
        ui_tok = (settings_cache.get("web.auth_token") or "").strip()
        return ui_tok or web_auth_token

    def _authorized(request: Request) -> bool:
        """True if the request may mutate. When no token is configured, writes are open (dev/LAN
        default); otherwise an `Authorization: Bearer <token>` must match (constant-time)."""
        required = _effective_web_token()
        if required is None:
            return True
        scheme, _, token = request.headers.get("authorization", "").partition(" ")
        try:
            return scheme == "Bearer" and secrets.compare_digest(token, required)
        except TypeError:
            # compare_digest raises on non-ASCII str; treat as a clean 401, never a 500.
            return False

    def _auth_error() -> JSONResponse:
        return JSONResponse({"detail": "unauthorized — set an access token"}, status_code=401)
    # In-memory effective-settings cache (defaults until the store loads in lifespan). Sync
    # endpoints read this; POST /api/settings refreshes it. Mutated in place (never rebound).
    settings_cache: dict[str, Any] = effective_settings({})
    # The control cycle's mutable state (B-46): one typed home for the ~10 `*_box` dicts + locks the
    # brain owns. Local aliases below keep the read/settings/strategy closures using their original
    # names — the SAME shared objects the ControlService (constructed later) mutates in place, the
    # `settings_cache` convention. Boxes touched ONLY by the control cycle (car session, held dedup,
    # car-obs, control lock) live purely in `ctx` now; those still read by the endpoints / strategy
    # closures are aliased here. B-46 stage 2 migrates the remaining closures to read ctx.*.
    ctx = ControlContext()
    override_box = ctx.override_box
    _OV_INTENT, _OV_EXP = "override.intent", "override.expires_at"
    # Kept here only for the lifespan's boot-time seed of the hysteresis state (§8.4 / B-15); the
    # read-modify-write + persist now lives in ControlService.commit_hysteresis (B-46 stage 2).
    _hysteresis_box = ctx.hysteresis_box
    _drift_box = ctx.drift_box  # cluster-drift dedup — also read by /api/diagnostics
    _override_tasks = ctx.override_tasks  # strong refs to override-triggered control cycles
    # Sky cloud-cover cache: Open-Meteo is polled at most every 15 min (best-effort) for the sky.
    _sky_box: dict[str, Any] = {"cc": None, "at": None}
    # Last scheduled-backup outcome (SPEC §11 durability), surfaced in /api/diagnostics so a
    # silently-failing backup is VISIBLE. Mutated in place by _run_backup in the maintenance loop.
    _backup_state: dict[str, Any] = {
        "last_backup_ts": None, "last_backup_ok": None,
        "last_backup_size": None, "backups_kept": 0,
    }
    # Last scheduled-backup outcome (SPEC §11 durability), surfaced in /api/diagnostics so a
    # silently-failing backup is VISIBLE. Mutated in place by _run_backup in the maintenance loop.
    _backup_state: dict[str, Any] = {
        "last_backup_ts": None, "last_backup_ok": None,
        "last_backup_size": None, "backups_kept": 0,
    }
    # Last 18:00 canonical-forecast job outcome (design §4.3), surfaced in /api/diagnostics
    # alongside the backup state — the job is otherwise invisible when dead (a gap only shows up
    # much later, in forecasts.csv or the accuracy surfaces). Mutated in place by
    # _run_canonical_forecast; `ok=False` covers both a failed write and a day that closed (past
    # 20:00) without ever succeeding.
    _canonical_forecast_state: dict[str, Any] = {
        "last_success_date": None, "last_attempt_iso": None, "ok": None,
    }
    # Store-health escalation (B-49): in-memory per-day dedupe for the `store_unhealthy` alert. Kept
    # in memory (not the store's dedupe) so it stays robust even when the store is the dead thing.
    _store_unhealthy_state: dict[str, Any] = {"alerted_date": None}
    # Notification outbox (B-20): built from the SAME history store + the live settings cache, so
    # a just-saved ntfy url/topic applies to the very next send without a restart. None when no
    # store is configured (e.g. some unit tests) — _run_backup treats a None notifier as a no-op.
    notifier = Notifier(store, settings_cache) if store is not None else None

    def _apply_control_settings() -> None:
        """Push the control.* settings onto the live controller (preserves its switch counters)."""
        if controller is None:
            return
        controller.max_switches_per_day = settings_cache["control.max_switches_per_day"]
        # Switches reserved for a committed grid-charge so routine flapping can't starve it (07-12
        # guardrail-starvation incident); mirrors max_switches_per_day's live-push convention.
        controller.commitment_reserve = settings_cache["control.commitment_reserve"]
        controller.min_dwell = timedelta(seconds=settings_cache["control.min_dwell_seconds"])
        controller.allow_export_discharge = settings_cache["control.allow_export_discharge"]

    def _apply_site_settings() -> None:
        """Push the site.* array settings onto the solar forecast source so the forecast responds
        live. Gated on an explicit opt-in marker so we never clobber a real adapter that happens
        to expose kwp/tilt/azimuth for its own purpose (it sets _ems_site_configurable=False)."""
        if solar_forecast is None or not getattr(
            solar_forecast, "_ems_site_configurable", False
        ):
            return
        for attr in ("kwp", "tilt", "azimuth"):
            setattr(solar_forecast, attr, settings_cache[f"site.{attr}"])

    # The AI explainer. OFF by default → TemplateExplainer (returns the deterministic reason
    # verbatim, never fails). Rebuilt in place from settings on save. `cache` memoises the phrasing
    # per (reason, language) so a 5 s dashboard poll never re-hits the LLM — only a CHANGED reason
    # does, keeping calls to a handful a day (and cost to cents).
    explainer_box: dict[str, Any] = {"ex": TemplateExplainer(), "cache": {}}
    # Latest AI second-opinion (advisory review of the plan), surfaced read-only in the UI.
    validation_box: dict[str, Any] = {"latest": None}

    def _apply_explainer_settings() -> None:
        """(Re)build the explainer from the settings cache. external_llm needs a key; otherwise we
        stay on the offline template. Privacy/fail-safe live in ExternalLlmExplainer itself."""
        s = settings_cache
        explainer_box["cache"] = {}  # settings changed → drop memoised phrasings
        if s.get("explainer.mode") == "external_llm" and s.get("explainer.api_key"):
            chat_post = make_openai_chat_post(
                s["explainer.base_url"], s["explainer.api_key"],
                timeout=float(s["explainer.timeout_seconds"]),
            )
            explainer_box["ex"] = ExternalLlmExplainer(
                chat_post, model=s["explainer.model"], language=s["explainer.language"],
                max_tokens=int(s["explainer.max_tokens"]),
            )
        else:
            explainer_box["ex"] = TemplateExplainer()

    def _explainer_active() -> bool:
        return isinstance(explainer_box["ex"], ExternalLlmExplainer)

    async def _explain(reason: str, facts: dict) -> dict:
        """Phrase a deterministic reason via the active explainer. Two cache layers, so an identical
        decision is explained at most once: an in-memory Task cache per (reason, language) coalesces
        concurrent polls into ONE in-flight call, and a persistent SQLite cache (keyed by
        model|language|reason) means a restart doesn't re-spend tokens re-explaining the same
        decision. Only real LLM answers are persisted — template/error fallbacks are not, so AI
        retries next time. Always falls back to the verbatim reason."""
        if not reason or not _explainer_active():
            return {"text": reason, "source": "template"}
        lang = settings_cache.get("explainer.language", "English")
        key = (reason, lang)
        cache = explainer_box["cache"]
        if key not in cache:
            async def _run() -> dict:
                ckey = _explain_cache_key(reason, lang, settings_cache.get("explainer.model", ""))
                if cache_store is not None:
                    try:
                        hit = await asyncio.to_thread(cache_store.get, ckey)
                    except Exception:
                        _log.debug("explanation cache read failed (non-fatal)", exc_info=True)
                        hit = None
                    if hit:
                        try:
                            return json.loads(hit)
                        except (ValueError, TypeError):
                            _log.debug("cached explanation unreadable (non-fatal)", exc_info=True)
                            pass  # corrupt entry → fall through and regenerate
                try:
                    expl = await asyncio.to_thread(explainer_box["ex"].explain, reason, facts)
                    out = {"text": expl.text, "source": expl.source}
                except Exception:
                    _log.debug("explainer failed; using template (non-fatal)", exc_info=True)
                    return {"text": reason, "source": "template"}
                if cache_store is not None and out["source"] == "external_llm":
                    ttl = float(settings_cache.get("explainer.cache_hours", 168.0)) * 3600.0
                    if ttl > 0:
                        try:
                            await asyncio.to_thread(
                                cache_store.set, ckey, json.dumps(out), ttl
                            )
                        except Exception:
                            _log.debug("explanation cache write failed (non-fatal)", exc_info=True)
                            pass  # cache write is best-effort; never fail the request over it
                return out
            _bounded_put(cache, key, asyncio.ensure_future(_run()), _EXPLAIN_MEM_CACHE_MAX)
        return await cache[key]

    site_tz = tz or ZoneInfo("UTC")
    # Last good battery CapabilityReport (probed off the hot path — at startup + opportunistically),
    # so the §8.11 validator can check requested power vs the battery's rating without a networked
    # probe on every decision. None until first probed (the validator simply skips that warn-check).
    _capability_box: dict[str, Any] = {"cap": None}

    async def _apply_battery_power_settings() -> None:
        """Keep the live driver's advertised capability aligned with battery.* power settings."""
        if controller is None:
            return
        configure = getattr(controller.driver, "configure_power_limits", None)
        if configure is not None:
            configure(
                max_charge_w=settings_cache["battery.max_charge_w"],
                max_discharge_w=settings_cache["battery.max_discharge_w"],
            )
        try:
            _capability_box["cap"] = await asyncio.to_thread(controller.driver.probe)
        except Exception:
            _log.debug("battery capability probe failed (non-fatal)", exc_info=True)
            _capability_box["cap"] = None

    # B-46 stage 2: the coalesced live reads (_current_sample/_soc/_towers/_mode/_battery_reachable/
    # _car_charging), the config builders (_planner_cfg[_from]/_night_target_soc/_summer_cfg/
    # _adaptive_cfg/_load_by) and the strategy resolution (_strategy_inputs/_commit_hysteresis/
    # _resolve_strategy/_active_strategy) — plus the car-charging guard — moved INTO ControlService
    # as methods (their primary caller is the control cycle). They are aliased back to these names
    # just after the service is constructed, so every endpoint keeps calling them unchanged.

    # Cached expected-load profile (learned async in _forward_projection) so the sync plan path can
    # feed the adaptive charger without its own DB read. None until the first projection runs. Lives
    # on `ctx` (B-46) — shared with the ControlService plan path + car-load prediction; also written
    # by the projection endpoint below.
    _load_profile_box = ctx.load_profile_box

    async def _audit_decision_loop(stop: asyncio.Event) -> None:
        """Record a plan/mode decision whenever it CHANGES (deduped) — a faithful, compact history
        in ANY mode. Advisory + off the control path: it only reads/previews, never writes the
        battery. Seeds the last mode from the latest audit entry so a restart never double-logs."""
        last_mode = await audit_store.last_decision_mode()
        while True:
            try:
                await asyncio.wait_for(stop.wait(), timeout=control_cycle_seconds)
                return
            except TimeoutError:
                pass
            try:
                now = datetime.now(UTC)
                intent, reason, override_active, tgt, pw, _val, car_action = (
                    await asyncio.to_thread(_effective_intent, now))
                if intent is None:
                    continue
                car_session = car_action is not None and car_action.action == "discharge"
                d = await asyncio.to_thread(
                    controller.preview, intent, now, target_soc=tgt, power_w=pw,
                    car_session=car_session,
                )
                mode = str(d.desired_mode)
                if mode == last_mode:
                    continue
                last_mode = mode
                # HONESTY: this logs the EMS's DECISION (a read-only preview), not a confirmed
                # device write — "Commanding", never "Set". Whether the battery actually obeyed is
                # shown live per-tower on the dashboard battery card (and a non-following tower is
                # the bug to look for on a cluster).
                verb = "Would set" if dry_run else "Commanding"
                await audit_store.append(
                    now.isoformat(), "battery_decision",
                    f"{verb} battery → {mode} — {reason}",
                    {"intent": str(intent), "desired_mode": mode, "reason": reason,
                     "override": override_active, "decided_only": True, "dry_run": dry_run},
                )
            except Exception:
                _log.exception("decision audit failed; will retry next cycle (fail-safe)")

    async def _ai_validation_loop(stop: asyncio.Event) -> None:
        """Scheduled, advisory AI review of the plan. Interval = explainer.validate_hours (re-read
        each cycle so it's live-tunable; 0 = off → idle-poll every 6 h). Off the control path."""
        while True:
            hours = float(settings_cache.get("explainer.validate_hours", 0) or 0)
            timeout = hours * 3600 if hours > 0 else 6 * 3600
            try:
                await asyncio.wait_for(stop.wait(), timeout=timeout)
                return
            except TimeoutError:
                pass
            # Housekeeping for a 24/7 process that rarely restarts: drop expired cache rows so the
            # table can't accumulate stale explanation/price/forecast snapshots indefinitely.
            if cache_store is not None:
                try:
                    await asyncio.to_thread(cache_store.purge_expired)
                except Exception:
                    _log.debug("external cache purge failed (non-fatal)", exc_info=True)
            if float(settings_cache.get("explainer.validate_hours", 0) or 0) > 0:
                await _run_validation()  # already guarded + never raises

    async def _maintenance_loop(stop: asyncio.Event) -> None:
        """Daily history maintenance for a 24/7 install: purge rows past the retention window,
        truncate the WAL / reclaim freed space, and take a rotated online DB backup (SPEC §11).
        Runs once at boot, then every 24 h. Best-effort — a busy DB just retries tomorrow.
        retention_days <= 0 keeps everything (purge skipped); backup_keep <= 0 disables backups."""
        first = True
        while True:
            if not first:
                try:
                    await asyncio.wait_for(stop.wait(), timeout=24 * 3600)
                    return
                except TimeoutError:
                    pass
            first = False
            try:
                if history_retention_days > 0:
                    cutoff = (datetime.now(UTC)
                              - timedelta(days=history_retention_days)).isoformat()
                    deleted = await store.purge_older_than(cutoff)
                    if deleted:
                        _log.info("history retention: purged %d rows older than %d days",
                                  deleted, history_retention_days)
                await store.maintain()
                # Compact long-horizon stores (design §4.1 / B-13): materialize YESTERDAY (local)
                # into the 15-min observation store + the daily kWh rollup — idempotent upserts, so
                # a re-run (restart, retry) just overwrites. Observations then purge at their OWN
                # 400-day horizon, INDEPENDENTLY of raw retention_days; daily_energy is never purged
                # (that is the point — year-over-year kWh survives the raw purge).
                now_local = datetime.now(UTC).astimezone(site_tz)
                y = now_local.date() - timedelta(days=1)
                day_start = datetime(y.year, y.month, y.day, tzinfo=site_tz)
                day_end = day_start + timedelta(days=1)
                cadence = _sample_cadence_seconds()
                await materialize_observations(store, day_start, day_end, cadence_seconds=cadence)
                await materialize_daily_energy(store, day_start, day_end, cadence_seconds=cadence)
                # Finance year-completeness (F5): also cache YESTERDAY's finance rollup now,
                # mirroring the daily_energy materialization above. daily_finance was previously
                # written ONLY on a view/export, so a day nobody looked at before the 90-day raw
                # purge became permanently uncomputable — a silent year-view undercount. This makes
                # every completed day's finance survive the raw purge. Idempotent: the day's cached
                # row (under the current calc_v) is returned unchanged, never re-upserted.
                await _ensure_day_finance(y)
                obs_cutoff = (datetime.now(UTC)
                              - timedelta(days=OBSERVATION_RETENTION_DAYS)).isoformat()
                purged = await store.purge_observations_older_than(obs_cutoff)
                if purged:
                    _log.info("observation retention: purged %d rows older than %d days",
                              purged, OBSERVATION_RETENTION_DAYS)
                # Prediction ledger shares the 400-day horizon (purged by target_start, symmetric
                # with observations — a forecast is only scorable against an actual we still keep).
                nowcast_cutoff = (datetime.now(UTC) - timedelta(days=60)).isoformat()
                purged_ledger = await store.purge_ledger_older_than(
                    obs_cutoff, nowcast_cutoff_iso=nowcast_cutoff)
                if purged_ledger:
                    _log.info("forecast ledger retention: purged %d rows older than %d days",
                              purged_ledger, OBSERVATION_RETENTION_DAYS)
            except Exception as exc:
                _log.warning("history maintenance failed (%s: %s); retrying next cycle",
                             type(exc).__name__, exc)
            # Backup is its OWN best-effort step (never raises): a failed backup must not skip the
            # retention/WAL work above, nor vice-versa. Runs after maintain() so it snapshots the
            # freshly-checkpointed DB.
            await _run_backup(store, store.db_path, history_backup_keep, _backup_state, notifier)

    async def _shutdown_restore() -> None:
        """Graceful-shutdown safety (SPEC §6.5 / runbook): in operational mode, hand the battery
        back to its safe vendor mode before exiting, so an upgrade/reboot/launchd restart can't
        leave it in a forced charge/hold/discharge. Bounded + best-effort — a slow or offline
        device can never hang shutdown — and the outcome is audited."""
        if dry_run or controller is None or not getattr(controller.driver, "armed", False):
            return
        # Nothing to undo unless EMS actually commanded a non-AUTO mode this run.
        last = controller.last_confirmed_action
        if last is None or last is PhysicalMode.AUTO:
            return
        # Prefer the mode the battery had before EMS took control, but NEVER restore into a forced
        # energy flow — fall back to AUTO (vendor self-consumption / P1-zeroing), the safe default.
        target = controller.original_vendor_mode or PhysicalMode.AUTO
        if target in (PhysicalMode.CHARGE, PhysicalMode.DISCHARGE):
            target = PhysicalMode.AUTO
        ok = False
        try:
            ok = await asyncio.wait_for(
                asyncio.to_thread(controller.driver.apply, target), timeout=8.0
            )
        except Exception as exc:
            _log.warning("shutdown restore failed (%s: %s)", type(exc).__name__, exc)
        if audit_store is not None:
            try:
                await audit_store.append(
                    datetime.now(UTC).isoformat(), "shutdown_restore",
                    f"Graceful shutdown — restored battery to {target.value} "
                    f"({'confirmed' if ok else 'UNCONFIRMED — verify the device'})",
                    {"target": target.value, "confirmed": ok},
                )
            except Exception:
                _log.warning("shutdown-restore audit append failed (non-fatal)", exc_info=True)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        # Guarantee the schema exists before anything touches the DB (no caller footgun).
        if store is not None:
            await store.init()
        if settings_store is not None:
            await settings_store.init()
            # In-place update ONLY (never clear()+update): effective_settings always returns the
            # FULL keyset (defaults ∪ valid overrides), so no key ever disappears and a threadpool
            # GET can never observe a missing key. clear()+update() briefly exposed an empty dict
            # between the two statements → KeyError/500 for a concurrent reader.
            settings_cache.update(effective_settings(await settings_store.all()))
            _apply_control_settings()
            _apply_site_settings()
            _apply_explainer_settings()
            await _apply_battery_power_settings()
        if override_store is not None:
            await override_store.init()
            stored = await override_store.all()
            override_box["ov"] = override_from_stored(
                stored.get(_OV_INTENT), stored.get(_OV_EXP)
            )
        if audit_store is not None:
            await audit_store.init()
        if cache_store is not None:
            await asyncio.to_thread(cache_store.init)
            # One-off housekeeping at boot so the cache table can't grow without bound.
            await asyncio.to_thread(cache_store.purge_expired)
            # Restore the seasonal-transition hysteresis counter (§8.4 / B-15) so a restart doesn't
            # reset a pending switch. Absent/expired ⇒ a fresh state = today's instantaneous pick.
            try:
                raw = await asyncio.to_thread(cache_store.get, HYSTERESIS_KEY)
                _hysteresis_box["state"] = HysteresisState.from_json(raw)
            except Exception:
                _log.debug("hysteresis state restore failed (non-fatal)", exc_info=True)
        # If no settings store exists, still probe once so the §8.11 validator can sanity-check
        # requested power without a networked probe per decision. Normal startup already probes via
        # _apply_battery_power_settings(), after applying battery.* power settings to the driver.
        if controller is not None and _capability_box["cap"] is None:
            try:
                _capability_box["cap"] = await asyncio.to_thread(controller.driver.probe)
            except Exception:
                _log.debug("battery capability probe at startup failed (non-fatal)", exc_info=True)
                pass  # unreachable battery → leave None; the validator just skips that warn-check
        # Start the read-only sense loop / recorder (SPEC §5.3). Take one awaited startup
        # sample so /api/series and /api/freshness are populated deterministically, then
        # run the periodic loop in the background.
        stop = asyncio.Event()
        task = None
        if recorder is not None:
            try:
                await recorder.record_now()
            except Exception:
                _log.warning("initial recorder sample failed (non-fatal)", exc_info=True)
                pass  # fail-safe: a bad first read must not block startup
            task = asyncio.create_task(recorder.run(stop))
            task.add_done_callback(_recorder_died)
        # The control loop (battery writes) runs ONLY in operational mode (not dry_run). In dry-run
        # it is never started, so the dashboard previews but the battery is never touched.
        control_task = None
        if not dry_run and controller is not None:
            # Operational: applies AND audits the CONFIRMED mode change each cycle.
            control_task = asyncio.create_task(_control_loop(stop))
            control_task.add_done_callback(_task_died("Control loop"))
        # Advisory decision audit — DRY-RUN ONLY (logs "would set" intent changes). In operational
        # mode the control loop above audits the real confirmed outcome instead, so this would only
        # duplicate/contradict it.
        audit_task = None
        if audit_store is not None and controller is not None and dry_run:
            audit_task = asyncio.create_task(_audit_decision_loop(stop))
            audit_task.add_done_callback(_task_died("Decision audit"))
        # Scheduled AI second-opinion (advisory, off control path; no-op until AI is on).
        validate_task = asyncio.create_task(_ai_validation_loop(stop))
        validate_task.add_done_callback(_task_died("AI validation"))
        # Daily history retention + DB maintenance (bounded storage for a 24/7 install).
        maintenance_task = None
        if store is not None:
            maintenance_task = asyncio.create_task(_maintenance_loop(stop))
            maintenance_task.add_done_callback(_task_died("History maintenance"))
        # Forecast-driven notifications (B-75): its OWN loop, independent of dry_run/controller —
        # see _run_detector_cycle's docstring for why it doesn't piggyback on the control loop.
        notify_task = None
        if store is not None and notifier is not None:
            notify_task = asyncio.create_task(_notify_loop(stop))
            notify_task.add_done_callback(_task_died("Forecast notifications"))
        try:
            yield
        finally:
            stop.set()
            # In operational mode, hand the battery back to its safe vendor mode before we go — a
            # graceful stop (upgrade, reboot, launchd restart) must not leave it in a forced
            # charge/hold/discharge. Bounded + best-effort: never block shutdown on the device.
            await _shutdown_restore()
            for t in (task, control_task, audit_task, validate_task, maintenance_task, notify_task):
                if t is not None:
                    await t
            # Close each store's shared long-lived connection (perf: B-49) now that every
            # background task has stopped touching it — a clean shutdown, not a leaked handle.
            for s in (store, settings_store, override_store, audit_store):
                if s is not None:
                    await s.close()

    app = FastAPI(title="Smart Energy Manager", version="0.0.1", lifespan=lifespan)

    # --- Access control (SPEC §12) --------------------------------------------------------------
    # One choke point for the whole JSON API (finding 1) instead of a guard sprinkled on each write.
    # Writes are ALWAYS gated when a token is configured. Reads are open on the LAN by default so
    # the dashboard degrades to read-only during an HA outage; set `web.require_auth` to gate reads
    # too — do that before reaching the app over a VPN / from outside the home network.
    _WRITE_API_PATHS = frozenset({
        "/api/override", "/api/settings", "/api/ai/validate", "/api/chat", "/api/car/soc",
        "/api/notifications/read",
    })
    # POST /api/whatif (B-73 scenario simulator, ems/web/routes/whatif.py) is DELIBERATELY not in
    # this set: it only opens `replay_range`'s read-only (mode=ro) history DB connection and never
    # touches `settings_store` — there is nothing to protect, so gating it like a write would
    # misrepresent what it does. It stays reachable exactly like any other read.
    # The mutating-method routes that are verified read-only and so are NOT token-gated. Every
    # POST/PUT/DELETE route MUST be in `_WRITE_API_PATHS` or here (the write-gating invariant test
    # `test_every_mutating_route_is_write_gated_or_explicitly_exempt` fails loudly otherwise) —
    # so a new mutating route can never ship un-triaged past the auth choke point.
    WRITE_EXEMPT_PATHS = frozenset({
        # Scenario simulator: opens the read-only history connection only, never `settings_store`.
        "/api/whatif",
        # Plan preview: recomputes a plan from the posted knobs in memory; persists nothing.
        "/api/plan-preview",
    })
    # Exposed for the write-gating invariant test (S2) so it checks the LIVE classification, not a
    # duplicated copy that could drift from what the middleware actually enforces.
    app.state.write_api_paths = _WRITE_API_PATHS
    app.state.write_exempt_paths = WRITE_EXEMPT_PATHS
    # Always reachable without a token: auth discovery, so a client can learn a token is required
    # and prompt for it. (Health probes live under /health and are never gated here.)
    _AUTH_EXEMPT_API_PATHS = frozenset({"/api/auth"})

    def _read_auth_required() -> bool:
        """Whether reads (not just writes) require the token. UI-editable; read live."""
        return bool(settings_cache.get("web.require_auth", False))

    def _sample_cadence_seconds() -> float:
        """The recorder's write cadence — one history row per this many seconds. Used to size
        report/finance row caps to the ACTUAL sampling frequency (finding 10)."""
        return float(recorder.cycle_seconds) if recorder is not None else 300.0

    async def _solar_forecast_skill(now: datetime) -> dict | None:
        """14-day solar forecast skill (B-72 `forecast_error`) — the exact evidence window
        /api/accuracy's 'solar' track already gathers. Factored out so /api/battery-plan's plan
        confidence score (B-68) reuses this ONE extra store read instead of recomputing accuracy
        from scratch. None only when there's no store at all (forecast_error itself always returns
        a dict, even with zero matched slots).

        Reads the prediction ledger's CANONICAL solar rows (design §4.2/§4.3) — the single scoring
        source every solar-accuracy surface shares (System page, this endpoint's callers, the
        solar-confidence advisor, the export package); a same-day nowcast is never scored, only
        the 18:00 day-ahead snapshot."""
        if store is None:
            return None
        start = now - timedelta(days=14)
        limit = history_row_cap((now - start).total_seconds(), _sample_cadence_seconds())
        raw = await store.raw_between(start.isoformat(), now.isoformat(), limit=limit)
        forecasts = await store.ledger_canonical_between(
            "solar", start.isoformat(), now.isoformat())
        return forecast_error(forecasts, raw)

    _WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

    def _cross_origin_write(request: Request) -> bool:
        """True when a state-changing /api request carries an Origin whose host does NOT match the
        request's Host — the cross-site write vector the SPEC §12 'CSRF-checked' claim relies on.
        Browsers ALWAYS attach Origin to a cross-origin (and, in modern browsers, same-origin)
        state-changing request, so a present-but-mismatched Origin is a genuine cross-site write;
        an absent Origin (curl, server-to-server) is trusted and passes, exactly as before.
        Compares only the host[:port] netloc; no proxy/forwarded headers are trusted."""
        origin = request.headers.get("origin")
        if not origin:
            return False
        # "scheme://host[:port]" -> "host[:port]"; "null"/opaque/bad -> "" (never same-origin).
        netloc = origin.split("://", 1)[-1].split("/", 1)[0] if "://" in origin else ""
        host = request.headers.get("host", "")
        return netloc.strip().lower() != host.strip().lower()

    def _cross_origin_error() -> JSONResponse:
        return JSONResponse(
            {"detail": "cross-origin writes are not allowed"}, status_code=403)

    class _AccessMiddleware:
        """One choke point for the whole JSON API (finding 1) — reuses the same `_authorized`
        check the writes always used. Deliberately a PURE-ASGI middleware, not
        `@app.middleware("http")`/`BaseHTTPMiddleware`: the latter wraps each request in an anyio
        task group, which starves the override endpoint's `asyncio.create_task` control cycle.
        The SPA shell + static assets stay open (so the browser can load its Access box); every
        datum it renders comes from a gated /api/* read. No proxy/forwarded headers are trusted —
        auth is the bearer token only (remote access is the LAN over a VPN); see
        docs/remote-access.md.

        Two independent gates, origin FIRST: (1) same-origin enforcement rejects every cross-site
        state-changing /api write (SPEC §12 CSRF) REGARDLESS of token config — defense in depth, so
        a browser-planted valid token can't be replayed cross-site; (2) the bearer-token gate for
        writes (always) and reads (when `web.require_auth`)."""

        def __init__(self, app):
            self.app = app

        async def __call__(self, scope, receive, send):
            if scope["type"] == "http":
                path = scope.get("path", "")
                is_write_method = scope.get("method", "GET").upper() in _WRITE_METHODS
                # (1) Same-origin gate — checked BEFORE the token so a cross-site request with a
                # planted token is still rejected. Covers EVERY /api write (incl. the review's
                # /api/ai/validate + /api/chat) via this one path; no per-endpoint code.
                if (
                    is_write_method
                    and path.startswith("/api/")
                    and _cross_origin_write(Request(scope))
                ):
                    await _cross_origin_error()(scope, receive, send)
                    return
                # (2) Token gate — unchanged.
                if (
                    path.startswith("/api/")
                    and path not in _AUTH_EXEMPT_API_PATHS
                    and _effective_web_token() is not None
                ):
                    is_write = is_write_method and path in _WRITE_API_PATHS
                    if (is_write or _read_auth_required()) and not _authorized(Request(scope)):
                        await _auth_error()(scope, receive, send)
                        return
            await self.app(scope, receive, send)

    app.add_middleware(_AccessMiddleware)

    # The recovery-integrated plan path (`_recovery_sizing`/`_build_plan_now`/`_plan_with_recovery`/
    # `_current_plan`) + the car-guard + the effective-intent + the control tick/cycle moved into
    # ControlService (B-46). It is constructed just below `_validate_plan_obj` (its last injected
    # dependency); the aliases created there keep every endpoint + closure calling the same names.

    def _data_quality(now: datetime) -> str:
        """Single source of the current data-quality level (SPEC §8.11)."""
        snap = freshness.snapshot(now) if freshness is not None else {}
        return data_quality(
            snap, prices_ok=price_source is not None, forecast_ok=solar_forecast is not None
        )

    def _freshness_ok(now: datetime) -> bool:
        """Whether EVERY currently-tracked signal is fresh — the same freshness snapshot as
        `_data_quality` (SPEC §4.7), reused rather than re-read. Deliberately STRICTER than the
        data-quality badge for the plan confidence score (B-68): `data_quality` also reads
        'degraded' purely from a missing forecast SOURCE (nothing actually stale), which this
        keeps True for; any signal that's actually stale/missing makes this False."""
        snap = freshness.snapshot(now) if freshness is not None else {}
        return all(state == "fresh" for state in snap.values()) if snap else True

    def _projection_sync(plan, now: datetime):
        """A synchronous forward SoC projection for `plan`, for the validator gate. Reuses the
        cached load profile (_load_by) + cached solar forecast and the pure project_energy — no
        awaits — so the §8.11 projected-SoC checks (e.g. 'drains below reserve') actually run in
        the live gate. Returns the ProjectedSlot list, or None if there's not enough to project."""
        if solar_forecast is None or not plan.slots:
            return None
        try:
            soc = _current_soc(now)
            solar_by = {f.start: f.p50_w for f in solar_forecast.slots()}
            load_by = _load_by([s.start for s in plan.slots])
            # Both seasons now use the adaptive charger, which sizes its own charge slots — don't
            # re-cap the projection at the night target (that would undo demand-aware sizing).
            return project_energy(
                plan.slots, start_soc_pct=soc, solar_w_by=solar_by, load_w_by=load_by,
                model=_battery_model(), charge_target_soc_pct=None,
            )
        except Exception:
            _log.debug("plan projection failed; skipping SoC checks (non-fatal)", exc_info=True)
            return None  # projection is best-effort; its absence just skips those checks

    def _validate_plan_obj(plan, now: datetime) -> PlanValidation:
        """Run the §8.11 hard validator over a given plan (pure besides the cached SoC, capability
        and projection). `unsafe` ⇒ the controller must hold AUTO."""
        return validate_plan(
            plan, soc_pct=_current_soc(now), data_quality=_data_quality(now),
            min_reserve_soc=settings_cache["battery.min_reserve_soc"],
            max_switches_per_day=int(settings_cache["control.max_switches_per_day"]),
            min_dwell=timedelta(seconds=settings_cache["control.min_dwell_seconds"]),
            capability=_capability_box["cap"], projection=_projection_sync(plan, now),
            validate_projection=bool(settings_cache["planner.validate_projection"]),
        )

    # --- The control brain (B-46): the plan-to-act path, intent resolution, car-session lifecycle
    # and the single per-cycle write. Stage 2 also folds the coalesced live reads, config builders
    # and strategy resolution IN as service methods (source + cache_store injected for them); only
    # `data_quality`/`validate_plan_obj` stay as api.py closures (freshness/capability-bound,
    # web-facing) and are still injected. `settings` is THE live shared dict (never copied — the
    # settings_cache convention). The aliases below expose the service methods under their original
    # names so every endpoint + closure keeps calling `_current_soc` / `_active_strategy` / … .
    control = ControlService(
        ctx=ctx, settings=settings_cache, controller=controller, store=store,
        audit_store=audit_store, price_source=price_source, solar_forecast=solar_forecast,
        site_tz=site_tz, dry_run=dry_run, control_cycle_seconds=control_cycle_seconds,
        source=source, cache_store=cache_store,
        data_quality=_data_quality, validate_plan_obj=_validate_plan_obj,
    )
    _effective_intent = control.effective_intent
    _current_plan = control.current_plan
    _plan_with_recovery = control.plan_with_recovery
    _build_plan_now = control.build_plan_now
    _recovery_sizing = control.recovery_sizing
    _control_tick = control.control_tick
    _run_control_cycle = control.run_cycle
    # B-46 stage 2 aliases: the reads / config builders / strategy resolution now live on the
    # service; bind their original create_app names to the service methods so every endpoint below
    # (and the closures defined above, via late binding) keeps calling them unchanged.
    _current_sample = control.current_sample
    _current_soc = control.current_soc
    _current_towers = control.current_towers
    _current_mode = control.current_mode
    _battery_reachable = control.battery_reachable
    _car_charging = control.car_charging
    _load_by = control.load_by
    _planner_cfg_from = control.planner_cfg_from
    _night_target_soc = control.night_target_soc
    _resolve_strategy = control.resolve_strategy
    _active_strategy = control.active_strategy

    def _plan_snapshot(now: datetime) -> dict | None:
        """Plan/target history snapshot (observability-data): what the planner intended THIS
        cycle — the same strategy/plan/intent/SoC computation /api/replay exposes, condensed to
        what a reviewer needs to later compare `target_soc` against the achieved `soc_pct` in
        raw_samples. Read-only and cheap (reuses the cached plan/soc machinery). Returns None
        when there's no plan yet (mirrors replay's `pp is None` guard) — the recorder then writes
        nothing for this cycle."""
        pp = _current_plan()
        if pp is None:
            return None
        _now, _prices, plan = pp
        strat, _why = _resolve_strategy(now)
        intent, *_rest = _effective_intent(now)
        # plan_version (EPOCH identity) + floor_soc (the current slot's reserve floor) feed the
        # intent-aware follow-through scorer (plan_execution_error): without the epoch key,
        # abandoned day-ahead targets collide with later rolling plans on identical deadline
        # strings; without the floor, discharge/hold deadlines are unscorable and fall out as
        # "insufficient evidence" instead of being mis-scored as charge misses.
        # The version derives from the COMMITMENT, not the plan object: `_current_plan()` rebuilds
        # the plan every call (fresh created_at), so any object-identity version would churn per
        # 5-min cycle and shatter one commitment into a singleton epoch per recorder row. Same
        # committed (strategy, target, deadline) => same epoch, by construction.
        slot = plan.intent_at(now)
        deadline_iso = plan.deadline.isoformat() if plan.deadline else None
        return {
            "strategy": strat,
            "target_soc": plan.target_soc,
            "deadline": deadline_iso,
            "soc_pct": _current_soc(now),
            "intent": str(intent) if intent is not None else None,
            "plan_version": f"{strat}|{plan.target_soc}|{deadline_iso or ''}",
            "floor_soc": slot.floor_soc if slot is not None else None,
        }

    if recorder is not None:
        recorder.plan_provider = _plan_snapshot

    def _chat_context() -> str:
        """A compact, REDACTED snapshot for the chat to ground on — only non-identifying facts (the
        plan, prices, power/percentage figures), NEVER location, IPs, raw history, or tokens. Every
        block is defensive: building the context must never raise."""
        now = datetime.now(UTC)
        lines = [f"Now (UTC): {now:%Y-%m-%d %H:%M}", f"Strategy: {_active_strategy(now)}"]
        try:
            lines.append(f"Battery level now: {_current_soc(now):.0f}%")
        except Exception:
            _log.debug("chat context: battery level unavailable (non-fatal)", exc_info=True)
        try:
            intent, reason, override_active, _t, _p, _v, _ca = _effective_intent(now)
            if intent is not None:
                lines.append(
                    f"Current decision: {intent} — {reason}"
                    + (" (manual override active)" if override_active else "")
                )
        except Exception:
            _log.debug("chat context: current decision unavailable (non-fatal)", exc_info=True)
        pp = _current_plan()
        if pp is not None:
            _now, prices, plan = pp
            try:
                fc = solar_forecast.slots() if solar_forecast is not None else None
                lines.append(f"Plan: {build_plan_detail(_now, prices, plan, fc)['summary']}")
            except Exception:
                _log.debug("chat context: plan summary unavailable (non-fatal)", exc_info=True)
            try:
                by = {p.start: p.eur_per_kwh for p in prices}
                lines.append(
                    f"Estimated savings today vs no smart control: "
                    f"€{estimate_daily_savings_eur(plan, by):.2f}"
                )
            except Exception:
                _log.debug("chat context: savings estimate unavailable (non-fatal)", exc_info=True)
            future = [p for p in prices if p.start >= _now]
            if future:
                lines.append(
                    f"Cheapest price ahead €{min(p.eur_per_kwh for p in future):.2f}/kWh, "
                    f"priciest €{max(p.eur_per_kwh for p in future):.2f}/kWh"
                )
        try:
            need = compute_charge_need(
                soc_pct=_current_soc(now), usable_kwh=settings_cache["battery.usable_kwh"],
                min_reserve_soc=settings_cache["battery.min_reserve_soc"],
                night_reserve_kwh=settings_cache["battery.night_reserve_kwh"],
                overnight_load_kwh=settings_cache["battery.overnight_load_kwh"],
                round_trip_efficiency=settings_cache["planner.round_trip_efficiency"],
            )
            lines.append(
                f"Tonight's target level: {need.target_soc_pct:.0f}%; "
                f"reserve floor: {settings_cache['battery.min_reserve_soc']:.0f}%"
            )
        except Exception:
            _log.debug("chat context: night target unavailable (non-fatal)", exc_info=True)
        return "\n".join(lines)

    async def _run_validation() -> dict | None:
        """Run one advisory AI review of the current plan (off the control path). Stores the latest
        for the UI and logs it to the audit trail. Returns the result, or None when AI is off.
        Never raises."""
        if not _explainer_active():
            return None
        try:
            out = await asyncio.to_thread(explainer_box["ex"].validate, _chat_context())
        except Exception:
            _log.exception("AI validation call failed")
            return None
        if out.source != "external_llm":
            return None  # guard/error → don't store advisory noise
        ts = datetime.now(UTC).isoformat()
        validation_box["latest"] = {"text": out.text, "ts": ts, "source": out.source}
        if audit_store is not None:
            await audit_store.append(
                ts, "ai_validation", f"AI second opinion: {out.text}", {"text": out.text},
            )
        return validation_box["latest"]

    # `_cluster_drift_record`, the car-session lifecycle (`_car_session_reset` /
    # `_car_session_end_if_active` / `_car_session_command`), `_control_tick`, `_refresh_car_obs`
    # and `_run_control_cycle` moved into ControlService (B-46). `_control_loop` below is the thin
    # periodic driver (kept here — it owns the lifespan `stop` event + cadence) and delegates to
    # `control.run_cycle()` via the `_run_control_cycle` alias set above.
    async def _control_loop(stop: asyncio.Event) -> None:
        """Operational control loop (SPEC §5.3 act): each cycle apply the intent + audit the
        confirmed result. Dry-run uses the advisory _audit_decision_loop instead. Fail-safe — a tick
        error is logged, never kills the loop."""
        while True:
            try:
                await asyncio.wait_for(stop.wait(), timeout=control_cycle_seconds)
                return
            except TimeoutError:
                pass
            try:
                await _run_control_cycle()
            except Exception:
                _log.exception("control loop tick failed; retry next cycle (fail-safe)")

    async def _run_detector_cycle(now: datetime) -> None:
        """Gathers already-available PLAIN data (price slots, solar P50, the car-charging plan,
        charge-need/projection for tonight's peak, 14 days of solar history) and hands it to the
        pure detectors via the standalone `_run_detectors` (BACKLOG B-75). Runs from `_notify_loop`
        on the same 5-minute cadence as the operational control loop, but DELIBERATELY
        INDEPENDENTLY of dry_run/controller: `_control_loop` only ever runs in live operational
        mode (see its spawn condition in `lifespan`), yet forecast notifications are just as
        useful during dry-run acceptance (CLAUDE.md "dry-run before every live strategy") and on
        an install with no battery configured at all — so this gets its own tiny loop instead of
        piggybacking on the battery-write path."""
        if store is None or notifier is None:
            return
        now_local = now.astimezone(site_tz)
        tomorrow = now_local.date() + timedelta(days=1)

        p50_tomorrow: dict[datetime, float] = {}
        if solar_forecast is not None:
            p50_tomorrow = {
                s.start: s.p50_w for s in solar_forecast.slots()
                if s.start.astimezone(site_tz).date() == tomorrow
            }

        typical_daily_kwh: float | None = None
        try:
            start = now - timedelta(days=14)
            limit = history_row_cap((now - start).total_seconds(), _sample_cadence_seconds())
            raw_rows = await store.raw_between(start.isoformat(), now.isoformat(), limit=limit)
            typical_daily_kwh = typical_daily_solar_kwh(raw_rows, site_tz, now_local.date())
        except Exception:
            _log.warning("B-75: typical-solar lookup failed (non-fatal)", exc_info=True)

        car_plan_resp = await gather_car_plan(ctx, now)
        car_charging_now = _car_charging(now)

        projected_soc_at_peak: float | None = None
        needed_soc: float | None = None
        confidence_level: str | None = None
        try:
            fp = await _forward_projection()
            if fp is not None and fp["deadline"] is not None:
                deadline = fp["deadline"]
                projected = fp["projected"]
                at_or_after = next((p for p in projected if p.start >= deadline), None)
                projected_soc_at_peak = (
                    at_or_after.soc_pct if at_or_after is not None
                    else (projected[-1].soc_pct if projected else None)
                )
                needed_soc = fp["need"].target_soc_pct
                confidence_level = plan_confidence(
                    data_quality=_data_quality(now),
                    forecast_skill=await _solar_forecast_skill(now),
                    freshness_ok=_freshness_ok(now),
                    battery_reachable=_battery_reachable(now),
                )["level"]
        except Exception:
            _log.warning("B-75: peak-risk projection gather failed (non-fatal)", exc_info=True)

        price_slots_tomorrow: list[Any] = []
        if price_source is not None:
            price_slots_tomorrow = [
                p for p in price_source.slots()
                if p.start.astimezone(site_tz).date() == tomorrow
            ]

        await _run_detectors(
            store, notifier, now_local,
            p50_by_slot_tomorrow=p50_tomorrow, typical_daily_kwh=typical_daily_kwh,
            car_plan=car_plan_resp.get("plan"), car_charging_now=car_charging_now,
            projected_soc_at_peak=projected_soc_at_peak, needed_soc=needed_soc,
            confidence_level=confidence_level, price_slots_tomorrow=price_slots_tomorrow,
        )

    async def _run_recovery_cycle(now: datetime) -> None:
        """Missed-window recovery side effects (SPEC §8.12 / B-16), once per cycle on the same
        cadence as the detectors and DELIBERATELY independent of dry_run — a missed cheap window is
        worth surfacing during dry-run acceptance too. Diagnoses the FRESH (pre-recovery) plan and,
        on a missed window, validates the catch-up + audits + notifies (rate-limited). The plan the
        controller acts on is reshaped in `_current_plan` through the same validator + caps; this
        loop only makes it observable. Fail-safe — an error is logged, never propagated."""
        pp = await asyncio.to_thread(_plan_with_recovery)
        if pp is None:
            return
        plan_now, prices, plan, _status, catch = pp
        # Recovery was already computed for the plan the controller/UI will see; pass the
        # resulting catch-up through the side-effect path without rebuilding or re-diagnosing it.
        if catch is None:
            return
        await _run_recovery(
            plan, plan_now, soc_pct=_current_soc(plan_now), prices=prices,
            enabled=bool(settings_cache["planner.recovery_enabled"]), tz=site_tz,
            cache_store=cache_store, notifier=notifier, audit_store=audit_store,
            validate_fn=_validate_plan_obj, precomputed_catch=catch, precomputed_status=_status,
            **_recovery_sizing(),
        )

    async def _run_canonical_forecast_cycle(now: datetime) -> None:
        """Gather the live solar forecast + the learned baseline load profile and hand them to
        `_run_canonical_forecast` (the 18:00 day-ahead canonical snapshot, design §4.3). Cheap
        gate FIRST — outside [18:00, 20:00) the (possibly expensive) profile/forecast gather is
        skipped; past 20:00 `_run_canonical_forecast` is still called (with empty/placeholder
        gathered inputs it will never use) purely so it can record a missed day in
        `_canonical_forecast_state` / notify — see that function's OBSERVABILITY note. Fail-safe —
        a gather error is logged, never propagated."""
        if store is None:
            return
        now_local = now.astimezone(site_tz)
        if now_local.hour < 18:
            return
        if now_local.hour >= 20:
            await _run_canonical_forecast(
                store, cache_store, now, site_tz, solar_slots=[], solar_source_name="",
                load_profile=None, state=_canonical_forecast_state, notifier=notifier)
            return
        tomorrow = now_local.date() + timedelta(days=1)
        if cache_store is not None:
            try:
                if await asyncio.to_thread(
                    cache_store.get, f"ledger:canonical:{tomorrow.isoformat()}"
                ) is not None:
                    # Already snapshotted tomorrow today — don't rebuild the profile, but the
                    # state box should still reflect the success (it may have already been set by
                    # an earlier cycle, but a fresh restart starts with last_success_date=None).
                    _canonical_forecast_state["ok"] = True
                    _canonical_forecast_state["last_success_date"] = tomorrow.isoformat()
                    return
            except Exception:
                _log.debug("canonical forecast dedupe pre-check failed (non-fatal)", exc_info=True)
        drows = await store.recent_derived(2016)  # ~7 days of derived history for the profile
        fallback_w = settings_cache["battery.overnight_load_kwh"] * 1000.0 / 12.0
        profile = build_load_profile(drows, site_tz, fallback_w=fallback_w)
        solar_slots = (await asyncio.to_thread(solar_forecast.slots)
                       if solar_forecast is not None else [])
        source_name = type(solar_forecast).__name__ if solar_forecast is not None else "none"
        await _run_canonical_forecast(
            store, cache_store, now, site_tz, solar_slots=solar_slots,
            solar_source_name=source_name, load_profile=profile,
            state=_canonical_forecast_state, notifier=notifier)

    async def _notify_loop(stop: asyncio.Event) -> None:
        """Periodic forecast-driven notifications (BACKLOG B-75) + the Sunday weekly-digest
        delivery (BACKLOG B-58) + the 18:00 canonical day-ahead forecast snapshot (design §4.3).
        Its own tiny loop (not `_control_loop` — see `_run_detector_cycle`'s docstring), started
        whenever a store + notifier exist regardless of dry_run/controller. Fail-safe: a gathering
        error is logged and the loop just retries next cycle."""
        while True:
            try:
                await asyncio.wait_for(stop.wait(), timeout=control_cycle_seconds)
                return
            except TimeoutError:
                pass
            try:
                await _run_detector_cycle(datetime.now(UTC))
            except Exception:
                _log.exception("detector cycle failed; retry next cycle (fail-safe)")
            try:
                await _run_recovery_cycle(datetime.now(UTC))
            except Exception:
                _log.exception("recovery cycle failed; retry next cycle (fail-safe)")
            try:
                await _run_canonical_forecast_cycle(datetime.now(UTC))
            except Exception:
                _log.exception("canonical forecast cycle failed; retry next cycle (fail-safe)")
            try:
                await _run_weekly_digest(
                    store, cache_store, notifier, datetime.now(UTC), site_tz,
                    lambda m: gather_digest(ctx, m))
            except Exception:
                _log.exception("weekly digest cycle failed; retry next cycle (fail-safe)")
            try:
                await _run_store_health_check(
                    recorder, notifier, datetime.now(UTC), site_tz, _store_unhealthy_state)
            except Exception:
                _log.exception("store-health check failed; retry next cycle (fail-safe)")

    @app.get("/health/live")
    def live() -> dict:
        return {"status": "alive"}

    def _readiness(now: datetime) -> Readiness:
        """Layered readiness for a control system (energy review #7): alive / dashboard / sensing /
        planning / control. Robust — every input is guarded so health never raises."""
        try:
            dq = _data_quality(now)
        except Exception:
            _log.debug("readiness: data-quality probe failed (non-fatal)", exc_info=True)
            dq = "unsafe"
        plan_valid = True
        plan_ok = False
        try:
            pp = _current_plan()
            plan_ok = pp is not None and bool(pp[2].slots)
            if pp is not None:
                plan_valid = _validate_plan_obj(pp[2], now).ok
        except Exception:
            _log.debug("readiness: plan validation probe failed (non-fatal)", exc_info=True)
            plan_ok, plan_valid = False, False
        return compute_readiness(
            store_ok=store is not None,
            sensing_ok=dq != "unsafe",
            plan_ok=plan_ok,
            data_quality=dq,
            plan_valid=plan_valid,
            operational=not dry_run,
            capability_ok=_capability_box["cap"] is not None,
        )

    @app.get("/health/ready")
    def ready() -> dict:
        r = _readiness(datetime.now(UTC))
        return {"status": "ready" if r.dashboard_ready else "starting",
                "dry_run": dry_run, "dev_mode": dev_mode, "readiness": r.to_dict()}

    @app.get("/api/auth")
    def auth_status(request: Request) -> dict:
        # Lets the UI show a token field only when writes are protected, and reflect auth state.
        return {"required": _effective_web_token() is not None,
                "authenticated": _authorized(request)}

    @app.get("/api/freshness")
    def freshness_snapshot() -> dict:
        if freshness is None:
            return {}
        return freshness.snapshot(datetime.now(UTC))

    @app.get("/api/prices")
    def prices() -> dict:
        if price_source is None:
            return {"currency": "EUR", "resolution": "quarter_hourly",
                    "current_eur_per_kwh": None, "slots": []}
        slots = price_source.slots()
        return {
            "currency": "EUR",
            "resolution": "quarter_hourly",
            "current_eur_per_kwh": current_price(slots, datetime.now(UTC)),
            "slots": [{"start": s.start.isoformat(), "eur_per_kwh": s.eur_per_kwh} for s in slots],
        }

    @app.get("/api/alerts")
    def alerts_endpoint() -> dict:
        now = datetime.now(UTC)
        # Take ONE snapshot and derive both the alert list and the data-quality badge from it, so
        # they can never disagree (a second snapshot could shift if the recorder marks a signal).
        snap = freshness.snapshot(now) if freshness is not None else {}
        dq = data_quality(
            snap, prices_ok=price_source is not None, forecast_ok=solar_forecast is not None
        )
        # The controller's would-do outcome (read-only preview) feeds battery-failure alerts.
        # Honour an active override so the outcome reflects what the controller would really do.
        outcome: str | None = None
        intent, _reason, override_active, tgt, pw, _v, _ca = _effective_intent(now)
        if intent is not None and controller is not None:
            outcome = controller.preview(intent, now, target_soc=tgt, power_w=pw,
                                         observed_mode=_current_mode(now),
                                         manual=override_active,
                                         priority=_car_charging(now),
                                         car_session=_ca is not None and _ca.action == "discharge",
                                         commitment=intent is BatteryIntent.GRID_CHARGE_TO_TARGET,
                                         ).outcome
        alerts = derive_alerts(snap, dry_run=dry_run, decision_outcome=outcome)
        out = [{"key": a.key, "severity": a.severity, "message": a.message,
                "safe": a.safe, "action": a.action} for a in alerts]
        if override_active:
            ov = override_box["ov"]
            until = ov.expires_at.astimezone(site_tz).strftime("%H:%M") if ov.expires_at else "?"
            # If the override was HELD (gated on unsafe data), say so — never claim it's "forcing"
            # the requested action when the battery is actually held at self-consumption.
            held = ov.intent is not None and intent is not ov.intent
            if held:
                msg = (f"Manual override held until {until} — data unsafe, so EMS is holding "
                       "self-consumption instead of forcing the requested action")
                safe = ("Yes — EMS is protecting the battery by holding self-consumption instead "
                        "of forcing an action while the data quality issue lasts.")
                action = ("Nothing needed — EMS applies your override automatically once the "
                          "data-quality issue clears. See the related alert above for what to "
                          "check.")
            else:
                msg = f"Manual override: forcing {intent.value if intent else '?'} until {until}"
                safe = ("Yes — you're intentionally directing the battery; EMS still enforces "
                        "its safety checks underneath.")
                action = (f"Nothing needed — the override ends automatically at {until}, or "
                          "cancel it now from Manual control.")
            out.append({"key": "manual_override_active", "severity": "warning", "message": msg,
                        "safe": safe, "action": action})
        # Cluster mismatch (a tower not following the commanded mode) — surfaced prominently, not
        # just in the audit log, because it means part of the battery isn't doing what was asked.
        laggard_sig = _drift_box.get("sig")
        if laggard_sig:
            ips = ", ".join(ip for ip, _mode in laggard_sig)
            out.append({
                "key": "battery_cluster_mismatch", "severity": "warning",
                "message": f"Battery cluster mismatch — tower(s) {ips} are not following "
                           "the commanded mode. The EMS commands the master; a tower that "
                           "doesn't follow keeps running its own mode.",
                "safe": "Yes — the mismatched tower is just running its own onboard self-use "
                        "mode; nothing unsafe is happening.",
                "action": f"Check tower(s) {ips} — power-cycle or reconnect it if it hasn't "
                          "rejoined the commanded mode after a few cycles.",
            })
        return {"data_quality": dq, "alerts": out}

    @app.get("/api/decision")
    async def decision_endpoint() -> dict:
        # What the controller would do right now, and why. An active override wins over the plan.
        if controller is None:
            return {"intent": None, "desired_mode": None, "applied": False,
                    "outcome": "unconfigured", "reason": "no controller",
                    "plan_reason": None, "override_active": False}
        now = datetime.now(UTC)

        # All the blocking/sync work (cached source/price/forecast reads + a read-only preview that
        # may read battery mode) runs off the event loop, so a slow device can't freeze the loop.
        def _snapshot():
            car_charging = _car_charging(now)
            intent, reason, override_active, tgt, pw, val, car_action = _effective_intent(now)
            if intent is None:
                return None, {"intent": None, "desired_mode": None, "applied": False,
                              "outcome": "no_plan", "reason": "no plan slot for now",
                              "plan_reason": None, "override_active": False,
                              "car_charging": car_charging}
            # preview() is read-only — a GET must never write to the battery or mutate counters.
            # Pass the coalesced observed mode so this poll doesn't read battery mode every cycle.
            # car_session keeps the previewed mode honest (DISCHARGE, not AUTO) in a car session.
            car_session = car_action is not None and car_action.action == "discharge"
            d = controller.preview(intent, now, target_soc=tgt, power_w=pw,
                                   observed_mode=_current_mode(now), manual=override_active,
                                   priority=car_charging, car_session=car_session,
                                   commitment=intent is BatteryIntent.GRID_CHARGE_TO_TARGET)
            home = home_state(
                _readiness(now), intent=str(d.intent), override_active=override_active,
                simulated=dev_mode != "live",
            )
            return (intent, reason, override_active, tgt, pw, val, d, car_charging, home), None

        computed, early = await asyncio.to_thread(_snapshot)
        if early is not None:
            return early
        intent, reason, override_active, tgt, pw, val, d, car_charging, home = computed
        # Phrase the deterministic plan reason via the explainer (verbatim unless AI is on; cached).
        explained = await _explain(
            reason, {"intent": str(intent), "desired_mode": str(d.desired_mode)}
        )
        return {
            "intent": d.intent,
            "desired_mode": d.desired_mode,
            "applied": d.applied,
            "outcome": d.outcome,
            "reason": d.reason,
            "plan_reason": reason,
            "plan_reason_explained": explained["text"],
            "explanation_source": explained["source"],
            "override_active": override_active,
            # Surfaced so the dashboard can show "car charging — battery held".
            "car_charging": car_charging,
            # §8.11 plan validation (status + findings) so the UI can show why control is held —
            # reuse the result _effective_intent already computed (no second plan rebuild).
            "plan_validation": (val.to_dict() if val is not None else None),
            # The energy amount travelling with the mode (energy review P2.4): the SoC the
            # controller would aim for now (None for self-consumption/hold) → UI "aiming for X%".
            "target_soc": tgt,
            # The single top-of-dashboard headline + tone the homeowner reads first (emotional #1).
            "home_state": home,
        }

    @app.get("/api/diagnostics")
    async def diagnostics_endpoint() -> dict:
        now = datetime.now(UTC)
        prices_ok = price_source is not None
        forecast_ok = solar_forecast is not None
        # Actually probe the stores so a broken DB shows as a failed check, not a silent pass.
        store_ok = False
        if store is not None:
            try:
                await store.table_names()
                store_ok = True
            except Exception:
                _log.debug("diagnostics: history-store probe failed (non-fatal)", exc_info=True)
                store_ok = False
        settings_ok = False
        if settings_store is not None:
            try:
                await settings_store.all()
                settings_ok = True
            except Exception:
                _log.debug("diagnostics: settings-store probe failed (non-fatal)", exc_info=True)
                settings_ok = False
        # probe() is a SYNC, possibly-networked call — run it off the event loop and guard it so an
        # unreachable battery shows as a warn check, not a 500 (and never blocks the loop).
        p1_paired = False
        battery_ok = battery is not None
        if battery is not None:
            try:
                p1_paired = (await asyncio.to_thread(battery.probe)).p1_paired
            except Exception:
                _log.debug("diagnostics: battery probe failed (non-fatal)", exc_info=True)
                battery_ok = False
        # data-quality / plan / readiness all run sync helpers that touch cached source/price/
        # forecast reads — compute them off the event loop so a slow device can't stall /api/health.
        def _core():
            return (_data_quality(now), _current_plan() is not None, _readiness(now).to_dict())

        dq, plan_ok, readiness = await asyncio.to_thread(_core)
        # The car-charging guard needs the EV meter to see the car; on + live + no EV meter = blind.
        ev_guard_blind = (
            bool(settings_cache.get("control.hold_battery_when_car_charging"))
            and dev_mode == "live"
            and not (settings_cache.get("meters.car_ip") or "").strip()
        )
        checks = build_diagnostics(
            dev_mode=dev_mode, dry_run=dry_run,
            data_quality=dq,
            prices_ok=prices_ok, forecast_ok=forecast_ok,
            battery_ok=battery_ok, p1_paired=p1_paired,
            plan_ok=plan_ok,
            store_ok=store_ok, settings_store_ok=settings_ok,
            auth_required=_effective_web_token() is not None,
            freshness=freshness.snapshot(now) if freshness is not None else None,
            ev_guard_blind=ev_guard_blind,
        )
        # Observability: how much is currently cached (reused instead of refetched / re-spent).
        cache_stats = None
        if cache_store is not None:
            try:
                cache_stats = await asyncio.to_thread(cache_store.breakdown)
            except Exception:
                _log.debug("diagnostics: cache breakdown failed (non-fatal)", exc_info=True)
                cache_stats = None
        # Long-run diagnostics (review): DB/WAL size + sample row counts, and recorder health so a
        # stuck recorder (full disk, DB lock, dead device) is VISIBLE, not just inferred from stale.
        storage = None
        if store is not None:
            try:
                storage = await store.db_stats()
            except Exception:
                _log.debug("diagnostics: db_stats failed (non-fatal)", exc_info=True)
                storage = None
            # Durability status (SPEC §11): the last scheduled-backup outcome + retained count, so
            # a silently-failing backup is visible alongside DB size. Copied out of the loop's box.
            if storage is not None:
                storage["backup"] = dict(_backup_state)
                # 18:00 canonical-forecast job status (design §4.3) — a dead job is otherwise
                # invisible until a gap shows up in forecasts.csv or the accuracy surfaces.
                storage["canonical_forecast"] = dict(_canonical_forecast_state)
                # Store self-heal visibility (B-49): the recorder's consecutive persist-failure
                # streak (the operator-visible "samples aren't storing" signal) + the last time the
                # history store had to discard + reopen a dead shared connection.
                storage["history_store"] = {
                    "consecutive_persist_failures": (
                        recorder.health()["consecutive_failures"] if recorder is not None else 0),
                    "last_reheal_iso": store.reheal_stats()["last_reheal_iso"],
                }
        return {"overall": overall_status(checks), "checks": [c.to_dict() for c in checks],
                "cache": cache_stats, "readiness": readiness, "storage": storage,
                "recorder": recorder.health() if recorder is not None else None}

    @app.get("/api/charge-need")
    def charge_need_endpoint() -> dict:
        # Advisory: how much the battery should hold by tonight, from current SoC + battery config.
        # Coalesced SoC (shared window) — don't read the battery on every poll of this card.
        s = settings_cache
        return compute_charge_need(
            soc_pct=_current_soc(datetime.now(UTC)),
            usable_kwh=s["battery.usable_kwh"],
            min_reserve_soc=s["battery.min_reserve_soc"],
            night_reserve_kwh=s["battery.night_reserve_kwh"],
            overnight_load_kwh=s["battery.overnight_load_kwh"],
            round_trip_efficiency=s["planner.round_trip_efficiency"],
        ).to_dict()

    @app.get("/api/override")
    def get_override() -> dict:
        now = datetime.now(UTC)
        return {**override_box["ov"].to_dict(now), "options": [i.value for i in BatteryIntent]}

    @app.post("/api/override")
    async def set_override(request: Request, body: dict | None = None) -> JSONResponse:
        # Auth is enforced centrally by the _enforce_access middleware (writes always gated).
        if override_store is None:
            return JSONResponse({"detail": "override store not configured"}, status_code=503)
        body = body or {}
        raw_intent = body.get("intent")
        now = datetime.now(UTC)
        # A null/"none"/missing intent clears the override (return to following the plan).
        if raw_intent in (None, "", "none"):
            await override_store.delete(_OV_INTENT, _OV_EXP)
            override_box["ov"] = OVERRIDE_NONE
            if audit_store is not None:
                await audit_store.append(
                    now.isoformat(), "manual_override",
                    "Manual override cleared — back to the automatic plan", {"action": "clear"},
                )
            if not dry_run:
                # apply now, don't wait a full cycle (tracked so it can't be GC'd mid-run)
                _spawn_tracked(_run_control_cycle(), "Override control cycle", _override_tasks)
            return JSONResponse(get_override())
        errors: dict[str, str] = {}
        try:
            intent = BatteryIntent(raw_intent)
        except ValueError:
            errors["intent"] = f"must be one of: {', '.join(i.value for i in BatteryIntent)}"
        minutes = body.get("minutes", 60)
        if isinstance(minutes, bool) or not isinstance(minutes, (int, float)):
            errors["minutes"] = "must be a number of minutes"
        elif not (MIN_MINUTES <= minutes <= MAX_MINUTES):
            errors["minutes"] = f"must be between {MIN_MINUTES} and {MAX_MINUTES}"
        if errors:
            return JSONResponse({"detail": "invalid override", "errors": errors}, status_code=422)
        expires = now + timedelta(minutes=int(minutes))
        await override_store.set_many({_OV_INTENT: intent.value, _OV_EXP: expires.isoformat()})
        override_box["ov"] = Override(intent=intent, expires_at=expires)
        if audit_store is not None:
            await audit_store.append(
                now.isoformat(), "manual_override",
                f"Manual override: {intent.value} for {int(minutes)} min",
                {"action": "set", "intent": intent.value, "minutes": int(minutes),
                 "expires_at": expires.isoformat()},
            )
        # Apply the override on the battery NOW (and audit the confirmed result) instead of waiting
        # up to a full control cycle — what the operator expects when they press "charge".
        if not dry_run:
            _spawn_tracked(_run_control_cycle(), "Override control cycle", _override_tasks)
        return JSONResponse(get_override())

    def _battery_cluster(now: datetime) -> tuple[list[dict], dict | None]:
        """Per-tower readings + the cluster aggregate, via the COALESCED tower read (so polling
        /api/battery doesn't hit every Indevolt tower on every dashboard refresh). Empty/None for
        the mock source (no per-tower reader) or before the first successful read."""
        towers = _current_towers(now)
        if not towers:
            return [], None
        rows = [
            {"ip": t.ip, "role": t.role, "soc_pct": t.soc_pct, "power_w": t.power_w,
             "capacity_kwh": t.capacity_kwh, "online": t.online, "mode": t.mode}
            for t in towers
        ]
        online = [t for t in towers if t.online and t.soc_pct is not None]
        if not online:
            return rows, None
        caps = [t.capacity_kwh for t in online]
        all_caps = all(c and c > 0 for c in caps)
        aggregate = {
            "soc_pct": round(aggregate_soc(online), 1),
            "power_w": round(sum(t.power_w for t in online), 1),
            "capacity_kwh": round(sum(caps), 2) if all_caps else None,
            "online_towers": len(online),
            "total_towers": len(towers),
        }
        return rows, aggregate

    @app.get("/api/battery")
    def battery_endpoint() -> dict:
        towers, aggregate = _battery_cluster(datetime.now(UTC))
        out: dict[str, Any] = {
            "current_mode": None, "capabilities": None,
            "towers": towers, "aggregate": aggregate,
        }
        if battery is None:
            return out
        cap = battery.probe()
        out["current_mode"] = battery.current_mode()
        out["capabilities"] = {
            "services": list(cap.services),
            "energy_mode_options": list(cap.energy_mode_options),
            "has_standby": cap.has_standby,
            "has_grid_charge_switch": cap.has_grid_charge_switch,
            "p1_paired": cap.p1_paired,
            "max_charge_w": cap.max_charge_w,
            "max_discharge_w": cap.max_discharge_w,
        }
        return out

    @app.get("/api/savings")
    def savings_endpoint() -> dict:
        pp = _current_plan()
        if pp is None:
            return {"today_eur": None}
        _now, prices, plan = pp
        by_start = {p.start: p.eur_per_kwh for p in prices}
        return {"today_eur": estimate_daily_savings_eur(plan, by_start)}

    # Planning knobs that shape the plan — the ONLY settings included in a replay bundle. Explicit
    # allow-list, so no meter IP, token, key or location can ever leak into an export (privacy §12).
    _REPLAY_SETTING_KEYS = (
        "strategy.mode", "strategy.summer_grid_topup", "strategy.summer_max_topup_price",
        "battery.usable_kwh", "battery.min_reserve_soc", "battery.night_reserve_kwh",
        "battery.overnight_load_kwh", "battery.max_charge_w", "battery.max_discharge_w",
        "planner.round_trip_efficiency", "planner.degradation_eur_per_kwh",
        "planner.risk_margin_eur_per_kwh", "planner.charge_slots", "planner.discharge_slots",
        "control.max_switches_per_day", "control.min_dwell_seconds",
    )

    @app.get("/api/replay")
    def replay_endpoint() -> dict:
        """A reproducibility bundle (energy review P2.6): the exact inputs, plan, projection,
        validation and decision behind the current state, so any surprising decision can be replayed
        offline. REDACTED — only planning knobs + non-identifying values; never IPs/tokens/location.
        Download from the System tab."""
        now = datetime.now(UTC)
        pp = _current_plan()
        if pp is None:
            return {"generated_at": now.isoformat(), "plan": None,
                    "note": "no plan yet (prices/forecast loading or no price source)"}
        _now, prices, plan = pp
        strat, why = _resolve_strategy(now)
        val = _validate_plan_obj(plan, now)
        proj = _projection_sync(plan, now) or []
        intent, dreason, override_active, tgt, _pw, _v, _ca = _effective_intent(now)
        s = settings_cache
        fc = solar_forecast.slots()[:96] if solar_forecast is not None else []
        return {
            "generated_at": now.isoformat(),
            "strategy": {"mode": s["strategy.mode"], "active": strat, "reason": why},
            "inputs": {
                "soc_pct": _current_soc(now),
                "data_quality": _data_quality(now),
                "settings": {k: s[k] for k in _REPLAY_SETTING_KEYS if k in s},
                "prices": [{"start": p.start.isoformat(), "eur_per_kwh": p.eur_per_kwh}
                           for p in prices[:96]],
                "forecast_p50_w": [{"start": f.start.isoformat(), "w": f.p50_w} for f in fc],
            },
            "plan": {
                "created_at": plan.created_at.isoformat(), "strategy": plan.strategy,
                "target_soc": plan.target_soc,
                "deadline": plan.deadline.isoformat() if plan.deadline else None,
                "slots": [{"start": sl.start.isoformat(), "intent": sl.intent.value,
                           "reason": sl.reason, "target_soc": sl.target_soc,
                           "target_kwh": sl.target_kwh, "power_w": sl.power_w,
                           "floor_soc": sl.floor_soc,
                           "deadline": sl.deadline.isoformat() if sl.deadline else None}
                          for sl in plan.slots],
            },
            "projection": [{"start": p.start.isoformat(), "soc_pct": round(p.soc_pct, 2),
                            "intent": p.intent.value} for p in proj],
            "validation": val.to_dict(),
            "decision": {"intent": str(intent) if intent else None, "reason": dreason,
                         "override_active": override_active, "target_soc": tgt},
        }

    @app.get("/api/plan")
    def plan_endpoint() -> dict:
        pp = _current_plan()
        if pp is None:
            return {"created_at": None, "current_intent": None,
                    "current_reason": None, "slots": []}
        now, _prices, plan = pp
        cur = plan.intent_at(now)
        val = _validate_plan_obj(plan, now)
        return {
            "created_at": plan.created_at.isoformat(),
            "strategy": plan.strategy,
            "target_soc": plan.target_soc,
            "deadline": plan.deadline.isoformat() if plan.deadline else None,
            "current_intent": cur.intent if cur else None,
            "current_reason": cur.reason if cur else None,
            # The §8.11 verdict so the UI can show "control held — why".
            "validation": val.to_dict(),
            "slots": [
                # The energy contract travels with the mode (energy review P2.4): the UI shows
                # "charge to X% (Y kWh) at Z W by <deadline>", not just a mode label.
                {"start": s.start.isoformat(), "intent": s.intent, "reason": s.reason,
                 "target_soc": s.target_soc, "target_kwh": s.target_kwh, "power_w": s.power_w,
                 "floor_soc": s.floor_soc,
                 "deadline": s.deadline.isoformat() if s.deadline else None}
                for s in plan.slots
            ],
        }

    @app.post("/api/plan-preview")
    def plan_preview(body: dict | None = None) -> dict:
        # What-if: recompute the plan with PROPOSED (unsaved) settings so the UI can show the impact
        # of a change before saving. Read-only — no persistence, no battery.
        if price_source is None:
            return {"current": None, "proposed": None}
        now = datetime.now(UTC)
        prices_ = price_source.slots()
        clean, _errors = validate_settings(body or {})
        merged = {**settings_cache, **clean}
        cur = plan_rule_based(prices_, now, _planner_cfg_from(settings_cache))
        prop = plan_rule_based(prices_, now, _planner_cfg_from(merged))
        return {
            "current": plan_metrics(cur, prices_),
            "proposed": plan_metrics(prop, prices_),
        }

    @app.get("/api/plan-detail")
    def plan_detail() -> dict:
        # Plan + prices + solar joined on ONE timeline (the plan's slots) so the UI can align them.
        pp = _current_plan()
        if pp is None:
            return {"current_intent": None, "summary": "No plan yet.", "slots": [],
                    "strategy": _active_strategy(datetime.now(UTC))}
        now, prices_, plan = pp
        fc = solar_forecast.slots() if solar_forecast is not None else None
        return {**build_plan_detail(now, prices_, plan, fc), "strategy": _active_strategy(now)}

    _STRATEGY_DESC = {
        "summer": "Solar-first — fill the battery from your panels and run the night on it; "
                  "top up from the grid only if the sun falls short.",
        "winter": "Arbitrage — charge the battery in the cheapest hours and discharge it during "
                  "the expensive evening peaks.",
    }

    @app.get("/api/strategy")
    def strategy_endpoint() -> dict:
        # What strategy is running, why, and its key knobs — drives the dashboard strategy card.
        now = datetime.now(UTC)
        mode = settings_cache["strategy.mode"]
        active, why = _resolve_strategy(now)
        return {
            "mode": mode,  # auto | summer | winter (the user's choice)
            "active": active,  # the resolved strategy actually running
            "auto": mode == "auto",
            "summary": _STRATEGY_DESC.get(active, ""),
            # Deterministic 'why this strategy' (emotional review) — esp. useful for auto.
            "reason": why,
            "grid_topup": settings_cache["strategy.summer_grid_topup"],
            "max_topup_price": settings_cache["strategy.summer_max_topup_price"],
        }

    def _battery_model() -> BatteryModel:
        s = settings_cache
        return BatteryModel(
            usable_kwh=s["battery.usable_kwh"],
            max_charge_w=s["battery.max_charge_w"],
            max_discharge_w=s["battery.max_discharge_w"],
            round_trip_efficiency=s["planner.round_trip_efficiency"],
            reserve_soc_pct=s["battery.min_reserve_soc"],
        )

    async def _forward_projection():
        """The forward plan + projection bundle (or None if there's no plan yet). Shared by
        /api/energy-forecast and /api/energy-story so they never drift. The async history read
        happens here; the blocking source/price/forecast reads + CPU projection run in a worker
        thread so this never stalls the event loop (a slow meter/Tibber/Forecast.Solar must not
        freeze unrelated requests)."""
        # Learn the expected load from ~7 days of derived history (async DB read off the loop).
        drows = await store.recent_derived(2016) if store is not None else []

        def _compute():
            pp = _current_plan()  # touches price_source/solar_forecast/source.read (all cached)
            if pp is None or solar_forecast is None:
                return None
            now, prices_, plan = pp
            if not plan.slots:
                return None
            soc = _current_soc(now)
            fc_slots = solar_forecast.slots()
            solar_by = {f.start: f.p50_w for f in fc_slots}
            fallback_w = settings_cache["battery.overnight_load_kwh"] * 1000.0 / 12.0
            profile = build_load_profile(drows, site_tz, fallback_w=fallback_w)
            _load_profile_box["profile"] = profile  # share with the sync _current_plan (adaptive)
            load_by = {s.start: profile.expected_w(s.start) for s in plan.slots}
            need = compute_charge_need(
                soc_pct=soc, usable_kwh=settings_cache["battery.usable_kwh"],
                min_reserve_soc=settings_cache["battery.min_reserve_soc"],
                night_reserve_kwh=settings_cache["battery.night_reserve_kwh"],
                overnight_load_kwh=settings_cache["battery.overnight_load_kwh"],
                round_trip_efficiency=settings_cache["planner.round_trip_efficiency"],
            )
            # Both seasons use the adaptive charger, which sizes its own charge slots — the
            # projection must NOT cap them at the night target (undoing demand-aware peak-shaving).
            projected = project_energy(
                plan.slots, start_soc_pct=soc, solar_w_by=solar_by,
                load_w_by=load_by, model=_battery_model(),
                charge_target_soc_pct=None,
            )
            return {"now": now, "current_soc": soc, "projected": projected, "need": need,
                    "deadline": sunset_after(fc_slots, now),
                    "price_by": {p.start: p.eur_per_kwh for p in prices_},
                    # The resolved season ('summer'/'winter') the ACTIVE plan was built with —
                    # carried through so /api/battery-plan's provenance line never has to rebuild
                    # the plan (or re-touch the seasonal-hysteresis counter) to know which planner
                    # ran.
                    "strategy": plan.strategy}

        return await asyncio.to_thread(_compute)

    @app.get("/api/energy-forecast")
    async def energy_forecast() -> dict:
        # Recorded SoC (past) + a forward projection (future) of SoC and grid flow. Read-only.
        reserve_pct = settings_cache["battery.min_reserve_soc"]
        history: list[dict] = []
        if store is not None:
            rows = await store.recent_raw(288)
            history = [{"ts": r["ts"], "soc_pct": r["soc_pct"]} for r in reversed(rows)]
        empty = {"now": datetime.now(UTC).isoformat(), "current_soc_pct": None,
                 "reserve_soc_pct": reserve_pct, "history": history, "projection": [],
                 "summary": "No plan yet.", "target_soc_pct": None, "target_kwh": None,
                 "target_deadline": None}
        fp = await _forward_projection()
        if fp is None:
            return empty
        projected, need, deadline = fp["projected"], fp["need"], fp["deadline"]
        return {
            "now": fp["now"].isoformat(),
            "current_soc_pct": round(fp["current_soc"], 1),
            "reserve_soc_pct": reserve_pct,
            "target_soc_pct": round(need.target_soc_pct, 1),
            "target_kwh": round(need.target_kwh, 1),
            "target_deadline": deadline.isoformat() if deadline is not None else None,
            "history": history,
            "projection": [
                {"start": p.start.isoformat(), "intent": p.intent,
                 "soc_pct": round(p.soc_pct, 1), "battery_w": round(p.battery_w, 1),
                 "grid_w": round(p.grid_w, 1), "solar_w": round(p.solar_w, 1),
                 "load_w": round(p.load_w, 1)}
                for p in projected
            ],
            **summarize_projection(projected),
        }

    def _empty_story(window: str, reserve_pct: float, headline: str) -> dict:
        return {"window": window, "now": datetime.now(UTC).isoformat(),
                "current_soc_pct": None, "reserve_soc_pct": reserve_pct,
                "target_soc_pct": None, "target_kwh": None, "target_deadline": None,
                "current_price_eur_per_kwh": None,
                "slots": [], "totals": _uslot_totals([]), "headline": headline}

    def _next_headline(totals: dict, need, grid_charge_kwh: float) -> str:
        # IMPORTANT: only a GRID charge is a "top up". totals["charge_kwh"] also counts SOLAR
        # charging the battery, which is not a grid top-up — using it would over-claim (the bug
        # where the headline promised a top-up the plan didn't contain).
        imp = totals["import_kwh"]
        ss = totals["self_sufficiency_pct"]
        peak = totals["soc_max_pct"]
        if grid_charge_kwh > 0.1:
            head = (f"Next 24h — top up {grid_charge_kwh:.1f} kWh from the grid toward the "
                    f"{need.target_soc_pct:.0f}% night target, then run the evening on battery.")
        elif peak is not None:
            head = (f"Next 24h — your solar fills the battery (peaking near {peak:.0f}%), then "
                    "runs the evening on it — no grid charging.")
        else:
            head = "Next 24h — running on solar + battery; no grid charging."
        head += f" Projected {imp:.1f} kWh imported"
        head += f", {ss:.0f}% self-sufficient." if ss is not None else "."
        return head

    def _trust_markers(projected, totals: dict, reserve_pct: float, target_pct: float) -> list[str]:
        """A few quiet, TRUE-only confirmations the plan is taking care of the home (emotional
        review): reserve respected, on track for the target, no needless grid top-up, peak covered.
        Only true markers are returned — no hype, no false positives."""
        markers: list[str] = []
        socs = [p.soc_pct for p in projected]
        if socs and min(socs) >= reserve_pct - 1e-6:
            markers.append("Reserve respected")
        if socs and target_pct > 0 and socs[-1] >= target_pct - 1.0:
            markers.append("On track for tonight's target")
        # "No grid top-up needed" iff there's no GRID charge slot — NOT total charge (which counts
        # solar filling the battery too).
        if not any(p.intent is BatteryIntent.GRID_CHARGE_TO_TARGET for p in projected):
            markers.append("No grid top-up needed")
        if any(p.intent is BatteryIntent.DISCHARGE_FOR_LOAD for p in projected):
            markers.append("Battery covers the evening peak")
        return markers

    def _slot_end_iso(slot: dict) -> str:
        if end := slot.get("end"):
            return end
        try:
            return (datetime.fromisoformat(slot["start"]) + timedelta(minutes=15)).isoformat()
        except Exception:
            _log.debug("slot end-time parse failed; using slot start (non-fatal)", exc_info=True)
            return slot["start"]

    def _action_blocks(slots: list[dict]) -> list[dict]:
        blocks: list[dict] = []
        for s in slots:
            action = s["action"]
            if blocks and blocks[-1]["action"] == action:
                blocks[-1]["end"] = _slot_end_iso(s)
                continue
            blocks.append({"start": s["start"], "end": _slot_end_iso(s), "action": action})
        return blocks

    def _planned_charge_windows(slots: list[dict]) -> list[dict]:
        """The windows the PLANNER actually grid-charges in (not a naive cheapest-percentile), so
        the highlighted band on the chart always matches where the EMS really buys. Contiguous
        `grid_charge` slots are merged; the band carries the min/max price it buys at. Windows with
        no stored price are dropped (the chart needs numeric bounds)."""
        windows: list[dict] = []
        for s in slots:
            if s.get("action") != "grid_charge":
                continue
            price = s.get("eur_per_kwh")
            if windows and windows[-1]["end"] == s["start"]:
                windows[-1]["end"] = _slot_end_iso(s)
                if price is not None:
                    lo, hi = windows[-1]["min_eur_per_kwh"], windows[-1]["max_eur_per_kwh"]
                    windows[-1]["min_eur_per_kwh"] = price if lo is None else min(lo, price)
                    windows[-1]["max_eur_per_kwh"] = price if hi is None else max(hi, price)
                continue
            windows.append({"start": s["start"], "end": _slot_end_iso(s),
                            "min_eur_per_kwh": price, "max_eur_per_kwh": price})
        return [w for w in windows if w["min_eur_per_kwh"] is not None]

    async def _window_price_slots(start_iso: str, end_iso: str) -> list:
        """Prices for a PAST window. Uses the persisted `price_slots` (the recorder saves the curve
        each cycle) so historical slots keep the price that was active then — the live feed only
        carries the current day/tomorrow, which left yesterday's price bars blank. The live feed is
        still included for any recent slot not yet persisted; build_past_story keys by slot start,
        so overlaps are harmless (the stored value wins)."""
        live = list(price_source.slots()) if price_source is not None else []
        if store is None:
            return live
        rows = await store.prices_between(start_iso, end_iso)
        stored = [PriceSlot(datetime.fromisoformat(r["start_ts"]), r["eur_per_kwh"]) for r in rows]
        return live + stored

    async def _window_carbon_factor(
        start_iso: str, end_iso: str
    ) -> tuple[float | None, str | None]:
        """Roadmap F3 (Insights reporting only): the window's average LIVE grid CO2 intensity
        (kg/kWh) from stored `carbon_intensity` rows (upserted by the recorder each cycle from
        the configured carbon source), plus a short note for the CO₂ score's explanation. A plain
        (unweighted) mean over the window's 15-min slots. (None, None) when nothing is stored for
        this window — the caller then stays on the flat `reporting.grid_co2_factor` setting, no
        different from before this feature existed."""
        if store is None:
            return None, None
        rows = await store.carbon_between(start_iso, end_iso)
        if not rows:
            return None, None
        avg = sum(r["kg_per_kwh"] for r in rows) / len(rows)
        return avg, f" (live grid signal, avg {avg:.2f} kg/kWh)"

    async def _recent_actuals(now: datetime) -> list[dict]:
        """The last RECENT_HOURS of RECORDED actuals on the 15-min grid (oldest→now), so the planner
        timeline can show what really happened just before now: actual SoC, actual solar, and the
        action the battery actually executed. Built like the 'past' story but scoped to the window;
        [] when there's no history yet (graceful)."""
        if store is None:
            return []
        cutoff = (now - timedelta(hours=RECENT_HOURS)).isoformat()
        raw = await store.recent_raw_since(cutoff)
        if not raw:
            return []
        der = await store.recent_derived_since(cutoff)
        prices = await _window_price_slots(cutoff, now.isoformat())
        story = build_past_story(raw, der, prices, now)
        return [
            _uslot(ps.start, ps.soc_pct, ps.grid_w, ps.solar_w, ps.battery_w, ps.load_w,
                   ps.eur_per_kwh,
                   # Split the charge by source using NON-EV (house-only) load: a concurrent car
                   # session must not crowd out the solar-vs-grid attribution (§4.5, retrospect).
                   _action_from_battery(ps.battery_w, ps.solar_w, ps.non_ev_load_w))
            for ps in story.slots
        ]

    def _on_track(current_soc: float, need, totals: dict, grid_charge_kwh: float,
                  reserve_pct: float) -> dict:
        """Verdict derived from the ACTUAL plan (not a separate heuristic), so it can never claim a
        top-up the plan doesn't contain. The conservative night target is a ceiling, not the goal —
        what matters is staying self-sufficient and above reserve:
          ahead       — the plan reaches the night target;
          on_track    — projected self-sufficient (≈no import) AND above reserve (even if below the
                        target — that's fine, no grid power needed);
          behind      — short AND either a grid top-up IS planned (say so, truthfully) or the
                        home will import / dip toward reserve (don't promise a phantom top-up)."""
        target = need.target_soc_pct
        proj_min, proj_max = totals.get("soc_min_pct"), totals.get("soc_max_pct")
        imp = totals.get("import_kwh")
        above_reserve = proj_min is None or proj_min >= reserve_pct - 0.5
        self_sufficient = imp is not None and imp < 0.1
        if proj_max is not None and proj_max >= target - 0.5:
            status = "ahead"
            msg = f"On track — projected to reach the {target:.0f}% night target."
        elif self_sufficient and above_reserve:
            status = "on_track"
            msg = (f"On track — solar covers the home: projected self-sufficient and above the "
                   f"{reserve_pct:.0f}% reserve. Below the {target:.0f}% night target, but no grid "
                   "power is needed.")
        elif grid_charge_kwh > 0.05:
            status = "behind"
            msg = (f"Behind the {target:.0f}% target — EMS tops up {grid_charge_kwh:.1f} kWh from "
                   "the grid in the cheapest window before sunset.")
        elif imp and imp > 0.05:
            status = "behind"
            msg = (f"Short of the {target:.0f}% target with no grid top-up planned — about "
                   f"{imp:.1f} kWh will come from the grid.")
        else:
            status = "behind"
            msg = (f"Short of the {target:.0f}% target — the battery may dip toward its "
                   f"{reserve_pct:.0f}% reserve before morning.")
        return {"status": status, "actual_soc_pct": round(current_soc, 1),
                "target_soc_pct": round(target, 1), "deficit_kwh": round(need.deficit_kwh, 1),
                "message": msg}

    def _recent_review(recent: list[dict], fc_slots) -> dict | None:
        """'Did the last few hours go as expected?' — actual solar produced vs the FORECAST for the
        same slots (was the sun as predicted?) and what the battery actually did (in/out). Honest,
        no hype. None when there's no recent history yet."""
        if not recent:
            return None
        dh = 0.25 / 1000.0  # 15-min slot, W → kWh
        solar_actual = sum(s["solar_w"] for s in recent) * dh
        # Match the forecast to the actuals on the 15-min epoch bucket, NOT the ISO string — the two
        # sources can carry different tz reps/precision for the same instant. timestamp() is
        # tz-agnostic, so flooring to 900 s lines them up regardless.
        def _bucket(dt: datetime) -> int:
            return int(dt.timestamp()) // 900
        fc_by = {_bucket(f.start): f.p50_w for f in fc_slots}
        fc_vals = [fc_by[k] for s in recent
                   if (k := _bucket(datetime.fromisoformat(s["start"]))) in fc_by]
        solar_fc = sum(fc_vals) * dh if fc_vals else None
        charged = sum(max(0.0, -s["battery_w"]) for s in recent) * dh
        discharged = sum(max(0.0, s["battery_w"]) for s in recent) * dh
        pct = round(solar_actual / solar_fc * 100) if solar_fc and solar_fc > 0.05 else None
        head = f"Last {RECENT_HOURS}h: {solar_actual:.1f} kWh solar"
        if pct is not None:
            head += f" ({pct}% of the {solar_fc:.1f} kWh forecast)"
        parts = [head]
        if charged > 0.05 or discharged > 0.05:
            parts.append(f"battery +{charged:.1f}/−{discharged:.1f} kWh")
        return {
            "hours": RECENT_HOURS,
            "solar_actual_kwh": round(solar_actual, 1),
            "solar_forecast_kwh": round(solar_fc, 1) if solar_fc is not None else None,
            "solar_pct_of_forecast": pct,
            "battery_charged_kwh": round(charged, 1),
            "battery_discharged_kwh": round(discharged, 1),
            "message": "; ".join(parts) + ".",
        }

    async def _next_story(reserve_pct: float) -> dict:
        fp = await _forward_projection()
        if fp is None:
            return _empty_story("next", reserve_pct, "No plan yet.")
        price_by, need, deadline = fp["price_by"], fp["need"], fp["deadline"]
        slots = [
            _uslot(p.start, p.soc_pct, p.grid_w, p.solar_w, p.battery_w, p.load_w,
                   price_by.get(p.start), _action_from_intent(p.intent, p.battery_w))
            for p in fp["projected"]
        ]
        totals = _uslot_totals(slots)
        # GRID top-up only — solar charging the battery (self-consume slots) must NOT count as a
        # top-up. The totals already split charge by source from the slot action.
        grid_charge_kwh = totals["grid_charge_kwh"]
        recent = await _recent_actuals(fp["now"])
        return {
            "window": "next", "now": fp["now"].isoformat(),
            "current_soc_pct": round(fp["current_soc"], 1), "reserve_soc_pct": reserve_pct,
            "target_soc_pct": round(need.target_soc_pct, 1),
            "target_kwh": round(need.target_kwh, 1),
            "target_deadline": deadline.isoformat() if deadline is not None else None,
            # The price right now = the first slot (it covers the current quarter-hour).
            "current_price_eur_per_kwh": slots[0]["eur_per_kwh"] if slots else None,
            "slots": slots, "totals": totals,
            "headline": _next_headline(totals, need, grid_charge_kwh),
            # Quiet, true-only trust markers (emotional review): proof the plan is doing right by
            # the home — never celebratory, only shown when genuinely true.
            "trust_markers": _trust_markers(fp["projected"], totals, reserve_pct,
                                            need.target_soc_pct),
            # "Am I on track?" — the last few hours of actuals on the same timeline + a verdict
            # DERIVED FROM THE PLAN (consistent with the chart) + a "did we do right" review.
            "recent_hours": RECENT_HOURS,
            "recent": recent,
            "on_track": _on_track(fp["current_soc"], need, totals, grid_charge_kwh, reserve_pct),
            "recent_review": _recent_review(
                recent, solar_forecast.slots() if solar_forecast is not None else []
            ),
        }

    def _forecast_source_label() -> str:
        """Humanized forecast-source class name for the plan-provenance line — 'what's actually
        feeding today's forecast', not a raw Python class name. Deliberately keyed on the CLASS
        (ForecastSolarSource / MockSolarForecastSource), not on whether ForecastSolarSource's own
        internal model-curve fallback happened to fire on the last fetch — that distinction is
        already surfaced honestly by /api/forecast's `source_label`."""
        if solar_forecast is None:
            return "No forecast source"
        name = type(solar_forecast).__name__
        if name in _FORECAST_SOURCE_LABEL:
            return _FORECAST_SOURCE_LABEL[name]
        # Unknown/future adapter: split CamelCase into words instead of leaking the raw class name.
        words = re.findall(r"[A-Z][a-z0-9]*", name.removesuffix("Source")) or [name]
        return " ".join(words)

    def _resolved_planner_name(strategy: str) -> str:
        """Which planner FUNCTION produced the live plan (ems.planner.strategy.build_plan), not just
        which season — the plan-provenance line reads 'rule-based winter planner', not just
        'winter'. `build_plan` overwrites `Plan.strategy` to the season name for the rest of the
        system, so this mirrors its dispatch instead of reading it back: `_current_plan` (via
        `_build_plan_now`) always supplies `load_w_by` + `AdaptiveConfig`, so 'summer' always
        resolves to the demand-aware adaptive charger and 'winter' always to the rule-based
        arbitrage planner. 'summer' (the plain solar-first planner, `build_plan`'s fallback when a
        caller omits the load profile) is kept as a valid value for completeness, even though this
        endpoint's live wiring never takes that path."""
        return "adaptive" if strategy == "summer" else "rule_based"

    def _plan_provenance(strategy: str) -> dict:
        """The plan-provenance line (CLAUDE.md honesty ask, feat/ux-batch-3): what is ACTUALLY
        steering today's plan — the forecast source, the solar_confidence dial, which planner
        function ran, and the scenario/ML intelligence layer's real (shadow, non-steering) status.
        No field here may overstate what's live: ems/intelligence/planning.py is pure and built, but
        unwired into live planning (see INTELLIGENCE_MODE)."""
        return {
            "forecast_source": _forecast_source_label(),
            "solar_confidence_pct": settings_cache["planner.solar_confidence"],
            "planner": _resolved_planner_name(strategy),
            "intelligence": INTELLIGENCE_MODE,
        }

    @app.get("/api/battery-plan")
    async def battery_plan() -> dict:
        """Homeowner-facing battery confidence contract: the answer first, then graph proof.

        This deliberately reuses the same projection/story helpers as /api/energy-forecast and
        /api/energy-story so the plan sentence, graph and diagnostics cannot drift apart.
        """
        now = datetime.now(UTC)
        reserve_pct = settings_cache["battery.min_reserve_soc"]
        quality = _data_quality(now)
        # Plan confidence (B-68): pure synthesis over signals already gathered elsewhere — the
        # data-quality badge above, per-signal freshness, battery reachability (all no-extra-cost
        # reuses of cached reads), plus the 14-day solar forecast skill (the one extra store read
        # this endpoint takes on, shared with /api/accuracy via _solar_forecast_skill).
        confidence = plan_confidence(
            data_quality=quality,
            forecast_skill=await _solar_forecast_skill(now),
            freshness_ok=_freshness_ok(now),
            battery_reachable=_battery_reachable(now),
        )
        fp = await _forward_projection()
        if fp is None:
            return {
                "status": "paused_safely",
                "summary": "Plan paused safely — no battery plan is available yet.",
                "current_action": "paused",
                "current_reason": "No current plan or forecast is available.",
                "window_start": now.isoformat(),
                "window_end": (now + timedelta(hours=24)).isoformat(),
                "current_soc_pct": None,
                "reserve_soc_pct": reserve_pct,
                "target_soc_pct": None,
                "target_deadline": None,
                "deviation": {"status": "missing", "message": "No forecast to compare yet."},
                "warnings": ["No plan is available yet."],
                "graph": {"forecast_soc": [], "actual_soc": [], "reserve_line": [],
                          "target_line": [], "planned_actions": [],
                          "price_windows": [], "solar": []},
                "confidence": confidence,
                # No plan exists yet, so there is no plan.strategy to read back — resolve it fresh
                # (idempotent: see _resolve_strategy/apply_hysteresis) just for the provenance line.
                "provenance": _plan_provenance(_active_strategy(now)),
            }

        projected, price_by, need, deadline = (
            fp["projected"], fp["price_by"], fp["need"], fp["deadline"]
        )
        slots = [
            _uslot(p.start, p.soc_pct, p.grid_w, p.solar_w, p.battery_w, p.load_w,
                   price_by.get(p.start), _action_from_intent(p.intent, p.battery_w))
            for p in projected
        ]
        totals = _uslot_totals(slots)
        recent = await _recent_actuals(fp["now"])
        grid_charge_kwh = totals["grid_charge_kwh"]
        _intent, reason, _override, _target, _power, validation, _ca = _effective_intent(fp["now"])

        # "Are we on track?" is derived from the ACTUAL plan (same engine as the story line), NOT a
        # compare of two actual SoC samples — so it measures the plan against target/reserve and
        # cannot false-alarm or sit dead. current_action mirrors the first planned slot so the chip
        # and the graph's action strip speak one vocabulary.
        verdict = _on_track(fp["current_soc"], need, totals, grid_charge_kwh, reserve_pct)
        deviation = {
            "status": "ok" if verdict["status"] in ("ahead", "on_track") else "behind_forecast",
            "message": verdict["message"],
            "actual_soc_pct": verdict["actual_soc_pct"],
            "target_soc_pct": verdict["target_soc_pct"],
        }
        current_action = slots[0]["action"] if slots else "paused"

        warnings: list[str] = []
        if quality == "unsafe":
            status = "data_stale"
            summary = "Data stale — EMS is paused safely until critical inputs are fresh again."
            current_action = "paused"
            current_reason = reason or "Critical sensor, price or forecast data is stale."
            warnings.append(current_reason)
            deviation = {"status": "missing",
                         "message": "Plan confidence is unavailable until fresh data returns."}
        elif validation is not None and not validation.ok:
            status = "paused_safely"
            finding = (validation.findings[0].message if validation.findings
                       else "Plan validation failed.")
            summary = f"Paused safely — {finding}"
            current_action = "paused"
            current_reason = finding
            warnings.append(finding)
            deviation = {"status": "missing", "message": finding}
        elif verdict["status"] == "behind" and grid_charge_kwh > 0.05:
            status = "needs_topup"
            summary = f"Needs top-up — {verdict['message']}"
            current_reason = reason or "Grid top-up is planned to reach the battery target."
        elif verdict["status"] == "behind":
            status = "behind_target"
            summary = f"Behind target — {verdict['message']}"
            current_reason = reason or "The battery is short of the night target."
            warnings.append(verdict["message"])
        else:
            status = "on_track"
            summary = _next_headline(totals, need, grid_charge_kwh)
            current_reason = reason or "Battery is following the current plan."

        # window_start must cover the recent ACTUAL history too, not just the forecast — otherwise
        # the actual-SoC line falls entirely left of the plotted domain (finding #2). recent is
        # oldest→now, slots is now→+24h, so the earliest sample is recent[0] when present.
        start = (recent[0]["start"] if recent else
                 (slots[0]["start"] if slots else fp["now"].isoformat()))
        end = _slot_end_iso(slots[-1]) if slots else (fp["now"] + timedelta(hours=24)).isoformat()
        target = round(need.target_soc_pct, 1)
        reserve = round(reserve_pct, 1)
        return {
            "status": status,
            "summary": summary,
            "current_action": current_action,
            "current_reason": current_reason,
            "window_start": start,
            "window_end": end,
            "current_soc_pct": round(fp["current_soc"], 1),
            "reserve_soc_pct": reserve,
            "target_soc_pct": target,
            "target_deadline": deadline.isoformat() if deadline is not None else None,
            "planned_grid_topup_kwh": round(grid_charge_kwh, 1),
            "deviation": deviation,
            "warnings": warnings,
            "graph": {
                "forecast_soc": [{"ts": s["start"], "soc_pct": s["soc_pct"]} for s in slots],
                "actual_soc": [{"ts": s["start"], "soc_pct": s.get("soc_pct")} for s in recent
                               if s.get("soc_pct") is not None],
                "reserve_line": [{"ts": start, "soc_pct": reserve},
                                 {"ts": end, "soc_pct": reserve}],
                "target_line": [{"ts": start, "soc_pct": target}, {"ts": end, "soc_pct": target}],
                "planned_actions": _action_blocks(slots),
                "price_windows": _planned_charge_windows(slots),
                "solar": [{"ts": s["start"], "forecast_w": s["solar_w"],
                           "actual_w": None} for s in slots],
            },
            "confidence": confidence,
            "provenance": _plan_provenance(fp["strategy"]),
        }

    async def _past_story(reserve_pct: float) -> dict:
        now = datetime.now(UTC)
        cutoff = (now - timedelta(hours=24)).isoformat()
        raw = await store.recent_raw_since(cutoff) if store is not None else []
        der = await store.recent_derived_since(cutoff) if store is not None else []
        prices = await _window_price_slots(cutoff, now.isoformat()) if store is not None else []
        story = build_past_story(raw, der, prices, now)
        slots = [
            _uslot(ps.start, ps.soc_pct, ps.grid_w, ps.solar_w, ps.battery_w, ps.load_w,
                   ps.eur_per_kwh,
                   # Split the charge by source using NON-EV (house-only) load: a concurrent car
                   # session must not crowd out the solar-vs-grid attribution (§4.5, retrospect).
                   _action_from_battery(ps.battery_w, ps.solar_w, ps.non_ev_load_w))
            for ps in story.slots
        ]
        if not slots:
            return _empty_story("past", reserve_pct, past_headline(story))
        # Show the night target that applied, as a reference line to validate against.
        need = _night_target_soc(story.soc_end_pct if story.soc_end_pct is not None else 50.0)
        return {
            "window": "past", "now": now.isoformat(),
            "current_soc_pct": story.soc_end_pct, "reserve_soc_pct": reserve_pct,
            "target_soc_pct": round(need.target_soc_pct, 1),
            "target_kwh": round(need.target_kwh, 1),
            "target_deadline": None,
            # The latest recorded price (most recent slot).
            "current_price_eur_per_kwh": slots[-1]["eur_per_kwh"] if slots else None,
            "slots": slots, "totals": _uslot_totals(slots), "headline": past_headline(story),
        }

    @app.get("/api/energy-story")
    async def energy_story(
        window: str = Query(default="next", pattern="^(past|next)$"),
    ) -> dict:
        # One shape, two directions: "next" = the plan/forecast; "past" = recorded last 24h. The
        # frontend renders both with the same timeline so the story reads consistently. Read-only.
        reserve_pct = settings_cache["battery.min_reserve_soc"]
        if window == "past":
            return await _past_story(reserve_pct)
        return await _next_story(reserve_pct)

    @app.get("/api/forecast")
    def forecast() -> dict:
        if solar_forecast is None:
            return {"today_kwh_p50": None, "source": None, "slots": []}
        slots = solar_forecast.slots()
        return {
            "today_kwh_p50": round(day_kwh_p50(slots), 2),
            # "forecast.solar" (live) | "model" / "model (fallback)" — so the UI is honest.
            "source": getattr(solar_forecast, "source_label", "model"),
            "slots": [
                {"start": s.start.isoformat(), "p10_w": s.p10_w, "p50_w": s.p50_w,
                 "p90_w": s.p90_w}
                for s in slots
            ],
        }

    @app.get("/api/energy-distribution")
    async def energy_distribution(date: str | None = None) -> dict:
        """A single LOCAL day's energy distribution (the Sankey view): where the day's energy came
        from and went, in kWh. Rolled up on demand from recorded history — NOT on the dashboard
        poll, and bounded to one day's rows, so it adds no device load (energy review: minimum
        load). `date` is YYYY-MM-DD in the site timezone; omitted = today."""
        now_local = datetime.now(UTC).astimezone(site_tz)
        if date:
            try:
                d = date_cls.fromisoformat(date)
            except ValueError:
                return JSONResponse(
                    {"detail": "date must be YYYY-MM-DD"}, status_code=422
                )  # type: ignore[return-value]
        else:
            d = now_local.date()
        day_start = datetime(d.year, d.month, d.day, tzinfo=site_tz)
        day_end = day_start + timedelta(days=1)
        partial = d == now_local.date()
        # No store, or a future day → an honest empty distribution (has_data False), never an error.
        if store is None or d > now_local.date():
            return build_daily_flows([], [], day_start, day_end,
                                     label=d.isoformat(), partial=partial).to_dict()
        s_iso, e_iso = day_start.astimezone(UTC).isoformat(), day_end.astimezone(UTC).isoformat()
        raw = await store.raw_between(s_iso, e_iso)
        der = await store.derived_between(s_iso, e_iso)
        return build_daily_flows(raw, der, day_start, day_end,
                                 label=d.isoformat(), partial=partial).to_dict()

    @app.get("/api/sky")
    async def sky() -> dict:
        """Today's sunrise/sunset (site tz) + current cloud cover for the time-of-day sky backdrop.
        Sun times are pure math (nulls if location unset / polar → UI falls back to clock phases).
        Cloud cover is best-effort from Open-Meteo, cached ≤15 min and fetched off the event loop;
        None when unavailable (offline/sim) → the sky just shows clear. Read-only."""
        now = datetime.now(UTC)
        lat, lon = settings_cache.get("site.lat"), settings_cache.get("site.lon")
        sunrise = sunset = None
        cloud_cover = _sky_box["cc"]
        try:
            if lat is not None and lon is not None:
                lat_f, lon_f = float(lat), float(lon)
                sr, ss = sun_times(lat_f, lon_f, now.astimezone(site_tz).date(), site_tz)
                sunrise = sr.isoformat() if sr else None
                sunset = ss.isoformat() if ss else None
                last = _sky_box["at"]
                if last is None or (now - last).total_seconds() > 900:
                    cloud_cover = await asyncio.to_thread(cloud_cover_pct, lat_f, lon_f)
                    _sky_box["cc"], _sky_box["at"] = cloud_cover, now
        except (TypeError, ValueError):
            _log.debug("sky: sun/cloud lookup failed (non-fatal)", exc_info=True)
        return {"now": now.isoformat(), "sunrise": sunrise, "sunset": sunset,
                "cloud_cover": cloud_cover}

    async def _report_for_window(
        period: str, start: datetime, end: datetime, label: str, partial: bool,
        now_local: datetime,
    ) -> dict:
        """The energy-flow distribution + the three scores (self-consumption, CO₂, best-price)
        for an ALREADY-RESOLVED window — the shared body behind `/api/report` and the weekly
        digest gather (BACKLOG B-58), which needs exactly this rollup for a week window without
        going through HTTP. `now_local` bounds queries to "not the future" and gates the empty-
        report fast path; the caller supplies it so a digest job computing several things off one
        `now` never risks a several-datetimes-now() race."""
        grid_factor = float(settings_cache.get("reporting.grid_co2_factor", 0.27))
        gas_factor = float(settings_cache.get("reporting.gas_co2_factor", 1.78))
        gas_price = float(settings_cache.get("reporting.gas_price_eur_per_m3", 1.40))
        # Roadmap F3: use the window's live grid-CO2 average when the recorder has persisted any
        # (else stay on the flat factor above — unchanged behaviour without the live signal).
        live_grid_factor, grid_factor_note = await _window_carbon_factor(
            start.astimezone(UTC).isoformat(), end.astimezone(UTC).isoformat())
        if live_grid_factor is not None:
            grid_factor = live_grid_factor
        # Stored prices so the best-price score is right for HISTORICAL windows too, not just
        # whatever the live feed still carries.
        prices = await _window_price_slots(start.astimezone(UTC).isoformat(),
                                           end.astimezone(UTC).isoformat())
        # Future / no store → an honest empty report (has_data False), never an error.
        if store is None or start > now_local:
            resp = build_report([], [], prices, period=period, start=start, end=end, label=label,
                                partial=partial, grid_factor=grid_factor,
                                gas_factor=gas_factor, grid_factor_note=grid_factor_note).to_dict()
            resp["series"] = build_series([], [], period=period, start=start, end=end, tz=site_tz)
            resp["gas"] = None
            return resp
        q_end = min(end, now_local + timedelta(minutes=1))  # never query the future
        # Size the row cap to the window AND the recorder cadence (finding 10) so week/month/year
        # aren't truncated and it stays correct if the sampling frequency changes.
        limit = history_row_cap((end - start).total_seconds(), _sample_cadence_seconds())
        raw = await store.raw_between(start.astimezone(UTC).isoformat(),
                                      q_end.astimezone(UTC).isoformat(), limit=limit)
        der = await store.derived_between(start.astimezone(UTC).isoformat(),
                                          q_end.astimezone(UTC).isoformat(), limit=limit)
        gas_rows = await store.gas_between(start.astimezone(UTC).isoformat(),
                                           q_end.astimezone(UTC).isoformat())
        gas = gas_m3_consumed(gas_rows)
        # Year view pre-aggregation (BACKLOG B-49 §4): the SERIES bucket comes from the never-
        # purged daily_energy rollup (~365 rows) instead of re-iterating the window's raw/derived
        # rows a SECOND time (build_report's flows already do that once — the Sankey attribution
        # can't be reconstructed from daily totals, so flows/scores keep the raw path unchanged).
        daily_rows = None
        if period == "year":
            daily_rows = await store.daily_energy_between(
                start.date().isoformat(), end.date().isoformat())

        def _assemble() -> dict:
            # CPU assembly (up to ~200k rows for a year) off the event loop (item 3), mirroring
            # _forward_projection's to_thread pattern — build_report/build_series/gas_summary are
            # all pure functions over plain data, safe to run in a worker thread.
            resp = build_report(raw, der, prices, period=period, start=start, end=end, label=label,
                                partial=partial, grid_factor=grid_factor,
                                gas_factor=gas_factor, gas_m3=gas,
                                grid_factor_note=grid_factor_note).to_dict()
            if daily_rows is not None:
                resp["series"] = build_series_from_daily_energy(
                    daily_rows, start=start, end=end, tz=site_tz)
                # F2: the year SERIES is already full-year (rollup) but flows/scores were built from
                # the (retention-bounded) raw window — recompute the totals-derivable scores from
                # the same full-year rollup and label the raw-window best_price + flows caption.
                apply_year_totals(resp, daily_rows, grid_factor=grid_factor,
                                  gas_factor=gas_factor, raw_days=history_retention_days)
            else:
                resp["series"] = build_series(raw, der, period=period, start=start, end=end,
                                              tz=site_tz)
            # Makes gas VISIBLE (beyond folding into the CO₂ score): m³/kWh-eq/€/CO₂ for the
            # Insights gas panel. None-safe — the panel hides itself with <2 gas readings.
            resp["gas"] = gas_summary(gas_rows, price_eur_per_m3=gas_price, co2_factor=gas_factor)
            return resp

        return await asyncio.to_thread(_assemble)

    @app.get("/api/report")
    async def report(
        period: str = Query(default="day", pattern="^(day|week|month|year)$"),
        date: str | None = None,
    ) -> dict:
        """Insights: the energy-flow distribution + the three scores (self-consumption, CO₂,
        best-price) over a day/week/month/year window. Rolled up on demand from recorded history —
        off the dashboard poll, bounded to the window's rows, read-only. `date` (YYYY-MM-DD, site
        tz) is any day inside the window; omitted = the current period."""
        now_local = datetime.now(UTC).astimezone(site_tz)
        if date:
            try:
                anchor = date_cls.fromisoformat(date)
            except ValueError:
                return JSONResponse(  # type: ignore[return-value]
                    {"detail": "date must be YYYY-MM-DD"}, status_code=422)
        else:
            anchor = now_local.date()
        start, end, label, partial = resolve_window(period, anchor, site_tz, now_local)
        return await _report_for_window(period, start, end, label, partial, now_local)

    async def _solar_confidence_advice(now: datetime) -> dict | None:
        """Advisory-only recommendation for `planner.solar_confidence`, derived from how the
        stored day-ahead forecast has actually performed over the last 14 days (SPEC: solar
        confidence should come from evidence, not a hand-tuned guess). None with no store. Shared
        by `/api/advisor/solar-confidence` and the weekly digest gather (BACKLOG B-58) — one
        recommendation, reused, never applied automatically anywhere; the human decides.

        Reads the prediction ledger's CANONICAL solar rows (design §4.2/§4.3) — same single
        scoring source as `_solar_forecast_skill`, so this advisor and the accuracy/health surfaces
        can never disagree about how the day-ahead forecast has actually performed."""
        if store is None:
            return None
        start = now - timedelta(days=14)
        start_iso, end_iso = start.isoformat(), now.isoformat()
        limit = history_row_cap((now - start).total_seconds(), _sample_cadence_seconds())
        raw = await store.raw_between(start_iso, end_iso, limit=limit)
        forecasts = await store.ledger_canonical_between("solar", start_iso, end_iso)
        current = settings_cache.get("planner.solar_confidence")
        return recommend_solar_confidence(
            forecasts, raw, current_pct=float(current) if current is not None else None)

    @app.get("/api/advisor/ev-charge")
    def advisor_ev_charge() -> dict:
        """Advisory-only "best time to charge the car" (docs/v2-ev-control.md: v2 EV control is
        out of scope — this never commands anything, just recommends a window). Off unless
        `ev.advice_enabled` is on; needs live prices. Reuses the same price/forecast access as
        /api/plan (price_source.slots() + solar_forecast.slots()).

        DEPRECATED: superseded by GET /api/car/plan (schedule-aware, multi-deadline, SoC-anchored).
        Kept unchanged for compatibility with any existing client; do not extend it."""
        if not settings_cache.get("ev.advice_enabled") or price_source is None:
            return {"advice": None}
        now = datetime.now(UTC)
        now_local = now.astimezone(site_tz)
        try:
            hh, mm = (int(x) for x in str(settings_cache["ev.departure_time"]).split(":", 1))
            assert 0 <= hh < 24 and 0 <= mm < 60
        except (ValueError, AssertionError):
            _log.debug("invalid ev.departure_time; using 07:30 (non-fatal)", exc_info=True)
            hh, mm = 7, 30  # fail-safe: an unparsable time never breaks the card
        departure_local = now_local.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if departure_local <= now_local:
            departure_local += timedelta(days=1)
        forecast = solar_forecast.slots() if solar_forecast is not None else []
        advice = advise_charge_window(
            price_source.slots(),
            {f.start: f.p50_w for f in forecast},
            departure=departure_local,  # kept in site_tz so the reason shows local wall-clock time
            kwh_needed=float(settings_cache["ev.charge_kwh"]),
            charger_kw=float(settings_cache["ev.charger_kw"]),
            export_model=str(settings_cache.get("prices.export_price_model", "net_metering")),
            energy_tax_eur_per_kwh=float(settings_cache.get("prices.energy_tax_eur_per_kwh", 0.13)),
            fixed_feed_in_eur_per_kwh=float(
                settings_cache.get("prices.fixed_feed_in_eur_per_kwh", 0.01)),
            now=now,
        )
        return {"advice": advice}

    async def _ensure_day_finance(day_local: date_cls) -> dict:
        """Compute (or return the current-version cached) finance rollup for one LOCAL day,
        persisting it once the day is completed. Shared by `/api/finance` (called per day of the
        viewed window) and the export package (which backfills every completed day in ITS window,
        so `daily_finance.csv` covers days no one ever viewed — not just previously-cached ones).
        `store` must not be None (both call sites already guard that)."""
        now_local = datetime.now(UTC).astimezone(site_tz)
        day_label = day_local.isoformat()
        cur = datetime(day_local.year, day_local.month, day_local.day, tzinfo=site_tz)
        nxt = cur + timedelta(days=1)
        completed = nxt <= now_local
        if completed:
            cached = await store.daily_finance_between(day_label, nxt.date().isoformat())
            # Only trust a cache entry written by the CURRENT finance formula; a day cached
            # under an older version is recomputed (re-upserted) so a math fix reaches history.
            if cached and cached[0]["data"].get("calc_v") == _FINANCE_CALC_VERSION:
                return cached[0]["data"]
        degradation = float(settings_cache.get("planner.degradation_eur_per_kwh", 0.05))
        export_model = str(settings_cache.get("prices.export_price_model", "net_metering"))
        energy_tax = float(settings_cache.get("prices.energy_tax_eur_per_kwh", 0.13))
        fixed_feed_in = float(settings_cache.get("prices.fixed_feed_in_eur_per_kwh", 0.01))
        q_end = min(nxt, now_local + timedelta(minutes=1))
        # Cadence-aware per-day cap (finding 10): sized to the recorder frequency, not a fixed
        # 3000 that would truncate a finer sampling rate.
        day_limit = history_row_cap((nxt - cur).total_seconds(), _sample_cadence_seconds())
        raw = await store.raw_between(cur.astimezone(UTC).isoformat(),
                                      q_end.astimezone(UTC).isoformat(), limit=day_limit)
        price_rows = await store.prices_between(cur.astimezone(UTC).isoformat(),
                                                nxt.astimezone(UTC).isoformat())
        f = day_finance(raw, price_rows, day=day_label,
                        degradation_eur_per_kwh=degradation,
                        export_price_model=export_model,
                        energy_tax_eur_per_kwh=energy_tax,
                        fixed_feed_in_eur_per_kwh=fixed_feed_in).to_dict()
        f["calc_v"] = _FINANCE_CALC_VERSION
        if completed:
            await store.upsert_daily_finance(day_label, f)
        return f

    async def _finance_window(start: datetime, end: datetime, now_local: datetime) -> list[dict]:
        """Batched replacement for calling `_ensure_day_finance` once per day (BACKLOG B-49): the
        old loop did up to 365 round trips for a year view (raw + prices + cached-lookup, PER
        day). Here the whole window's raw rows / price slots / cached daily_finance rows are each
        fetched in ONE round trip, sliced back into per-day inputs in memory
        (`raw_rows_by_local_day` / `price_rows_by_local_day`), and the per-day `day_finance()` math
        + cache-guard decision runs OFF the event loop in a worker thread (item 3: CPU that used to
        run inline for up to 365 days). Only days that actually need (re)computing are upserted —
        same calc_v cache-guard contract as `_ensure_day_finance`, which stays UNCHANGED (and is
        still used, one day at a time, by the export package for arbitrary/non-contiguous days)."""
        q_end = min(end, now_local + timedelta(minutes=1))
        limit = history_row_cap((end - start).total_seconds(), _sample_cadence_seconds())
        raw = await store.raw_between(start.astimezone(UTC).isoformat(),
                                      q_end.astimezone(UTC).isoformat(), limit=limit)
        price_rows = await store.prices_between(start.astimezone(UTC).isoformat(),
                                                end.astimezone(UTC).isoformat())
        cached = await store.daily_finance_between(start.date().isoformat(), end.date().isoformat())
        cached_by_day = {c["day"]: c["data"] for c in cached}
        raw_by_day = raw_rows_by_local_day(raw, start, end, site_tz)
        price_by_day = price_rows_by_local_day(price_rows, start, end, site_tz)

        degradation = float(settings_cache.get("planner.degradation_eur_per_kwh", 0.05))
        export_model = str(settings_cache.get("prices.export_price_model", "net_metering"))
        energy_tax = float(settings_cache.get("prices.energy_tax_eur_per_kwh", 0.13))
        fixed_feed_in = float(settings_cache.get("prices.fixed_feed_in_eur_per_kwh", 0.01))

        def _compute() -> list[tuple[str, dict, bool]]:
            out: list[tuple[str, dict, bool]] = []
            cur = start
            while cur < end and cur <= now_local:
                day_label = cur.date().isoformat()
                nxt = cur + timedelta(days=1)
                completed = nxt <= now_local
                cached_data = cached_by_day.get(day_label)
                if completed and cached_data is not None \
                        and cached_data.get("calc_v") == _FINANCE_CALC_VERSION:
                    out.append((day_label, cached_data, False))
                else:
                    f = day_finance(
                        raw_by_day.get(day_label, []), price_by_day.get(day_label, []),
                        day=day_label, degradation_eur_per_kwh=degradation,
                        export_price_model=export_model, energy_tax_eur_per_kwh=energy_tax,
                        fixed_feed_in_eur_per_kwh=fixed_feed_in,
                    ).to_dict()
                    f["calc_v"] = _FINANCE_CALC_VERSION
                    out.append((day_label, f, completed))
                cur = nxt
            return out

        computed = await asyncio.to_thread(_compute)
        days: list[dict] = []
        for day_label, data, needs_upsert in computed:
            if needs_upsert:
                await store.upsert_daily_finance(day_label, data)
            days.append(data)
        return days

    @app.get("/api/finance")
    async def finance(
        period: str = Query(default="day", pattern="^(day|week|month|year)$"),
        date: str | None = None,
    ) -> dict:
        """Financial history (spec 2026-07-03 B): per LOCAL day — what the grid cost, what the
        battery cost in wear, and what the EMS saved vs the no-battery baseline — measured from
        recorded samples + stored prices, never from the plan. Completed days are computed once
        and persisted (`daily_finance`, retention-proof); the running day is always fresh."""
        now_local = datetime.now(UTC).astimezone(site_tz)
        if date:
            try:
                anchor = date_cls.fromisoformat(date)
            except ValueError:
                return JSONResponse(  # type: ignore[return-value]
                    {"detail": "date must be YYYY-MM-DD"}, status_code=422)
        else:
            anchor = now_local.date()
        start, end, label, partial = resolve_window(period, anchor, site_tz, now_local)
        days = await _finance_window(start, end, now_local) if store is not None else []

        def _sum(key: str) -> float | None:
            vals = [d[key] for d in days if d.get(key) is not None]
            return round(sum(vals), 2) if vals else None

        totals = {
            "grid_cost_eur": _sum("grid_cost_eur"),
            "battery_cost_eur": _sum("battery_cost_eur"),
            "saved_eur": _sum("saved_eur"),
            "grid_import_kwh": _sum("grid_import_kwh") or 0.0,
            "grid_export_kwh": _sum("grid_export_kwh") or 0.0,
            "days_with_prices": sum(1 for d in days if d.get("price_coverage", 0) > 0),
            "days_with_data": sum(1 for d in days if d.get("has_data")),
        }
        return {"period": period, "label": label,
                "window_start": start.astimezone(UTC).isoformat(),
                "window_end": end.astimezone(UTC).isoformat(),
                "partial": partial, "days": days, "totals": totals}

    @app.get("/api/series")
    async def series(limit: int = Query(default=100, ge=1, le=2000)) -> dict:
        if store is None:
            return {"raw": [], "derived": []}
        return {
            "raw": await store.recent_raw(limit),
            "derived": await store.recent_derived(limit),
        }

    @app.get("/api/settings")
    def get_settings() -> dict:
        # The UI renders a form from `schema` and fills it from `values` (effective config).
        # Secrets are masked (a parallel "<key>.__set" flag tells the UI whether one is stored).
        return {"schema": schema_json(), "values": public_values(settings_cache)}

    @app.post("/api/settings")
    async def post_settings(request: Request, body: dict | None = None) -> JSONResponse:
        # Auth is enforced centrally by the _enforce_access middleware (writes always gated).
        if settings_store is None:
            return JSONResponse(
                {"detail": "settings store not configured"}, status_code=503
            )
        # Pass body straight through: validate_settings guards non-dict, so a missing/None body
        # becomes a 422 ("expected a JSON object") rather than a silent 200 no-op.
        clean, errors = validate_settings(body)
        if errors:
            # Reject the whole payload if ANY key is invalid — partial saves are confusing.
            return JSONResponse(
                {"detail": "invalid settings", "errors": errors}, status_code=422
            )
        await settings_store.set_many(clean)
        # In-place update ONLY (never clear()+update): the effective set is always the full keyset,
        # so a concurrent threadpool GET never observes a missing key (the KeyError/500 race).
        settings_cache.update(effective_settings(await settings_store.all()))
        _apply_control_settings()
        _apply_site_settings()
        _apply_explainer_settings()
        await _apply_battery_power_settings()
        if audit_store is not None and clean:
            # Record WHICH settings changed — keys only, never values (so a token/secret is never
            # written to the audit log). Secret keys are flagged so the entry reads sensibly.
            keys = sorted(clean)
            await audit_store.append(
                datetime.now(UTC).isoformat(), "config_change",
                f"Changed {len(keys)} setting(s): {', '.join(keys)}",
                {"keys": keys, "secrets": sorted(k for k in keys if k in SECRET_KEYS)},
            )
        # Tell the caller if any saved key needs a restart to take effect (connection / operational
        # mode are read at startup) — so the UI never implies operational control is live when it
        # isn't. Mask secrets in the response exactly like GET — never echo a stored token back.
        restart_required = any(
            k in SETTINGS_BY_KEY and SETTINGS_BY_KEY[k].applies == "restart" for k in clean
        )
        return JSONResponse(
            {"values": public_values(dict(settings_cache)), "restart_required": restart_required}
        )

    @app.get("/api/status")
    def status() -> dict:
        # Coalesced read (shared 30 s window) so the 5–10 s dashboard poll doesn't read the battery
        # cluster on every refresh. Fall back to a direct read only if nothing's cached yet (cold
        # start) — preserving the original "unreadable source surfaces an error" behaviour.
        raw = _current_sample(datetime.now(UTC)) or source.read()
        derived = reconstruct(raw)
        return {
            "dry_run": dry_run,
            "dev_mode": dev_mode,
            "soc_pct": raw.soc_pct,
            "grid_power_w": raw.grid_power_w,
            "solar_power_w": raw.solar_power_w,
            "battery_power_w": raw.battery_power_w,
            "house_load_w": derived.house_load_w,
            "non_ev_load_w": derived.non_ev_load_w,
        }

    @app.get("/api/audit")
    async def audit_endpoint(
        limit: int = Query(default=100, ge=1, le=500), category: str | None = None,
    ) -> dict:
        """The audit trail: every plan/battery-mode decision, config change and manual override —
        newest first. Read-only. Empty when no audit store is configured."""
        if audit_store is None:
            return {"entries": []}
        return {"entries": await audit_store.recent(limit, category)}

    @app.get("/api/incidents")
    async def incidents_endpoint() -> dict:
        """Control-health incidents (command failures, cluster mismatches, fallbacks, reverts)
        rolled up from the audit log — the same read `/api/export/package` embeds in the manifest,
        so the System page can show it without downloading the export. Read-only."""
        if audit_store is None:
            return {"incidents": expkg.incident_rollup([])}
        return {"incidents": expkg.incident_rollup(await audit_store.recent(limit=5000))}

    @app.get("/api/ai/validation")
    def ai_validation_latest() -> dict:
        """The latest AI second-opinion (advisory), for the dashboard. null until one has run."""
        return {"latest": validation_box["latest"], "active": _explainer_active()}

    @app.post("/api/ai/validate")
    async def ai_validate_now(request: Request) -> JSONResponse:
        """Run an AI second-opinion on demand (the dashboard's "check now"). Advisory; off → 200
        with latest=null. Auth-gated like other writes (via _enforce_access); never 500s."""
        try:
            result = await _run_validation()
        except Exception:
            _log.debug("on-demand AI validation failed (non-fatal)", exc_info=True)
            result = None
        return JSONResponse({"latest": result, "active": _explainer_active()})

    @app.get("/api/explainer")
    def explainer_status() -> dict:
        """Whether AI explanations/chat are active, for the UI to show state + gate the chat."""
        return {
            "mode": settings_cache.get("explainer.mode", "template"),
            "active": _explainer_active(),
            "language": settings_cache.get("explainer.language", "English"),
        }

    @app.get("/api/faq")
    def faq_endpoint() -> dict:
        """Grounded, DETERMINISTIC answers to the few questions a homeowner actually asks — built
        from the current decision/readiness/plan, NOT the AI (emotional review #8). Works with AI
        off, so 'Is my battery safe?' always has an answer. Every block is defensive."""
        now = datetime.now(UTC)
        items: list[dict] = []
        try:
            rd = _readiness(now)
            safe = rd.summary
            if dry_run:
                safe += " EMS is read-only here — it can't command the battery."
            items.append({"key": "battery_safe", "question": "Is my battery safe?", "answer": safe})
        except Exception:
            _log.debug("faq: 'is my battery safe?' item failed (non-fatal)", exc_info=True)
        try:
            intent, reason, *_ = _effective_intent(now)
            if intent is not None and reason:
                items.append({"key": "why_mode", "question": "Why is it in this mode?",
                              "answer": reason})
        except Exception:
            _log.debug("faq: 'why this mode?' item failed (non-fatal)", exc_info=True)
        try:
            need = _night_target_soc(_current_soc(now))
            items.append({"key": "tonight", "question": "What happens tonight?",
                          "answer": need.reason})
        except Exception:
            _log.debug("faq: 'what happens tonight?' item failed (non-fatal)", exc_info=True)
        try:
            if settings_cache.get("ev.advice_enabled"):
                car = car_by_id(str(settings_cache.get("ev.car_id") or ""))
                subject = f"Your {car.brand} {car.model}" if car is not None else "The car"
                items.append({
                    "key": "why_charge_car",
                    "question": "Why should I charge the car then?",
                    "answer": (
                        "The car card works out the cheapest way to hit each day's scheduled "
                        "minimum by its ready-by time: it buys the cheapest priced slots before "
                        "each deadline, and when solar is forecast in surplus during a slot, that "
                        "slot only costs what the surplus would otherwise have earned feeding in "
                        f"(often far cheaper, sometimes free). {subject}'s SoC is estimated from "
                        "the % you last anchored plus what the car meter has measured charging "
                        "since — driving isn't modeled, so re-anchor after a trip to keep the "
                        "estimate honest."
                    ),
                })
        except Exception:
            _log.debug("faq: 'why charge the car?' item failed (non-fatal)", exc_info=True)
        return {"items": items, "ai_on": _explainer_active()}

    @app.post("/api/chat")
    async def chat_endpoint(request: Request) -> JSONResponse:
        """Ask the assistant about the current decisions/dashboard. Grounded ONLY on a redacted
        snapshot (_chat_context); advisory, never touches control. Off → a friendly nudge to enable
        it. Any failure degrades to a safe message, never a 500."""
        # Auth is enforced centrally by the _enforce_access middleware (writes always gated).
        try:
            data = await request.json()
        except Exception:
            _log.debug("chat: request body parse failed (non-fatal)", exc_info=True)
            data = {}
        question = (data.get("question") or "").strip()[:500] if isinstance(data, dict) else ""
        if not question:
            return JSONResponse({"detail": "empty question"}, status_code=400)
        if not _explainer_active():
            return JSONResponse({
                "answer": "AI chat is off. Turn on AI explanations in Settings to use it.",
                "source": "disabled",
            })
        try:
            out = await asyncio.to_thread(explainer_box["ex"].chat, question, _chat_context())
            return JSONResponse({"answer": out.text, "source": out.source})
        except Exception:
            _log.exception("chat failed")
            return JSONResponse(
                {"answer": "Sorry — the assistant isn't available right now.", "source": "error"}
            )

    # --- Extracted per-domain routers (BACKLOG B-25, incremental slice) --------------------------
    # Build the shared context ONCE (all its helper closures are defined above) and include each
    # self-contained domain's APIRouter. `settings_cache` is passed by reference (mutated in place,
    # never rebound) so a router sees a just-saved setting immediately, exactly like the closures.
    # Included BEFORE the /api/{rest} catch-all below so the specific routes match first. AUTH is
    # unchanged: their paths (incl. the writes /api/car/soc + /api/notifications/read) are still
    # gated by _AccessMiddleware via _WRITE_API_PATHS / _read_auth_required — moving a handler into
    # a router does not change its path.
    ctx = AppContext(
        source=source,
        store=store,
        settings_cache=settings_cache,
        audit_store=audit_store,
        cache_store=cache_store,
        notifier=notifier,
        price_source=price_source,
        solar_forecast=solar_forecast,
        recorder=recorder,
        site_tz=site_tz,
        tz=tz,
        dry_run=dry_run,
        dev_mode=dev_mode,
        replay_setting_keys=_REPLAY_SETTING_KEYS,
        car_charging=_car_charging,
        data_quality=_data_quality,
        sample_cadence_seconds=_sample_cadence_seconds,
        capability_present=lambda: _capability_box["cap"] is not None,
        ensure_day_finance=_ensure_day_finance,
        solar_forecast_skill=_solar_forecast_skill,
        solar_confidence_advice=_solar_confidence_advice,
        report_for_window=_report_for_window,
    )
    for build in (build_car_router, build_digest_router, build_notify_router,
                  build_export_router, build_accuracy_router, build_whatif_router):
        app.include_router(build(ctx))

    # Unknown /api/* paths must return a JSON 404 — NOT fall through to the SPA catch-all
    # below (which would serve index.html with a 200, silently breaking API clients).
    # Registered routes above are matched first; this only catches the rest under /api.
    @app.api_route("/api/{rest:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
    def api_not_found(rest: str) -> JSONResponse:
        return JSONResponse({"detail": f"/api/{rest} not found"}, status_code=404)

    # Serve the built React/Vite SPA (no runtime CDN). Mounted LAST so /api and /health
    # routes are matched first; html=True serves index.html at "/".
    if static_dir is not None:
        dist = Path(static_dir)
        if (dist / "index.html").exists():
            app.mount("/", StaticFiles(directory=dist, html=True), name="spa")

    return app
