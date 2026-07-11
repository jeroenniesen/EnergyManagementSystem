"""Read-only status API (SPEC §9.1). No device writes in M0a."""
from __future__ import annotations

import asyncio
import csv
import hashlib
import io
import json
import logging
import secrets
import threading
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from datetime import date as date_cls
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Query, Request
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from ems import export_package as expkg
from ems.alerts import data_quality, derive_alerts
from ems.analysis import forecast_error
from ems.control.failsafe import failsafe_intent
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
from ems.domain import BatteryIntent, PhysicalMode
from ems.energy_flow import build_daily_flows
from ems.finance import day_finance
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
from ems.planner.strategy import build_plan, select_strategy_with_reason
from ems.planner.summer import SummerConfig, sunset_after
from ems.planner.validator import PlanValidation, validate_plan
from ems.readiness import Readiness, compute_readiness, home_state
from ems.reporting import build_report, build_series, gas_m3_consumed, resolve_window
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
from ems.storage.history import DERIVED_COLUMNS, RAW_COLUMNS, HistoryStore
from ems.storage.settings import SettingsStore
from ems.weather import cloud_cover_pct

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

# Cluster per-tower mode LABEL (from tower_mode_label) → PhysicalMode, so the coalesced cluster
# read can serve the control loop's idempotency/reachability without a separate master mode-read.
_LABEL_TO_MODE = {
    "self-consumption": PhysicalMode.AUTO,
    "standby": PhysicalMode.IDLE,
    "charging": PhysicalMode.CHARGE,
    "discharging": PhysicalMode.DISCHARGE,
    "outdoor": PhysicalMode.AUTO,
    "schedule": PhysicalMode.AUTO,
}
# Mode FAMILY for cluster-consistency checks: the STABLE distinction is vendor self-consumption
# (mode 1) vs EMS real-time control (mode 4). charging/discharging/standby are transient real-time
# states (a battery that finished charging shows "standby", not "charging") — so we compare at the
# family level to avoid false "didn't follow" flags. outdoor/schedule/unknown → None (don't judge).
_REALTIME_LABELS = {"standby", "charging", "discharging"}


def _tower_family(label: str | None) -> str | None:
    if label == "self-consumption":
        return "self-consumption"
    if label in _REALTIME_LABELS:
        return "real-time"
    return None


def _commanded_family(mode: PhysicalMode) -> str:
    return "self-consumption" if mode is PhysicalMode.AUTO else "real-time"

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


def history_row_cap(
    span_seconds: float,
    cycle_seconds: float,
    *,
    margin: float = 2.0,
    floor: int = 1000,
    ceiling: int = 200_000,
) -> int:
    """Row limit for a history query spanning `span_seconds`, sized to the recorder cadence rather
    than hardcoded (finding 10). The recorder writes ~one row every `cycle_seconds`, so a report
    stays correct if the sampling frequency changes (e.g. faster in dev) instead of silently
    truncating at a fixed 3000/day or a 1-row-per-minute ceiling. `margin` gives headroom for
    write jitter; the result is clamped to `[floor, ceiling]`."""
    cadence = max(float(cycle_seconds), 1.0)
    rows = int(max(span_seconds, 0.0) / cadence) + 1
    return max(floor, min(ceiling, int(rows * margin)))


