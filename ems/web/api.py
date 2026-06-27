"""Read-only status API (SPEC §9.1). No device writes in M0a."""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from ems.freshness import FreshnessTracker
from ems.load_model import reconstruct
from ems.planner.rule_based import plan_rule_based
from ems.sense import Recorder
from ems.sources.base import Source
from ems.sources.battery import BatteryDriver
from ems.sources.forecast import SolarForecastSource, day_kwh_p50
from ems.sources.prices import PriceSource, current_price
from ems.storage.history import HistoryStore

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
    static_dir: str | Path | None = None,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        # Guarantee the schema exists before anything touches the DB (no caller footgun).
        if store is not None:
            await store.init()
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

    @app.get("/api/plan")
    def plan_endpoint() -> dict:
        if price_source is None:
            return {"created_at": None, "current_intent": None,
                    "current_reason": None, "slots": []}
        now = datetime.now(UTC)
        plan = plan_rule_based(price_source.slots(), now)
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
