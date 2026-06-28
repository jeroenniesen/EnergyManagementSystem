"""Read-only status API (SPEC §9.1). No device writes in M0a."""
from __future__ import annotations

import asyncio
import csv
import hashlib
import io
import json
import logging
import secrets
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Query, Request
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from ems.alerts import data_quality, derive_alerts
from ems.control.failsafe import failsafe_intent
from ems.control.loop import ControlLoop
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
from ems.diagnostics import build_diagnostics, overall_status
from ems.domain import BatteryIntent
from ems.freshness import FreshnessTracker
from ems.lifecycle import OwnershipState
from ems.load_model import reconstruct
from ems.planner.adaptive import AdaptiveConfig
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
from ems.planner.rule_based import PlannerConfig, plan_rule_based
from ems.planner.strategy import build_plan, select_strategy
from ems.planner.summer import SummerConfig, sunset_after
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
from ems.sources.base import Source
from ems.sources.battery import BatteryDriver
from ems.sources.forecast import SolarForecastSource, day_kwh_p50
from ems.sources.indevolt import aggregate_soc
from ems.sources.prices import PriceSource, current_price
from ems.storage.audit import AuditStore
from ems.storage.cache import CacheStore
from ems.storage.history import DERIVED_COLUMNS, RAW_COLUMNS, HistoryStore
from ems.storage.settings import SettingsStore

_log = logging.getLogger("ems.recorder")


def _task_died(name: str):
    def _cb(task: asyncio.Task) -> None:
        # Background tasks are awaited only at shutdown; surface an unexpected death immediately.
        if not task.cancelled() and (exc := task.exception()) is not None:
            _log.error("%s task exited unexpectedly: %s", name, exc, exc_info=exc)

    return _cb


_recorder_died = _task_died("Recorder")


def _explain_cache_key(reason: str, language: str, model: str) -> str:
    """Stable persistent-cache key for a phrased explanation. Includes model + language so changing
    either refreshes; the (possibly long) reason is hashed to a fixed-size key. No secrets enter it
    — only the deterministic reason, language and model name."""
    raw = f"{model}|{language}|{reason}".encode()
    return "explain:" + hashlib.sha256(raw).hexdigest()


# Live meter/SoC reads are deliberately NEVER served from the persistent external cache (Tibber /
# Forecast.Solar / AI) — they must always reflect the hardware. The only caching applied to a live
# read is this short *in-memory* coalescing window, so a single dashboard refresh that fans out to
# several endpoints reads the (slow) battery cluster + meters once instead of many times. It is lost
# on restart (so the first read after a restart is always fresh) and is intentionally brief.
_LIVE_SAMPLE_COALESCE_SECONDS = 30.0

# --- Unified energy-story slot/totals (shared by the past + next windows so they never drift) ---
_INTENT_ACTION = {
    "grid_charge_to_target": "charge",
    "discharge_for_load": "discharge",
    "hold_reserve": "hold",
    "allow_self_consumption": "self_consume",
}


def _action_from_intent(intent: object) -> str:
    return _INTENT_ACTION.get(str(intent), "self_consume")