# Bump when the finance math changes so completed-day rows cached under the OLD formula are
# recomputed instead of served stale (finding 4). v2 = same-window wear (dis_priced) + price-gate.
_FINANCE_CALC_VERSION = 2


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
    # Current manual override, cached in memory (expiry is evaluated per request). Mutated in
    # place via the "ov" key so the closure stays valid; loaded in lifespan, set by POST /override.
    override_box: dict[str, Override] = {"ov": OVERRIDE_NONE}
    _OV_INTENT, _OV_EXP = "override.intent", "override.expires_at"
    # Serialise control cycles: the periodic loop and an immediate override-triggered cycle must not
    # run decide()/apply() concurrently (two writes racing the battery).
    _control_lock = asyncio.Lock()
    # Cluster-drift dedup: the signature of towers currently NOT in the commanded mode family, so we
    # audit a mismatch (or its resolution) ONCE per episode, not every cycle.
    _drift_box: dict[str, Any] = {"sig": None}
    # Held-decision dedup: the (outcome, desired_mode) we last audited as a HELD/blocked decision
    # (dwell/cap/not_controlling), so a recurring hold is explained ONCE, not logged every cycle.
    _held_box: dict[str, Any] = {"sig": None}
    # Sky cloud-cover cache: Open-Meteo is polled at most every 15 min (best-effort) for the sky.
    _sky_box: dict[str, Any] = {"cc": None, "at": None}

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
            _bounded_put(cache, key, asyncio.ensure_future(_run()), _EXPLAIN_MEM_CACHE_MAX)
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
    # Same idea for the PER-TOWER battery read (/api/battery): coalesce read_towers() across all
    # dashboard endpoints and browser tabs so the Indevolt cluster is polled at most once per
    # window, no matter how many clients are open. This is what stops the read flood that can knock
    # a tower off the network — every battery touch here is read-only, but the VOLUME was the issue.
    _tower_cache: dict[str, Any] = {"towers": None, "at": None}
    # Single-flight locks: sync endpoints run in FastAPI's threadpool, so a cold start / cache
    # expiry with several tabs polling can have multiple threads miss the cache at once. The lock +
    # double-check means exactly ONE thread reads the hardware per window; the rest reuse its
    # result. This is the device-flood protection at its highest-risk moment (cache expiry).
    _sample_lock = threading.Lock()
    _tower_lock = threading.Lock()
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
            _capability_box["cap"] = None

    def _coalesce_s() -> float:
        """How long a live read is reused before re-reading the hardware. UI-tunable
        (control.live_read_seconds) so the operator can ease load on a battery shared with Home
        Assistant + the Indevolt app; falls back to the module default."""
        try:
            return float(settings_cache.get("control.live_read_seconds")
                         or _LIVE_SAMPLE_COALESCE_SECONDS)
        except (TypeError, ValueError):
            return _LIVE_SAMPLE_COALESCE_SECONDS

    def _sample_fresh(now: datetime) -> bool:
        at = _sample_cache["at"]
        return (at is not None and _sample_cache["sample"] is not None
                and (now - at).total_seconds() < _coalesce_s())

    def _current_sample(now: datetime):
        if _sample_fresh(now):  # fast path: no lock when the cache is warm
            return _sample_cache["sample"]
        with _sample_lock:  # single-flight: one thread reads hardware per window, others reuse it
            if _sample_fresh(now):
                return _sample_cache["sample"]
            try:
                _sample_cache["sample"], _sample_cache["at"] = source.read(), now
            except Exception:
                pass  # keep the last good sample (fail-safe)
            return _sample_cache["sample"]

    def _current_soc(now: datetime) -> float:
        s = _current_sample(now)
        return float(s.soc_pct) if s is not None else 0.0

    def _towers_fresh(now: datetime) -> bool:
        at = _tower_cache["at"]
        return (at is not None and _tower_cache["towers"] is not None
                and (now - at).total_seconds() < _coalesce_s())

    def _current_towers(now: datetime):
        """Coalesced + single-flight per-tower battery read (same window as _current_sample).
        Returns the cached list of TowerReading, or None when there's no cluster reader (mock). On
        a read failure the last good snapshot is kept (fail-safe) so a transient blip doesn't blank
        the card. The lock means several tabs hitting /api/battery at cache expiry poll the cluster
        ONCE, not once each."""
        reader = getattr(source, "battery", None)
        if reader is None or not hasattr(reader, "read_towers"):
            return None
        if _towers_fresh(now):  # fast path
            return _tower_cache["towers"]
        with _tower_lock:
            if _towers_fresh(now):
                return _tower_cache["towers"]
            try:
                _tower_cache["towers"], _tower_cache["at"] = reader.read_towers(), now
            except Exception:
                pass  # keep last good snapshot (fail-safe)
            return _tower_cache["towers"]

    def _current_mode(now: datetime):
        """The battery's current physical mode, DERIVED from the shared coalesced cluster read
        (`_current_towers`) — so the dashboard previews AND the control loop reuse that one read
        instead of each hitting the master with its own `driver.current_mode()`. This is the big
        master-load saver, given the device is shared with Home Assistant + the Indevolt app. None
        in dry-run / no controller. Falls back to a direct driver read only when there's no cluster
        reader at all (mock / single non-cluster driver)."""
        if controller is None or dry_run:
            return None
        towers = _current_towers(now)
        if towers is not None:  # cluster reader present → reuse its coalesced read, not the master
            cand = next((t for t in towers if t.role == "master" and t.online and t.mode), None) \
                or next((t for t in towers if t.online and t.mode), None)
            return _LABEL_TO_MODE.get(cand.mode) if cand is not None else None
        try:
            return controller.driver.current_mode()
        except Exception:
            return None

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
            solar_confidence=s["planner.solar_confidence"] / 100.0,
            allow_grid_topup=s["strategy.summer_grid_topup"],
            max_topup_price_eur_per_kwh=s["strategy.summer_max_topup_price"],
        )

    def _strategy_inputs(now: datetime):
        """(surplus_kwh, price_spread_eur) over the next ~24h, for the energy-condition `auto`
        strategy choice. Defensive — any failure yields None so it falls back to the season."""
        surplus = spread = None
        try:
            if solar_forecast is not None:
                fc = solar_forecast.slots()[:96]
                load = _load_by([f.start for f in fc])
                surplus = sum(max(0.0, f.p50_w - load.get(f.start, 0.0)) * 0.25 / 1000.0
                              for f in fc)
        except Exception:
            pass
        try:
            if price_source is not None:
                ps = [p.eur_per_kwh for p in price_source.slots()[:96]]
                if ps:
                    spread = max(ps) - min(ps)
        except Exception:
            pass
        return surplus, spread

    def _resolve_strategy(now: datetime) -> tuple[str, str]:
        """(strategy, reason). Forced modes skip the (cheap-but-unneeded) energy-input computation;
        `auto` decides by forecast surplus + price spread (energy review P1.1)."""
        mode = settings_cache["strategy.mode"]
        if mode in ("summer", "winter"):
            return select_strategy_with_reason(now, mode, site_tz)
        surplus, spread = _strategy_inputs(now)
        return select_strategy_with_reason(
            now, mode, site_tz, surplus_kwh=surplus, price_spread_eur=spread
        )

    def _active_strategy(now: datetime) -> str:
        return _resolve_strategy(now)[0]

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
            solar_confidence=s["planner.solar_confidence"] / 100.0,
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
                intent, reason, override_active, tgt, pw, _val = await asyncio.to_thread(
                    _effective_intent, now
                )
                if intent is None:
                    continue
                d = await asyncio.to_thread(
                    controller.preview, intent, now, target_soc=tgt, power_w=pw
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
                    pass
            if float(settings_cache.get("explainer.validate_hours", 0) or 0) > 0:
                await _run_validation()  # already guarded + never raises

    async def _maintenance_loop(stop: asyncio.Event) -> None:
        """Daily history maintenance for a 24/7 install: purge rows past the retention window and
        truncate the WAL / reclaim freed space. Runs once at boot, then every 24 h. Best-effort —
        a busy DB just retries tomorrow. retention_days <= 0 keeps everything (purge skipped)."""
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
            except Exception as exc:
                _log.warning("history maintenance failed (%s: %s); retrying next cycle",
                             type(exc).__name__, exc)

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
                pass

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
        # If no settings store exists, still probe once so the §8.11 validator can sanity-check
        # requested power without a networked probe per decision. Normal startup already probes via
        # _apply_battery_power_settings(), after applying battery.* power settings to the driver.
        if controller is not None and _capability_box["cap"] is None:
            try:
                _capability_box["cap"] = await asyncio.to_thread(controller.driver.probe)
            except Exception:
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
        try:
            yield
        finally:
            stop.set()
            # In operational mode, hand the battery back to its safe vendor mode before we go — a
            # graceful stop (upgrade, reboot, launchd restart) must not leave it in a forced
            # charge/hold/discharge. Bounded + best-effort: never block shutdown on the device.
            await _shutdown_restore()
            for t in (task, control_task, audit_task, validate_task, maintenance_task):
                if t is not None:
                    await t

    app = FastAPI(title="Smart Energy Manager", version="0.0.1", lifespan=lifespan)

    # --- Access control (SPEC §12) --------------------------------------------------------------
    # One choke point for the whole JSON API (finding 1) instead of a guard sprinkled on each write.
    # Writes are ALWAYS gated when a token is configured. Reads are open on the LAN by default so
    # the dashboard degrades to read-only during an HA outage; set `web.require_auth` to gate reads
    # too — do that before reaching the app over a VPN / from outside the home network.
    _WRITE_API_PATHS = frozenset({
        "/api/override", "/api/settings", "/api/ai/validate", "/api/chat",
    })
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

    _WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

    class _AccessMiddleware:
        """One choke point for the whole JSON API (finding 1) — reuses the same `_authorized`
        check the writes always used. Deliberately a PURE-ASGI middleware, not
        `@app.middleware("http")`/`BaseHTTPMiddleware`: the latter wraps each request in an anyio
        task group, which starves the override endpoint's `asyncio.create_task` control cycle.
        The SPA shell + static assets stay open (so the browser can load its Access box); every
        datum it renders comes from a gated /api/* read. No proxy/forwarded headers are trusted —
        auth is the bearer token only (remote access is the LAN over a VPN); see
        docs/remote-access.md."""

        def __init__(self, app):
            self.app = app

        async def __call__(self, scope, receive, send):
            if scope["type"] == "http":
                path = scope.get("path", "")
                if (
                    path.startswith("/api/")
                    and path not in _AUTH_EXEMPT_API_PATHS
                    and _effective_web_token() is not None
                ):
                    is_write = (
                        scope.get("method", "GET").upper() in _WRITE_METHODS
                        and path in _WRITE_API_PATHS
                    )
                    if (is_write or _read_auth_required()) and not _authorized(Request(scope)):
                        await _auth_error()(scope, receive, send)
                        return
            await self.app(scope, receive, send)

    app.add_middleware(_AccessMiddleware)

    def _current_plan():
        """Single source of the current plan (DRY) so /api/plan, /api/savings, /api/decision and
        /api/alerts all reflect the same computation. Dispatches to the active strategy
        (summer solar-first / winter arbitrage). Returns (now, prices, plan) or None."""
        if price_source is None:
            return None
        now = datetime.now(UTC)
        prices = price_source.slots()
        strategy = _active_strategy(now)
        # BOTH seasons now use the adaptive (demand-aware) charger, so both need the live SoC, the
        # solar forecast and the expected-load profile — winter sizes the top-up to the evening
        # peak load above reserve, not just the cheapest slots (energy review P1.2).
        soc = _current_soc(now)
        forecast = solar_forecast.slots() if solar_forecast is not None else []
        load_by = _load_by([p.start for p in prices])
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
        )

    def _effective_intent(now: datetime):
        """The intent the controller should act on now + its energy sizing, honouring an active
        manual override and the data-quality fail-safe. Returns
        (intent|None, reason|None, override_active, target_soc|None, power_w|None, validation|None).

        An active override wins over the plan AND the fail-safe (deliberate, time-boxed operator
        action — the UI shows the data-quality badge). The planner path is gated: unsafe data falls
        back to self-consumption (CLAUDE.md "fail safe"). Sizing (target_soc/power_w) is taken from
        the SAME plan slot we resolved, and ONLY when the final intent still matches that slot's
        intent — an override, a fail-safe or a car-guard substitution carries no sizing, so a stale
        target can never leak to the driver. A target is emitted only for a physical CHARGE
        (the slot target SoC) or, when export-discharge is enabled, a forced DISCHARGE (the reserve
        floor); a DISCHARGE_FOR_LOAD that maps to AUTO needs none."""
        cur = None
        val: PlanValidation | None = None
        ov = override_box["ov"]
        if ov.active(now):
            assert ov.intent is not None and ov.expires_at is not None
            until = ov.expires_at.astimezone().strftime("%H:%M")
            intent, override_active = ov.intent, True
            # Gate a RISKY override (anything other than self-consumption) on data quality: EMS
            # won't force charge/discharge/hold when critical data is unsafe — it can't trust SoC or
            # reachability. Returning to self-consumption is always allowed (energy review #5).
            risky = intent is not BatteryIntent.ALLOW_SELF_CONSUMPTION
            if risky and _data_quality(now) == "unsafe":
                intent = BatteryIntent.ALLOW_SELF_CONSUMPTION
                reason = (f"manual override held — sensor data is unsafe, so EMS won't force "
                          f"{ov.intent.value}; holding self-consumption until {until}")
            else:
                reason = f"manual override: {ov.intent.value} until {until}"
        else:
            pp = _current_plan()
            if pp is None:
                return None, None, False, None, None, None
            cur = pp[2].intent_at(now)
            if cur is None:
                return None, None, False, None, None, None
            # §8.11 hard gate: a plan that fails validation (impossible target, projected below
            # reserve, …) must not be acted on — hold self-consumption, like the data fail-safe.
            # Validate the plan we ALREADY fetched (no second _current_plan rebuild).
            val = _validate_plan_obj(pp[2], now)
            if not val.ok:
                top = next((f for f in val.findings if f.severity == "unsafe"), None)
                note = top.message if top is not None else "plan failed validation"
                cur = None  # not acting on a plan slot — sizing must be None below
                intent, reason, override_active = (
                    BatteryIntent.ALLOW_SELF_CONSUMPTION,
                    f"holding self-consumption — {note}", False)
            else:
                safe, fs_reason = failsafe_intent(cur.intent, _data_quality(now))
                intent, reason = ((safe, fs_reason) if fs_reason is not None
                                  else (cur.intent, cur.reason))
                override_active = False
        # Final guardrail (over the plan AND a manual override): never discharge into the car.
        intent, reason = _car_guard(now, intent, reason)
        target_soc = power_w = None
        if override_active:
            # A manual override is an EXPLICIT operator command, so it carries its own target —
            # "charge now" means charge toward full (deliberate, not the planner's silent default),
            # a forced discharge stops at the reserve floor. (Gated overrides held to
            # self-consumption fall through with no target, which is correct.)
            if intent is BatteryIntent.GRID_CHARGE_TO_TARGET:
                target_soc = 100.0
                # "charge now" = charge at the configured cluster max (default 4 kW), which the
                # driver then splits across towers — not the driver's conservative 2 kW default.
                power_w = settings_cache["battery.max_charge_w"]
            elif (intent is BatteryIntent.DISCHARGE_FOR_LOAD and controller is not None
                  and controller.allow_export_discharge):
                target_soc = settings_cache["battery.min_reserve_soc"]
                power_w = settings_cache["battery.max_discharge_w"]
        elif cur is not None and intent is cur.intent:
            if intent is BatteryIntent.GRID_CHARGE_TO_TARGET:
                target_soc, power_w = cur.target_soc, cur.power_w
            elif (intent is BatteryIntent.DISCHARGE_FOR_LOAD and controller is not None
                  and controller.allow_export_discharge):
                target_soc, power_w = cur.floor_soc, cur.power_w  # forced discharge → reserve floor
        return intent, reason, override_active, target_soc, power_w, val

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
        return {
            "strategy": strat,
            "target_soc": plan.target_soc,
            "deadline": plan.deadline.isoformat() if plan.deadline else None,
            "soc_pct": _current_soc(now),
            "intent": str(intent) if intent is not None else None,
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
            pass
        try:
            intent, reason, override_active, _t, _p, _v = _effective_intent(now)
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

    def _cluster_drift_record(desired: PhysicalMode, towers) -> dict | None:
        """Deduped audit record when the cluster doesn't match the commanded mode FAMILY. Returns a
        record once when drift starts and once when it clears; None while unchanged. towers = the
        coalesced per-tower read (steady-state).

        CLUSTER MODEL (asymmetric, verified live): the EMS commands the MASTER for real-time, which
        drives the slaves in lockstep — a following slave keeps REPORTING self-consumption (7101=1)
        even while it charges. So:
        - Commanded REAL-TIME (charge/discharge/idle): judge the MASTER only (it reflects the mode);
          a slave in self-consumption is FOLLOWING, not a fault — judging it falsely flagged it.
        - Commanded SELF-CONSUMPTION (AUTO): every tower is commanded and must return to it, so flag
          ANY tower still stuck in real-time (the genuine "didn't revert" fault)."""
        if not towers:
            return None
        want = _commanded_family(desired)
        if want == "self-consumption":
            judged = [t for t in towers if t.online]  # all must have returned to self-consumption
        else:
            masters = [t for t in towers if t.online and t.role == "master"]
            judged = masters or [t for t in towers if t.online]  # master drives real-time
        laggards = [t for t in judged
                    if t.mode and _tower_family(t.mode) not in (want, None)]
        sig = tuple(sorted((t.ip, t.mode) for t in laggards))
        if sig == _drift_box["sig"]:
            return None  # already reported this exact state
        prev = _drift_box["sig"]
        _drift_box["sig"] = sig
        if not laggards:
            return ({"summary": "Battery cluster back in sync — all towers match the commanded "
                                "mode",
                     "detail": {"event": "drift_resolved", "commanded": desired.value}}
                    if prev else None)
        modes = {t.ip: t.mode for t in laggards}
        return {
            "summary": (f"Battery cluster MISMATCH — {len(laggards)} tower(s) NOT following the "
                        f"commanded {desired.value}: {', '.join(sorted(set(modes.values())))}"),
            "detail": {"event": "cluster_drift", "commanded": desired.value, "laggards": modes},
        }

    def _control_tick(now: datetime) -> list[dict]:
        """Operational mode ONLY: advance the ownership lifecycle and, once CONTROLLING, apply the
        current intent — the single battery write per cycle. Every safety gate (dwell, daily cap,
        fail-safe AUTO on unsafe data, override) is enforced by ModeController.decide /
        _effective_intent. Returns audit records for the async caller to log: a CONFIRMED
        mode-change record when a write was attempted (applied/failed), and/or a cluster-mismatch
        record when a tower isn't following the commanded mode (steady state). [] = nothing."""
        if controller is None:
            return []
        lc = controller.lifecycle
        if lc.state is OwnershipState.INACTIVE:
            lc.start(now)
        # Readiness sequence (SPEC §13.3): validated sensors, a reachable battery, a loaded plan.
        if _data_quality(now) != "unsafe":
            lc.mark_sensors_validated()
        # Reachability + idempotency reuse the SHARED coalesced cluster read (observed) instead of a
        # separate per-cycle master mode-read — far gentler on a device shared with HA + the app.
        # IMPORTANT: "reachable" = the battery RESPONDED this cycle (a tower online), NOT that its
        # mode decoded to a known label — else an unexpected mode value would stall ALL control
        # (incl. manual overrides). `observed` may be None; decide() then reads fresh.
        observed = _current_mode(now)
        towers = _current_towers(now)
        reachable = any(t.online for t in towers) if towers else observed is not None
        if reachable:
            lc.mark_probe_ok()  # battery readable this cycle
        if _current_plan() is not None:
            lc.mark_plan_loaded()
        lc.tick(now)
        if not lc.can_command(now):
            return []
        intent, _reason, override_active, tgt, pw, _v = _effective_intent(now)
        if intent is None:
            return []
        # decide() uses `observed` for the idempotency gate; its post-write CONFIRM re-reads the
        # device fresh, so a stale observation only risks a redundant idempotent write. `manual` (an
        # active operator override) and `priority` (a SAFETY action — the car-guard hold while the
        # car charges) bypass the automatic dwell/cap gates: never leave the battery draining into
        # the car just because today's switch budget is spent (a return to AUTO is always allowed
        # too; see _gate).
        priority = _car_charging(now)
        dec = controller.decide(intent, now, target_soc=tgt, power_w=pw,
                                observed_mode=observed, manual=override_active, priority=priority)
        records: list[dict] = []
        if dec.outcome in ("applied", "failed_recovered", "failed_unrecovered"):
            # An ACTUAL device write — audit it. `accepted` = the device acknowledged the command
            # (result:true); the mode switches with latency, so whether it actually TOOK is verified
            # on a later cycle by the cluster-consistency check below (which flags a tower that
            # never follows). So this logs "command sent" / "FAILED", not a premature "confirmed".
            _held_box["sig"] = None  # an action happened — re-explain any future hold afresh
            before = observed.value if observed is not None else "unknown"
            accepted = dec.applied
            records.append({
                "summary": (f"Battery mode {before} → {dec.desired_mode.value} — "
                            + ("command sent" if accepted else f"command FAILED ({dec.reason})")),
                "detail": {"from_mode": before, "desired_mode": dec.desired_mode.value,
                           "intent": str(dec.intent), "outcome": dec.outcome,
                           "accepted": accepted, "reason": dec.reason}})
        elif dec.outcome == "idempotent":
            # Steady state: EMS believes it's already in `desired`. VERIFY the whole cluster —
            # a tower that didn't follow (still self-consuming while we commanded real-time) is the
            # silent slave-not-following bug. Towers are fresh here (no write this cycle).
            _held_box["sig"] = None
            drift = _cluster_drift_record(dec.desired_mode, towers)
            if drift is not None:
                records.append(drift)
        elif dec.outcome == "unconfirmed":
            # The write TIMED OUT (device slow/unreachable) — we did NOT revert (the device likely
            # got it; reverting would also time out). Surface it (deduped) so a recurring "charge
            # isn't sticking because the battery is slow to answer" is visible, not silent.
            sig = (dec.outcome, dec.desired_mode.value)
            if _held_box["sig"] != sig:
                _held_box["sig"] = sig
                records.append({
                    "summary": (f"Battery {dec.desired_mode.value} unconfirmed — device slow to "
                                "respond; holding and retrying (not reverting)"),
                    "detail": {"desired_mode": dec.desired_mode.value, "intent": str(dec.intent),
                               "outcome": dec.outcome, "reason": dec.reason,
                               "override_active": override_active}})
        elif dec.outcome in ("dwell", "cap_reached", "not_controlling"):
            # A HELD decision: the EMS WANTED to switch but a guardrail blocked it. Never silent
            # (CLAUDE.md "explainability first — including why it is NOT acting"). Deduped so a
            # recurring hold is explained once per (outcome, desired_mode), not every cycle. With
            # the manual/return-to-AUTO bypass this only fires for the AUTOMATIC planner now.
            sig = (dec.outcome, dec.desired_mode.value)
            if _held_box["sig"] != sig:
                _held_box["sig"] = sig
                records.append({
                    "summary": f"Battery NOT switched to {dec.desired_mode.value} — {dec.reason}",
                    "detail": {"desired_mode": dec.desired_mode.value, "intent": str(dec.intent),
                               "outcome": dec.outcome, "reason": dec.reason,
                               "override_active": override_active}})
        return records

    async def _run_control_cycle() -> None:
        """One operational control cycle: run the (blocking) tick off the event loop, then AUDIT the
        CONFIRMED mode change it reports. Serialised by `_control_lock` so the periodic loop and an
        immediate override-triggered run can't overlap (two concurrent writes to the battery)."""
        if controller is None or dry_run:
            return
        async with _control_lock:
            now = datetime.now(UTC)
            try:
                records = await asyncio.to_thread(_control_tick, now)
            except Exception:
                _log.exception("control tick failed; retry next cycle (fail-safe)")
                return
            for rec in records:
                if audit_store is not None:
                    try:
                        await audit_store.append(now.isoformat(), "battery_decision",
                                                 rec["summary"], rec["detail"])
                    except Exception:
                        _log.warning("failed to write battery-decision audit", exc_info=True)

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

    @app.get("/health/live")
    def live() -> dict:
        return {"status": "alive"}

    def _readiness(now: datetime) -> Readiness:
        """Layered readiness for a control system (energy review #7): alive / dashboard / sensing /
        planning / control. Robust — every input is guarded so health never raises."""
        try:
            dq = _data_quality(now)
        except Exception:
            dq = "unsafe"
        plan_valid = True
        plan_ok = False
        try:
            pp = _current_plan()
            plan_ok = pp is not None and bool(pp[2].slots)
            if pp is not None:
                plan_valid = _validate_plan_obj(pp[2], now).ok
        except Exception:
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
        intent, _reason, override_active, tgt, pw, _v = _effective_intent(now)
        if intent is not None and controller is not None:
            outcome = controller.preview(intent, now, target_soc=tgt, power_w=pw,
                                         observed_mode=_current_mode(now),
                                         manual=override_active,
                                         priority=_car_charging(now)).outcome
        alerts = derive_alerts(snap, dry_run=dry_run, decision_outcome=outcome)
        out = [{"key": a.key, "severity": a.severity, "message": a.message} for a in alerts]
        if override_active:
            ov = override_box["ov"]
            until = ov.expires_at.astimezone().strftime("%H:%M") if ov.expires_at else "?"
            # If the override was HELD (gated on unsafe data), say so — never claim it's "forcing"
            # the requested action when the battery is actually held at self-consumption.
            held = ov.intent is not None and intent is not ov.intent
            msg = (f"Manual override held until {until} — data unsafe, so EMS is holding "
                   "self-consumption instead of forcing the requested action"
                   if held else
                   f"Manual override: forcing {intent.value if intent else '?'} until {until}")
            out.append({"key": "manual_override_active", "severity": "warning", "message": msg})
        # Cluster mismatch (a tower not following the commanded mode) — surfaced prominently, not
        # just in the audit log, because it means part of the battery isn't doing what was asked.
        laggard_sig = _drift_box.get("sig")
        if laggard_sig:
            ips = ", ".join(ip for ip, _mode in laggard_sig)
            out.append({"key": "battery_cluster_mismatch", "severity": "warning",
                        "message": f"Battery cluster mismatch — tower(s) {ips} are not following "
                                   "the commanded mode. The EMS commands the master; a tower that "
                                   "doesn't follow keeps running its own mode."})
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
            intent, reason, override_active, tgt, pw, val = _effective_intent(now)
            if intent is None:
                return None, {"intent": None, "desired_mode": None, "applied": False,
                              "outcome": "no_plan", "reason": "no plan slot for now",
                              "plan_reason": None, "override_active": False,
                              "car_charging": car_charging}
            # preview() is read-only — a GET must never write to the battery or mutate counters.
            # Pass the coalesced observed mode so this poll doesn't read battery mode every cycle.
            d = controller.preview(intent, now, target_soc=tgt, power_w=pw,
                                   observed_mode=_current_mode(now), manual=override_active,
                                   priority=car_charging)
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
                cache_stats = None
        # Long-run diagnostics (review): DB/WAL size + sample row counts, and recorder health so a
        # stuck recorder (full disk, DB lock, dead device) is VISIBLE, not just inferred from stale.
        storage = None
        if store is not None:
            try:
                storage = await store.db_stats()
            except Exception:
                storage = None
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
                asyncio.create_task(_run_control_cycle())  # apply now, don't wait a full cycle
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
            asyncio.create_task(_run_control_cycle())
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
        intent, dreason, override_active, tgt, _pw, _v = _effective_intent(now)
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
                    "price_by": {p.start: p.eur_per_kwh for p in prices_}}

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

    @app.get("/api/battery-plan")
    async def battery_plan() -> dict:
        """Homeowner-facing battery confidence contract: the answer first, then graph proof.

        This deliberately reuses the same projection/story helpers as /api/energy-forecast and
        /api/energy-story so the plan sentence, graph and diagnostics cannot drift apart.
        """
        now = datetime.now(UTC)
        reserve_pct = settings_cache["battery.min_reserve_soc"]
        quality = _data_quality(now)
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
        _intent, reason, _override, _target, _power, validation = _effective_intent(fp["now"])

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
            pass
        return {"now": now.isoformat(), "sunrise": sunrise, "sunset": sunset,
                "cloud_cover": cloud_cover}

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
        grid_factor = float(settings_cache.get("reporting.grid_co2_factor", 0.27))
        gas_factor = float(settings_cache.get("reporting.gas_co2_factor", 1.78))
        # Stored prices so the best-price score is right for HISTORICAL windows too, not just
        # whatever the live feed still carries.
        prices = await _window_price_slots(start.astimezone(UTC).isoformat(),
                                           end.astimezone(UTC).isoformat())
        # Future / no store → an honest empty report (has_data False), never an error.
        if store is None or start > now_local:
            resp = build_report([], [], prices, period=period, start=start, end=end, label=label,
                                partial=partial, grid_factor=grid_factor,
                                gas_factor=gas_factor).to_dict()
            resp["series"] = build_series([], [], period=period, start=start, end=end, tz=site_tz)
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
        resp = build_report(raw, der, prices, period=period, start=start, end=end, label=label,
                            partial=partial, grid_factor=grid_factor,
                            gas_factor=gas_factor, gas_m3=gas).to_dict()
        # The behavior series (P1/house/car/solar per bucket) rides on the same rows/window.
        resp["series"] = build_series(raw, der, period=period, start=start, end=end, tz=site_tz)
        return resp

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
        q_end = min(nxt, now_local + timedelta(minutes=1))
        # Cadence-aware per-day cap (finding 10): sized to the recorder frequency, not a fixed
        # 3000 that would truncate a finer sampling rate.
        day_limit = history_row_cap((nxt - cur).total_seconds(), _sample_cadence_seconds())
        raw = await store.raw_between(cur.astimezone(UTC).isoformat(),
                                      q_end.astimezone(UTC).isoformat(), limit=day_limit)
        price_rows = await store.prices_between(cur.astimezone(UTC).isoformat(),
                                                nxt.astimezone(UTC).isoformat())
        f = day_finance(raw, price_rows, day=day_label,
                        degradation_eur_per_kwh=degradation).to_dict()
        f["calc_v"] = _FINANCE_CALC_VERSION
        if completed:
            await store.upsert_daily_finance(day_label, f)
        return f

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
        days: list[dict] = []
        cur = start
        while cur < end and cur <= now_local and store is not None:
            days.append(await _ensure_day_finance(cur.date()))
            cur = cur + timedelta(days=1)

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

    @app.get("/api/export/package")
    async def export_package_endpoint(days: int = Query(default=90, ge=1, le=400)) -> Response:
        """One ZIP: the recorded history as analytics-ready CSVs (energy, prices, daily finance,
        audit trail) plus a manifest for validating production operation. Read-only and privacy-safe
        to share: the manifest carries only the replay-safe settings subset — no tokens, IPs or
        location."""
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
        if store is not None:
            row_cap = min(600_000, days * 24 * 60 + 1000)  # ~one row/min ceiling over the window
            raw = await store.raw_between(start_iso, end_iso, limit=row_cap)
            derived = await store.derived_between(start_iso, end_iso, limit=row_cap)
            prices = await store.prices_between(start_iso, end_iso)
            forecasts = await store.forecasts_between(start_iso, end_iso)
            plan = await store.plan_history_between(start_iso, end_iso)
            gas = await store.gas_between(start_iso, end_iso)
            # Self-complete the window before reading it back: `daily_finance` rows are otherwise
            # only ever written when a finance view for that day was requested (/api/finance), so
            # a day nobody looked at is silently absent from the export. Backfill every COMPLETED
            # local day the export window touches (already bounded by `days` <= 400) so
            # daily_finance.csv covers the whole window, not just previously-viewed days. One bad
            # day must not fail the whole export — best-effort per day.
            today_local = now.astimezone(site_tz).date()
            backfill_day = start.astimezone(site_tz).date()
            while backfill_day < today_local:
                try:
                    await _ensure_day_finance(backfill_day)
                except Exception:
                    _log.exception(
                        "export/package: failed to backfill daily_finance for %s", backfill_day)
                backfill_day += timedelta(days=1)
            fin_rows = await store.daily_finance_between(
                start.date().isoformat(), (now.date() + timedelta(days=1)).isoformat())
            finance = [r["data"] for r in fin_rows]
        if audit_store is not None:
            audit = list(reversed(await audit_store.recent(limit=5000)))  # oldest→newest
        # Production-validation payload — privacy-safe (only the replay-safe settings, no IPs /
        # tokens / location). Lets a reviewer see run mode, the planner knobs in effect, and live
        # health (data quality, whether the battery capability probed, recorder liveness).
        validation = {
            "operational": {"dry_run": dry_run, "dev_mode": dev_mode, "timezone": str(tz)},
            "config": {k: settings_cache.get(k)
                       for k in _REPLAY_SETTING_KEYS if k in settings_cache},
            "health": {
                "data_quality": _data_quality(now),
                "capability_present": _capability_box["cap"] is not None,
                "recorder": recorder.health() if recorder is not None else None,
            },
            "incidents": expkg.incident_rollup(audit),
        }
        counts = {"raw_samples": len(raw), "derived_samples": len(derived),
                  "prices": len(prices), "forecasts": len(forecasts),
                  "daily_finance": len(finance), "audit_log": len(audit),
                  "plan_history": len(plan), "gas": len(gas)}
        saved_vals = [d["saved_eur"] for d in finance if d.get("saved_eur") is not None]
        saved_total = round(sum(saved_vals), 2) if saved_vals else None
        window = {"start": start_iso, "end": end_iso}
        fc_skill = forecast_error(forecasts, raw)
        members = {
            "raw_samples.csv": expkg.rows_to_csv(raw, expkg.RAW_COLUMNS),
            "derived_samples.csv": expkg.rows_to_csv(derived, expkg.DERIVED_COLUMNS),
            "prices.csv": expkg.rows_to_csv(prices, expkg.PRICE_COLUMNS),
            "forecasts.csv": expkg.rows_to_csv(forecasts, expkg.FORECAST_COLUMNS),
            "daily_finance.csv": expkg.rows_to_csv(finance, expkg.FINANCE_COLUMNS),
            "audit_log.csv": expkg.rows_to_csv(audit, expkg.AUDIT_COLUMNS),
            "plan_history.csv": expkg.rows_to_csv(plan, expkg.PLAN_COLUMNS),
            "gas.csv": expkg.rows_to_csv(gas, expkg.GAS_COLUMNS),
            "manifest.json": expkg.build_manifest(
                generated_at=now.isoformat(), app_version=expkg.app_version(),
                window_start=start_iso, window_end=end_iso, counts=counts, extra=validation,
            ),
            "README.md": expkg.readme_text(),
            "validation_summary.txt": expkg.validation_summary(
                generated_at=now.isoformat(), app_version=expkg.app_version(), window=window,
                counts=counts, validation=validation, saved_total_eur=saved_total,
                forecast_skill=fc_skill,
            ),
        }
        data = expkg.build_zip(members)
        fname = f"ems-export-{now.strftime('%Y%m%d')}.zip"
        return Response(content=data, media_type="application/zip",
                        headers={"Content-Disposition": f'attachment; filename="{fname}"'})

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
        refreshed = effective_settings(await settings_store.all())
        settings_cache.clear()
        settings_cache.update(refreshed)
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
            pass
        try:
            intent, reason, *_ = _effective_intent(now)
            if intent is not None and reason:
                items.append({"key": "why_mode", "question": "Why is it in this mode?",
                              "answer": reason})
        except Exception:
            pass
        try:
            need = _night_target_soc(_current_soc(now))
            items.append({"key": "tonight", "question": "What happens tonight?",
                          "answer": need.reason})
        except Exception:
            pass
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
