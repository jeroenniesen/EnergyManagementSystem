"""Read-only status API (SPEC §9.1). No device writes in M0a."""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Query
from fastapi.staticfiles import StaticFiles

from ems.load_model import reconstruct
from ems.sources.base import Source
from ems.storage.history import HistoryStore


def create_app(
    source: Source,
    *,
    dry_run: bool,
    dev_mode: str,
    store: HistoryStore | None = None,
    static_dir: str | Path | None = None,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        # Guarantee the schema exists before anything touches the DB (no caller footgun).
        if store is not None:
            await store.init()
        yield

    app = FastAPI(title="Smart Energy Manager", version="0.0.1", lifespan=lifespan)

    @app.get("/health/live")
    def live() -> dict:
        return {"status": "alive"}

    @app.get("/health/ready")
    def ready() -> dict:
        return {"status": "ready", "dry_run": dry_run, "dev_mode": dev_mode}

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

    # Serve the built React/Vite SPA (no runtime CDN). Mounted LAST so /api and /health
    # routes are matched first; html=True serves index.html at "/".
    if static_dir is not None:
        dist = Path(static_dir)
        if (dist / "index.html").exists():
            app.mount("/", StaticFiles(directory=dist, html=True), name="spa")

    return app