def _action_from_battery(battery_w: float) -> str:
    # What the battery actually did this slot (+discharge / −charge); a small dead-band = idle.
    if battery_w < -50.0:
        return "charge"
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
        "discharge_kwh": round(sum(kwh(max(0.0, s["battery_w"])) for s in slots), 2),
        "load_kwh": round(load, 2),
        "grid_cost_eur": cost_eur if priced else None,
        "self_sufficiency_pct": round(ss, 1) if ss is not None else None,
        "soc_start_pct": socs[0] if socs else None,
        "soc_end_pct": socs[-1] if socs else None,
        "soc_min_pct": min(socs) if socs else None,
        "soc_max_pct": max(socs) if socs else None,
    }


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
    web_auth_token: str | None = None,
    static_dir: str | Path | None = None,
) -> FastAPI:
    def _authorized(request: Request) -> bool:
        """True if the request may mutate. When no token is configured, writes are open (dev/LAN
        default); otherwise an `Authorization: Bearer <token>` must match (constant-time)."""
        if web_auth_token is None:
            return True
        scheme, _, token = request.headers.get("authorization", "").partition(" ")
        try:
            return scheme == "Bearer" and secrets.compare_digest(token, web_auth_token)
        except TypeError:
            # compare_digest raises on non-ASCII str; treat as a clean 401, never a 500.
            return False

    def _auth_error() -> JSONResponse:
        return JSONResponse({"detail": "unauthorized — set an access token"}, status_code=401)
    # In-memory effective-settings cache (defaults until the store loads in lifespan). Sync
    # endpoints read this; POST /api/settings refreshes it. Mutated in place (never rebound).
    settings_cache: dict[str, Any] = effective_settings({})
    # Current manual override, cached in memory (expiry is evaluated per request). Mutated in
    # place via the "ov" key so the closure stays valid; loaded in lifespan, set by POST /override.
    override_box: dict[str, Override] = {"ov": OVERRIDE_NONE}
    _OV_INTENT, _OV_EXP = "override.intent", "override.expires_at"

    def _apply_control_settings() -> None:
        """Push the control.* settings onto the live controller (preserves its switch counters)."""
        if controller is None:
            return
        controller.max_switches_per_day = settings_cache["control.max_switches_per_day"]
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
                        hit = None
                    if hit:
                        try:
                            return json.loads(hit)
                        except (ValueError, TypeError):
                            pass  # corrupt entry → fall through and regenerate
                try:
                    expl = await asyncio.to_thread(explainer_box["ex"].explain, reason, facts)
                    out = {"text": expl.text, "source": expl.source}
                except Exception:
                    return {"text": reason, "source": "template"}
                if cache_store is not None and out["source"] == "external_llm":
                    ttl = float(settings_cache.get("explainer.cache_hours", 168.0)) * 3600.0
                    if ttl > 0:
                        try:
                            await asyncio.to_thread(
                                cache_store.set, ckey, json.dumps(out), ttl
                            )
                        except Exception:
                            pass  # cache write is best-effort; never fail the request over it
                return out
            cache[key] = asyncio.ensure_future(_run())
        return await cache[key]

    def _planner_cfg_from(s: dict) -> PlannerConfig:
        return PlannerConfig(
            round_trip_efficiency=s["planner.round_trip_efficiency"],
            degradation_eur_per_kwh=s["planner.degradation_eur_per_kwh"],
            risk_margin_eur_per_kwh=s["planner.risk_margin_eur_per_kwh"],
            charge_slots=s["planner.charge_slots"],
            discharge_slots=s["planner.discharge_slots"],
        )

    def _planner_cfg() -> PlannerConfig:
        return _planner_cfg_from(settings_cache)

    site_tz = tz or ZoneInfo("UTC")
    # See _LIVE_SAMPLE_COALESCE_SECONDS: a short in-memory window so one dashboard refresh reads the
    # hardware once. Meter/SoC data is never put in the persistent external cache.
    _sample_cache: dict[str, Any] = {"sample": None, "at": None}

    def _current_sample(now: datetime):
        cached_at = _sample_cache["at"]
        if (cached_at is not None
                and (now - cached_at).total_seconds() < _LIVE_SAMPLE_COALESCE_SECONDS
                and _sample_cache["sample"] is not None):
            return _sample_cache["sample"]
        try:
            _sample_cache["sample"], _sample_cache["at"] = source.read(), now
        except Exception:
            pass  # keep the last good sample (fail-safe)
        return _sample_cache["sample"]

    def _current_soc(now: datetime) -> float:
        s = _current_sample(now)
        return float(s.soc_pct) if s is not None else 0.0

    def _car_charging(now: datetime) -> bool:
        s = _current_sample(now)
        return s is not None and float(s.ev_power_w) > settings_cache[
            "control.car_charging_threshold_w"]

    def _car_guard(now: datetime, intent, reason):
        """Never feed the car from the home battery: while the car is charging, force any
        discharging intent to HOLD (the battery holds / may still charge from solar; solar + grid
        cover the car). GRID_CHARGE/HOLD pass through. Re-evaluated every cycle, so it engages as
        soon as the car plugs in and releases when it stops."""
        if (intent is None or not settings_cache["control.hold_battery_when_car_charging"]
                or not _car_charging(now)):
            return intent, reason
        if intent in (BatteryIntent.DISCHARGE_FOR_LOAD, BatteryIntent.ALLOW_SELF_CONSUMPTION):
            return (BatteryIntent.HOLD_RESERVE,
                    "car charging — holding the battery so it won't discharge into the car "
                    "(solar + grid cover the car)")
        return intent, reason

    def _night_target_soc(soc_pct: float):
        """The night-carry target (overnight load + reserve + floor), via compute_charge_need."""
        return compute_charge_need(
            soc_pct=soc_pct, usable_kwh=settings_cache["battery.usable_kwh"],
            min_reserve_soc=settings_cache["battery.min_reserve_soc"],
            night_reserve_kwh=settings_cache["battery.night_reserve_kwh"],
            overnight_load_kwh=settings_cache["battery.overnight_load_kwh"],
            round_trip_efficiency=settings_cache["planner.round_trip_efficiency"],
        )

    def _summer_cfg(soc_pct: float) -> SummerConfig:
        s = settings_cache
        return SummerConfig(
            usable_kwh=s["battery.usable_kwh"],
            target_soc_pct=_night_target_soc(soc_pct).target_soc_pct,
            round_trip_efficiency=s["planner.round_trip_efficiency"],
            max_charge_w=s["battery.max_charge_w"],
            expected_load_w=s["battery.overnight_load_kwh"] * 1000.0 / 12.0,
            allow_grid_topup=s["strategy.summer_grid_topup"],
            max_topup_price_eur_per_kwh=s["strategy.summer_max_topup_price"],
        )

    def _active_strategy(now: datetime) -> str:
        return select_strategy(now, settings_cache["strategy.mode"], site_tz)

    # Cached expected-load profile (learned async in _forward_projection) so the sync _current_plan
    # can feed the adaptive charger without its own DB read. None until the first projection runs.
    _load_profile_box: dict[str, Any] = {"profile": None}

    def _load_by(starts: list[datetime]) -> dict[datetime, float]:
        prof = _load_profile_box["profile"]
        if prof is None:  # cold start: a flat overnight-derived baseline
            fallback = settings_cache["battery.overnight_load_kwh"] * 1000.0 / 12.0
            return {s: fallback for s in starts}
        return {s: prof.expected_w(s) for s in starts}

    def _adaptive_cfg() -> AdaptiveConfig:
        s = settings_cache
        return AdaptiveConfig(
            usable_kwh=s["battery.usable_kwh"],
            reserve_soc_pct=s["battery.min_reserve_soc"],
            round_trip_efficiency=s["planner.round_trip_efficiency"],
            max_charge_w=s["battery.max_charge_w"],
            degradation_eur_per_kwh=s["planner.degradation_eur_per_kwh"],
            risk_margin_eur_per_kwh=s["planner.risk_margin_eur_per_kwh"],
        )

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
                intent, reason, override_active = await asyncio.to_thread(_effective_intent, now)
                if intent is None:
                    continue
                d = await asyncio.to_thread(controller.preview, intent, now)
                mode = str(d.desired_mode)
                if mode == last_mode:
                    continue
                last_mode = mode
                verb = "Would set" if dry_run else "Set"
                await audit_store.append(
                    now.isoformat(), "battery_decision",
                    f"{verb} battery to {mode} — {reason}",
                    {"intent": str(intent), "desired_mode": mode, "reason": reason,
                     "override": override_active, "applied": d.applied, "dry_run": dry_run},
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
            if float(settings_cache.get("explainer.validate_hours", 0) or 0) > 0:
                await _run_validation()  # already guarded + never raises

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        # Guarantee the schema exists before anything touches the DB (no caller footgun).
        if store is not None:
            await store.init()
        if settings_store is not None:
            await settings_store.init()
            # Build the new dict BEFORE touching the cache, then swap with no await between
            # clear() and update() — otherwise a concurrent reader could observe an empty cache.
            loaded = effective_settings(await settings_store.all())
            settings_cache.clear()
            settings_cache.update(loaded)
            _apply_control_settings()
            _apply_site_settings()
            _apply_explainer_settings()
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
        # Start the read-only sense loop / recorder (SPEC §5.3). Take one awaited startup
        # sample so /api/series and /api/freshness are populated deterministically, then
        # run the periodic loop in the background.
        stop = asyncio.Event()
        task = None
        if recorder is not None:
            try:
                await recorder.record_now()
            except Exception:
                pass  # fail-safe: a bad first read must not block startup
            task = asyncio.create_task(recorder.run(stop))
            task.add_done_callback(_recorder_died)
        # The control loop (battery writes) runs ONLY in operational mode (not dry_run). In dry-run
        # it is never started, so the dashboard previews but the battery is never touched.
        control_task = None
        if not dry_run and controller is not None:
            control_task = asyncio.create_task(
                ControlLoop(_control_tick, control_cycle_seconds).run(stop)
            )
            control_task.add_done_callback(_task_died("Control loop"))
        # Decision/plan audit loop — advisory, runs in ANY mode (dry-run too), off the control path.
        audit_task = None
        if audit_store is not None and controller is not None:
            audit_task = asyncio.create_task(_audit_decision_loop(stop))
            audit_task.add_done_callback(_task_died("Decision audit"))
        # Scheduled AI second-opinion (advisory, off control path; no-op until AI is on).
        validate_task = asyncio.create_task(_ai_validation_loop(stop))
        validate_task.add_done_callback(_task_died("AI validation"))
        try:
            yield
        finally:
            stop.set()
            for t in (task, control_task, audit_task, validate_task):
                if t is not None:
                    await t

    app = FastAPI(title="Smart Energy Manager", version="0.0.1", lifespan=lifespan)

    def _current_plan():
        """Single source of the current plan (DRY) so /api/plan, /api/savings, /api/decision and
        /api/alerts all reflect the same computation. Dispatches to the active strategy
        (summer solar-first / winter arbitrage). Returns (now, prices, plan) or None."""
        if price_source is None:
            return None
        now = datetime.now(UTC)
        prices = price_source.slots()
        strategy = _active_strategy(now)
        # Summer uses the adaptive (demand-aware peak-shaving) charger; it needs the live SoC, the
        # solar forecast and the expected-load profile. Winter (arbitrage) needs none of these.
        soc = _current_soc(now) if strategy == "summer" else 0.0
        forecast = (solar_forecast.slots()
                    if strategy == "summer" and solar_forecast is not None else [])
        load_by = _load_by([p.start for p in prices]) if strategy == "summer" else None
        plan = build_plan(
            strategy, prices=prices, forecast=forecast, now=now, soc_pct=soc,
            winter_cfg=_planner_cfg(), summer_cfg=_summer_cfg(soc),
            load_w_by=load_by, adaptive_cfg=_adaptive_cfg(),
        )
        return now, prices, plan

    def _data_quality(now: datetime) -> str:
        """Single source of the current data-quality level (SPEC §8.11)."""
        snap = freshness.snapshot(now) if freshness is not None else {}
        return data_quality(
            snap, prices_ok=price_source is not None, forecast_ok=solar_forecast is not None
        )

    def _effective_intent(now: datetime):
        """The intent the controller should act on now, honouring an active manual override and the
        data-quality fail-safe. Returns (intent | None, reason | None, override_active). An active
        override wins over the plan AND the fail-safe (deliberate, time-boxed operator action — the
        UI shows the data-quality badge). The planner path is gated: unsafe data falls back to
        self-consumption (CLAUDE.md "fail safe"). Works even with no price source."""
        ov = override_box["ov"]
        if ov.active(now):
            assert ov.intent is not None and ov.expires_at is not None
            until = ov.expires_at.astimezone().strftime("%H:%M")
            intent, reason, override_active = ov.intent, (
                f"manual override: {ov.intent.value} until {until}"), True
        else:
            pp = _current_plan()
            if pp is None:
                return None, None, False
            cur = pp[2].intent_at(now)
            if cur is None:
                return None, None, False
            safe, fs_reason = failsafe_intent(cur.intent, _data_quality(now))
            intent, reason = ((safe, fs_reason) if fs_reason is not None
                              else (cur.intent, cur.reason))
            override_active = False
        # Final guardrail (over the plan AND a manual override): never discharge into the car.
        intent, reason = _car_guard(now, intent, reason)
        return intent, reason, override_active

    def _chat_context() -> str:
        """A compact, REDACTED snapshot for the chat to ground on — only non-identifying facts (the
        plan, prices, power/percentage figures), NEVER location, IPs, raw history, or tokens. Every
        block is defensive: building the context must never raise."""
        now = datetime.now(UTC)
        lines = [f"Now (UTC): {now:%Y-%m-%d %H:%M}", f"Strategy: {_active_strategy(now)}"]
        try:
            lines.append(f"Battery level now: {_current_soc(now):.0f}%")
        except Exception:
            pass
        try:
            intent, reason, override_active = _effective_intent(now)
            if intent is not None:
                lines.append(
                    f"Current decision: {intent} — {reason}"
                    + (" (manual override active)" if override_active else "")
                )
        except Exception:
            pass
        pp = _current_plan()
        if pp is not None:
            _now, prices, plan = pp
            try:
                fc = solar_forecast.slots() if solar_forecast is not None else None
                lines.append(f"Plan: {build_plan_detail(_now, prices, plan, fc)['summary']}")
            except Exception:
                pass
            try:
                by = {p.start: p.eur_per_kwh for p in prices}
                lines.append(
                    f"Estimated savings today vs no smart control: "
                    f"€{estimate_daily_savings_eur(plan, by):.2f}"
                )
            except Exception:
                pass
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
            pass
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

    def _control_tick(now: datetime) -> None:
        """Operational mode ONLY (never called in dry-run): advance the ownership lifecycle and,
        once CONTROLLING, apply the current intent — the single battery write per cycle. Every
        safety gate (dwell, daily cap, fail-safe AUTO on unsafe data, override) is enforced by
        ModeController.decide / _effective_intent."""
        if controller is None:
            return
        lc = controller.lifecycle
        if lc.state is OwnershipState.INACTIVE:
            lc.start(now)
        # Readiness sequence (SPEC §13.3): validated sensors, a reachable battery, a loaded plan.
        if _data_quality(now) != "unsafe":
            lc.mark_sensors_validated()
        try:
            controller.driver.current_mode()  # read-only reachability check
            lc.mark_probe_ok()
        except Exception:
            pass  # battery unreadable -> not probe-ok -> stays observing, never commands
        if _current_plan() is not None:
            lc.mark_plan_loaded()
        lc.tick(now)
        if lc.can_command(now):
            intent, _reason, _override = _effective_intent(now)
            if intent is not None:
                controller.decide(intent, now)

    @app.get("/health/live")
    def live() -> dict:
        return {"status": "alive"}

    @app.get("/health/ready")
    def ready() -> dict:
        return {"status": "ready", "dry_run": dry_run, "dev_mode": dev_mode}

    @app.get("/api/auth")
    def auth_status(request: Request) -> dict:
        # Lets the UI show a token field only when writes are protected, and reflect auth state.
        return {"required": web_auth_token is not None, "authenticated": _authorized(request)}

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
        intent, _reason, override_active = _effective_intent(now)
        if intent is not None and controller is not None:
            outcome = controller.preview(intent, now).outcome
        alerts = derive_alerts(snap, dry_run=dry_run, decision_outcome=outcome)
        out = [{"key": a.key, "severity": a.severity, "message": a.message} for a in alerts]
        if override_active:
            ov = override_box["ov"]
            until = ov.expires_at.astimezone().strftime("%H:%M") if ov.expires_at else "?"
            label = ov.intent.value if ov.intent else "?"
            out.append({
                "key": "manual_override_active", "severity": "warning",
                "message": f"Manual override: forcing {label} until {until}",
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
        car_charging = _car_charging(now)
        intent, reason, override_active = _effective_intent(now)
        if intent is None:
            return {"intent": None, "desired_mode": None, "applied": False,
                    "outcome": "no_plan", "reason": "no plan slot for now",
                    "plan_reason": None, "override_active": False, "car_charging": car_charging}
        # preview() is read-only — a GET must never write to the battery or mutate counters.
        d = controller.preview(intent, now)
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
                store_ok = False
        settings_ok = False
        if settings_store is not None:
            try:
                await settings_store.all()
                settings_ok = True
            except Exception:
                settings_ok = False
        # probe() is a SYNC, possibly-networked call — run it off the event loop and guard it so an
        # unreachable battery shows as a warn check, not a 500 (and never blocks the loop).
        p1_paired = False
        battery_ok = battery is not None
        if battery is not None:
            try:
                p1_paired = (await asyncio.to_thread(battery.probe)).p1_paired
            except Exception:
                battery_ok = False
        checks = build_diagnostics(
            dev_mode=dev_mode, dry_run=dry_run,
            data_quality=_data_quality(now),
            prices_ok=prices_ok, forecast_ok=forecast_ok,
            battery_ok=battery_ok, p1_paired=p1_paired,
            plan_ok=_current_plan() is not None,
            store_ok=store_ok, settings_store_ok=settings_ok,
            auth_required=web_auth_token is not None,
            freshness=freshness.snapshot(now) if freshness is not None else None,
        )
        return {"overall": overall_status(checks), "checks": [c.to_dict() for c in checks]}

    @app.get("/api/charge-need")
    def charge_need_endpoint() -> dict:
        # Advisory: how much the battery should hold by tonight, from current SoC + battery config.
        s = settings_cache
        return compute_charge_need(
            soc_pct=source.read().soc_pct,
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
        if not _authorized(request):
            return _auth_error()
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
        return JSONResponse(get_override())

    def _battery_cluster() -> tuple[list[dict], dict | None]:
        """Per-tower readings + the cluster aggregate, read once from the live cluster reader.
        Empty/None for the mock source (which has no per-tower battery reader)."""
        reader = getattr(source, "battery", None)
        if reader is None or not hasattr(reader, "read_towers"):
            return [], None
        try:
            towers = reader.read_towers()
        except Exception:
            return [], None  # never let a battery read break the endpoint
        rows = [
            {"ip": t.ip, "role": t.role, "soc_pct": t.soc_pct, "power_w": t.power_w,
             "capacity_kwh": t.capacity_kwh, "online": t.online}
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
        towers, aggregate = _battery_cluster()
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

    @app.get("/api/plan")
    def plan_endpoint() -> dict:
        pp = _current_plan()
        if pp is None:
            return {"created_at": None, "current_intent": None,
                    "current_reason": None, "slots": []}
        now, _prices, plan = pp
        cur = plan.intent_at(now)
        return {
            "created_at": plan.created_at.isoformat(),
            "current_intent": cur.intent if cur else None,
            "current_reason": cur.reason if cur else None,
            "slots": [
                {"start": s.start.isoformat(), "intent": s.intent, "reason": s.reason}
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
        active = _active_strategy(now)
        return {
            "mode": mode,  # auto | summer | winter (the user's choice)
            "active": active,  # the resolved strategy actually running
            "auto": mode == "auto",
            "summary": _STRATEGY_DESC.get(active, ""),
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
        /api/energy-forecast and /api/energy-story so they never drift."""
        pp = _current_plan()
        if pp is None or solar_forecast is None:
            return None
        now, prices_, plan = pp
        if not plan.slots:
            return None
        soc = _current_soc(now)
        fc_slots = solar_forecast.slots()
        solar_by = {f.start: f.p50_w for f in fc_slots}
        # Learn the expected load from ~7 days of derived history; fall back to the overnight
        # estimate spread across a ~12h night when there's little history.
        drows = await store.recent_derived(2016) if store is not None else []
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
        # The adaptive (summer) charger sizes its own charge slots, so the projection must NOT cap
        # them at the night target (that would undo demand-aware peak-shaving). Winter's fixed-slot
        # plan keeps the cap as a safety ceiling.
        cap = None if _active_strategy(now) == "summer" else need.target_soc_pct
        projected = project_energy(
            plan.slots, start_soc_pct=soc, solar_w_by=solar_by,
            load_w_by=load_by, model=_battery_model(),
            charge_target_soc_pct=cap,
        )
        return {"now": now, "current_soc": soc, "projected": projected, "need": need,
                "deadline": sunset_after(fc_slots, now),
                "price_by": {p.start: p.eur_per_kwh for p in prices_}}

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

    def _next_headline(totals: dict, need) -> str:
        charge, imp, ss = totals["charge_kwh"], totals["import_kwh"], totals["self_sufficiency_pct"]
        if charge > 0.1:
            head = (f"Next 24h — top up {charge:.1f} kWh to the {need.target_soc_pct:.0f}% night "
                    f"target, then run the evening on the battery.")
        else:
            head = "Next 24h — the sun covers the night; running on the battery, no grid charging."
        head += f" Projected {imp:.1f} kWh imported"
        head += f", {ss:.0f}% self-sufficient." if ss is not None else "."
        return head

    async def _next_story(reserve_pct: float) -> dict:
        fp = await _forward_projection()
        if fp is None:
            return _empty_story("next", reserve_pct, "No plan yet.")
        price_by, need, deadline = fp["price_by"], fp["need"], fp["deadline"]
        slots = [
            _uslot(p.start, p.soc_pct, p.grid_w, p.solar_w, p.battery_w, p.load_w,
                   price_by.get(p.start), _action_from_intent(p.intent))
            for p in fp["projected"]
        ]
        totals = _uslot_totals(slots)
        return {
            "window": "next", "now": fp["now"].isoformat(),
            "current_soc_pct": round(fp["current_soc"], 1), "reserve_soc_pct": reserve_pct,
            "target_soc_pct": round(need.target_soc_pct, 1),
            "target_kwh": round(need.target_kwh, 1),
            "target_deadline": deadline.isoformat() if deadline is not None else None,
            # The price right now = the first slot (it covers the current quarter-hour).
            "current_price_eur_per_kwh": slots[0]["eur_per_kwh"] if slots else None,
            "slots": slots, "totals": totals, "headline": _next_headline(totals, need),
        }

    async def _past_story(reserve_pct: float) -> dict:
        now = datetime.now(UTC)
        cutoff = (now - timedelta(hours=24)).isoformat()
        raw = await store.recent_raw_since(cutoff) if store is not None else []
        der = await store.recent_derived_since(cutoff) if store is not None else []
        prices = price_source.slots() if price_source is not None else []
        story = build_past_story(raw, der, prices, now)
        slots = [
            _uslot(ps.start, ps.soc_pct, ps.grid_w, ps.solar_w, ps.battery_w, ps.load_w,
                   ps.eur_per_kwh, _action_from_battery(ps.battery_w))
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

    @app.get("/api/export")
    async def export(
        kind: str = Query(default="raw", pattern="^(raw|derived)$"),
        fmt: str = Query(default="csv", pattern="^(csv|json)$", alias="format"),
        limit: int = Query(default=1000, ge=1, le=2000),
    ) -> Response:
        # Download recent history (oldest→newest) as CSV or JSON. Read-only, open like reads.
        columns = RAW_COLUMNS if kind == "raw" else DERIVED_COLUMNS
        rows: list[dict] = []
        if store is not None:
            recent = store.recent_raw if kind == "raw" else store.recent_derived
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
        if not _authorized(request):
            return _auth_error()
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
        refreshed = effective_settings(await settings_store.all())
        settings_cache.clear()
        settings_cache.update(refreshed)
        _apply_control_settings()
        _apply_site_settings()
        _apply_explainer_settings()
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
        raw = source.read()
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

    @app.get("/api/ai/validation")
    def ai_validation_latest() -> dict:
        """The latest AI second-opinion (advisory), for the dashboard. null until one has run."""
        return {"latest": validation_box["latest"], "active": _explainer_active()}

    @app.post("/api/ai/validate")
    async def ai_validate_now(request: Request) -> JSONResponse:
        """Run an AI second-opinion on demand (the dashboard's "check now"). Advisory; off → 200
        with latest=null. Auth-gated like other writes; never 500s."""
        if not _authorized(request):
            return _auth_error()
        try:
            result = await _run_validation()
        except Exception:
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

    @app.post("/api/chat")
    async def chat_endpoint(request: Request) -> JSONResponse:
        """Ask the assistant about the current decisions/dashboard. Grounded ONLY on a redacted
        snapshot (_chat_context); advisory, never touches control. Off → a friendly nudge to enable
        it. Any failure degrades to a safe message, never a 500."""
        if not _authorized(request):
            return _auth_error()
        try:
            data = await request.json()
        except Exception:
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
