"""Read-only status API (SPEC §9.1). No device writes in M0a."""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from ems.alerts import data_quality, derive_alerts
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
from ems.domain import BatteryIntent
from ems.freshness import FreshnessTracker
from ems.load_model import reconstruct
from ems.planner.rule_based import PlannerConfig, plan_rule_based
from ems.savings import estimate_daily_savings_eur
from ems.sense import Recorder
from ems.settings import effective_settings, schema_json, validate_settings
from ems.sources.base import Source
from ems.sources.battery import BatteryDriver
from ems.sources.forecast import SolarForecastSource, day_kwh_p50
from ems.sources.prices import PriceSource, current_price
from ems.storage.history import HistoryStore
from ems.storage.settings import SettingsStore

_log = logging.getLogger("ems.recorder")


def _recorder_died(task: asyncio.Task) -> None:
    # The recorder is awaited only at shutdown; surface an unexpected death immediately.
    if not task.cancelled() and (exc := task.exception()) is not None:
        _log.error("Recorder task exited unexpectedly: %s", exc, exc_info=exc)


def create_app(
    source: Source,
    *,
    dry_run: bool,
    dev_mode: str,
    store: HistoryStore | None = None,
    freshness: FreshnessTracker | None = None,
    recorder: Recorder | None = None,
    price_source: PriceSource | None = None,
    solar_forecast: SolarForecastSource | None = None,
    battery: BatteryDriver | None = None,
    controller: ModeController | None = None,
    settings_store: SettingsStore | None = None,
    override_store: SettingsStore | None = None,
    static_dir: str | Path | None = None,
) -> FastAPI:
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

    def _planner_cfg() -> PlannerConfig:
        s = settings_cache
        return PlannerConfig(
            round_trip_efficiency=s["planner.round_trip_efficiency"],
            degradation_eur_per_kwh=s["planner.degradation_eur_per_kwh"],
            risk_margin_eur_per_kwh=s["planner.risk_margin_eur_per_kwh"],
            charge_slots=s["planner.charge_slots"],
            discharge_slots=s["planner.discharge_slots"],
        )

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
        try:
            yield
        finally:
            if task is not None:
                stop.set()
                await task

    app = FastAPI(title="Smart Energy Manager", version="0.0.1", lifespan=lifespan)

    def _current_plan():
        """Single source of the current plan (DRY) so /api/plan, /api/savings, /api/decision and
        /api/alerts all reflect the same computation. Returns (now, prices, plan) or None."""
        if price_source is None:
            return None
        now = datetime.now(UTC)
        prices = price_source.slots()
        return now, prices, plan_rule_based(prices, now, _planner_cfg())

    def _effective_intent(now: datetime):
        """The intent the controller should act on now, honouring an active manual override.
        Returns (intent | None, reason | None, override_active). An active override wins over
        the plan — and works even with no price source (it's a control action, not a forecast)."""
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
        return cur.intent, cur.reason, False

    @app.get("/health/live")
    def live() -> dict:
        return {"status": "alive"}

    @app.get("/health/ready")
    def ready() -> dict:
        return {"status": "ready", "dry_run": dry_run, "dev_mode": dev_mode}

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
        snap = freshness.snapshot(now) if freshness is not None else {}
        # The controller's would-do outcome (read-only preview) feeds battery-failure alerts.
        # Honour an active override so the outcome reflects what the controller would really do.
        outcome: str | None = None
        intent, _reason, override_active = _effective_intent(now)
        if intent is not None and controller is not None:
            outcome = controller.preview(intent, now).outcome
        prices_ok = price_source is not None  # price staleness isn't tracked in freshness yet
        forecast_ok = solar_forecast is not None
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
        return {
            "data_quality": data_quality(snap, prices_ok=prices_ok, forecast_ok=forecast_ok),
            "alerts": out,
        }

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

    @app.get("/api/override")
    def get_override() -> dict:
        now = datetime.now(UTC)
        return {**override_box["ov"].to_dict(now), "options": [i.value for i in BatteryIntent]}

    @app.post("/api/override")
    async def set_override(body: dict | None = None) -> JSONResponse:
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

    @app.get("/api/battery")
    def battery_endpoint() -> dict:
        if battery is None:
            return {"current_mode": None, "capabilities": None}
        cap = battery.probe()
        return {
            "current_mode": battery.current_mode(),
            "capabilities": {
                "services": list(cap.services),
                "energy_mode_options": list(cap.energy_mode_options),
                "has_standby": cap.has_standby,
                "has_grid_charge_switch": cap.has_grid_charge_switch,
                "p1_paired": cap.p1_paired,
                "max_charge_w": cap.max_charge_w,
                "max_discharge_w": cap.max_discharge_w,
            },
        }

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

    @app.get("/api/forecast")
    def forecast() -> dict:
        if solar_forecast is None:
            return {"today_kwh_p50": None, "slots": []}
        slots = solar_forecast.slots()
        return {
            "today_kwh_p50": round(day_kwh_p50(slots), 2),
            "slots": [
                {"start": s.start.isoformat(), "p10_w": s.p10_w, "p50_w": s.p50_w,
                 "p90_w": s.p90_w}
                for s in slots
            ],
        }

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
        return {"schema": schema_json(), "values": dict(settings_cache)}

    @app.post("/api/settings")
    async def post_settings(body: dict | None = None) -> JSONResponse:
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
        return JSONResponse({"values": dict(settings_cache)})

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
