"""Entrypoint: build the app from config + a source, run uvicorn."""
from __future__ import annotations

import os
from pathlib import Path
from zoneinfo import ZoneInfo

import uvicorn

from ems.config import load_config
from ems.connection import build_wiring, effective_connection
from ems.control.mode_controller import ModeController
from ems.freshness import FreshnessTracker
from ems.lifecycle import Lifecycle
from ems.sense import SIGNALS, Recorder
from ems.storage.history import HistoryStore
from ems.storage.settings import SettingsStore
from ems.web.api import create_app

_REPO_ROOT = Path(__file__).parent.parent
# Built SPA (ems/web/static/dist) — present after `npm run build`; absent in pure-API dev.
_STATIC_DIR = Path(__file__).parent / "web" / "static" / "dist"


def build_app():
    cfg = load_config("config.yaml")
    # Anchor a relative db_path to the repo root so it doesn't depend on the process CWD.
    db_path = Path(cfg.db_path)
    if not db_path.is_absolute():
        db_path = _REPO_ROOT / db_path
    db_path.parent.mkdir(parents=True, exist_ok=True)
    store = HistoryStore(str(db_path))
    settings_store = SettingsStore(str(db_path))
    override_store = SettingsStore(str(db_path), table="runtime_state")
    freshness = FreshnessTracker()
    freshness.register(*SIGNALS)
    tz = ZoneInfo(cfg.timezone)
    # Connection (which devices/services) comes from the settings store (UI), seeded from
    # config.yaml + env on first boot. Built read-only; the battery driver is unarmed and dry_run
    # is forced on — no live write path exists (arming is a separate step, Loop 5).
    eff = effective_connection(str(db_path), cfg)
    source, price_source, solar_forecast, battery_endpoint, controller_driver, dev_mode = (
        build_wiring(eff, tz)
    )
    dry_run = True
    recorder = Recorder(source, store, freshness, cycle_seconds=cfg.cycle_seconds)
    lifecycle = Lifecycle(dry_run=dry_run)
    # The controller's driver is the mock (dev) or the real-but-UNARMED Indevolt driver (live).
    # dry_run is forced on for live, so decide() never calls apply(); even if it did, an unarmed
    # driver with no write transport cannot touch the battery.
    controller = ModeController(controller_driver, lifecycle, dry_run=dry_run)
    app = create_app(
        source,
        dry_run=dry_run,
        dev_mode=dev_mode,
        store=store,
        freshness=freshness,
        recorder=recorder,
        price_source=price_source,
        solar_forecast=solar_forecast,
        battery=battery_endpoint,
        controller=controller,
        settings_store=settings_store,
        override_store=override_store,
        # Secret via env (never config/SQLite). Unset -> writes open (dev/LAN); set -> writes
        # require Authorization: Bearer <token>. Reads (the dashboard) are always open.
        web_auth_token=os.environ.get("EMS_WEB_TOKEN") or None,
        static_dir=_STATIC_DIR,
    )
    return app, cfg


app, _cfg = build_app()


def main() -> None:
    # Reuse the already-built config; don't rebuild the app (which would make a 2nd store/recorder).
    uvicorn.run("ems.main:app", host="0.0.0.0", port=_cfg.web_port)


if __name__ == "__main__":
    main()
