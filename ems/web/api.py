"""Read-only status API (SPEC §9.1). No device writes in M0a."""
from __future__ import annotations

import asyncio
import csv
import io
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
from ems.planner.charge_need import compute_charge_need
from ems.planner.explain import build_plan_detail, plan_metrics, summarize_projection
from ems.planner.load_profile import build_load_profile
from ems.planner.projection import BatteryModel, project_energy
from ems.planner.rule_based import PlannerConfig, plan_rule_based
from ems.planner.strategy import build_plan, select_strategy
from ems.planner.summer import SummerConfig, sunset_after
from ems.retrospect import build_past_story, past_headline
from ems.savings import estimate_daily_savings_eur
from ems.sense import Recorder
from ems.settings import (
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
    # The live SoC read (battery cluster) is slow; cache it briefly so a single dashboard poll —
    # which fans out to several plan-consuming endpoints — doesn't read the battery many times over.
    _soc_cache: dict[str, Any] = {"soc": None, "at": None}

    def _current_soc(now: datetime) -> float:
        cached_at = _soc_cache["at"]
        if cached_at is not None and (now - cached_at).total_seconds() < 30:
            return _soc_cache["soc"]
        try:
            soc = float(source.read().soc_pct)
        except Exception:
            soc = _soc_cache["soc"] if _soc_cache["soc"] is not None else 0.0
        _soc_cache["soc"], _soc_cache["at"] = soc, now
        return soc

    def _night_target_soc(soc_pct: float):
        """The night-carry target (overnight load + reserve + floor), via compute_charge_need."""
        return compute_charge_need(
            soc_pct=soc_pct, usable_kwh=settings_cache["battery.usable_kwh"],
            min_reserve_soc=settings_cache["battery.min_reserve_soc"],
            night_reserve_kwh=settings_cache["battery.night_reserve_kwh"],
            overnight_load_kwh=settings_cache["battery.overnight_load_kwh"],
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
        if override_store is not None:
            await override_store.init()
            stored = await override_store.all()
            override_box["ov"] = override_from_stored(
                stored.get(_OV_INTENT), stored.get(_OV_EXP)
            )
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
        try:
            yield
        finally:
            stop.set()
            for t in (task, control_task):
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
        # Only the summer strategy needs the (slow) live SoC + the solar forecast.
        soc = _current_soc(now) if strategy == "summer" else 0.0
        forecast = (solar_forecast.slots()
                    if strategy == "summer" and solar_forecast is not None else [])
        plan = build_plan(
            strategy, prices=prices, forecast=forecast, now=now, soc_pct=soc,
            winter_cfg=_planner_cfg(), summer_cfg=_summer_cfg(soc),
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
            return ov.intent, f"manual override: {ov.intent.value} until {until}", True
        pp = _current_plan()
        if pp is None:
            return None, None, False
        cur = pp[2].intent_at(now)
        if cur is None:
            return None, None, False
        safe, fs_reason = failsafe_intent(cur.intent, _data_quality(now))
        if fs_reason is not None:
            return safe, fs_reason, False
        return cur.intent, cur.reason, False

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
    def decision_endpoint() -> dict:
        # What the controller would do right now, and why. An active override wins over the plan.
        if controller is None:
            return {"intent": None, "desired_mode": None, "applied": False,
                    "outcome": "unconfigured", "reason": "no controller",
                    "plan_reason": None, "override_active": False}
        now = datetime.now(UTC)
        intent, reason, override_active = _effective_intent(now)
        if intent is None:
            return {"intent": None, "desired_mode": None, "applied": False,
                    "outcome": "no_plan", "reason": "no plan slot for now",
                    "plan_reason": None, "override_active": False}
        # preview() is read-only — a GET must never write to the battery or mutate counters.
        d = controller.preview(intent, now)
        return {
            "intent": d.intent,
            "desired_mode": d.desired_mode,
            "applied": d.applied,
            "outcome": d.outcome,
            "reason": d.reason,
            "plan_reason": reason,
            "override_active": override_active,
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
        load_by = {s.start: profile.expected_w(s.start) for s in plan.slots}
        # Size grid-charging to the night-carry target (overnight load + reserve), not to full.
        need = compute_charge_need(
            soc_pct=soc, usable_kwh=settings_cache["battery.usable_kwh"],
            min_reserve_soc=settings_cache["battery.min_reserve_soc"],
            night_reserve_kwh=settings_cache["battery.night_reserve_kwh"],
            overnight_load_kwh=settings_cache["battery.overnight_load_kwh"],
        )
        projected = project_energy(
            plan.slots, start_soc_pct=soc, solar_w_by=solar_by,
            load_w_by=load_by, model=_battery_model(),
            charge_target_soc_pct=need.target_soc_pct,
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
