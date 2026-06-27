"""Read-only status API (SPEC §9.1). No device writes in M0a."""
from __future__ import annotations

from fastapi import FastAPI

from ems.load_model import reconstruct
from ems.sources.base import Source


def create_app(source: Source, *, dry_run: bool, dev_mode: str) -> FastAPI:
    app = FastAPI(title="Smart Energy Manager", version="0.0.1")

    @app.get("/health/live")
    def live() -> dict:
        return {"status": "alive"}

    @app.get("/health/ready")
    def ready() -> dict:
        return {"status": "ready", "dry_run": dry_run, "dev_mode": dev_mode}

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

    return app
