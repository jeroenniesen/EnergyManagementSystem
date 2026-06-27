"""Entrypoint: build the app from config + a source, run uvicorn."""
from __future__ import annotations

import os
from pathlib import Path
from zoneinfo import ZoneInfo

import uvicorn

from ems.config import load_config
from ems.control.mode_controller import ModeController
from ems.freshness import FreshnessTracker
from ems.lifecycle import Lifecycle
from ems.sense import SIGNALS, Recorder
from ems.sources.battery import MockBatteryDriver
from ems.sources.forecast import MockSolarForecastSource
from ems.sources.mock import MockSource
from ems.sources.prices import MockPriceSource
from ems.storage.history import HistoryStore
from ems.storage.settings import SettingsStore
from ems.web.api import create_app

_REPO_ROOT = Path(__file__).parent.parent
# Built SPA (ems/web/static/dist) — present after `npm run build`; absent in pure-API dev.
_STATIC_DIR = Path(__file__).parent / "web" / "static" / "dist"


def _build_sources(cfg, tz):
    """Return (source, price_source, battery_endpoint, dev_mode, dry_run).

    Live mode reads the HomeWizard meters + (optionally) the Indevolt battery, READ-ONLY. It never
    wires a battery WRITER and forces dry_run on — we only sense the battery, never command it.
    """
    if cfg.sources_mode == "live":
        from ems.sources.indevolt import IndevoltReadClient
        from ems.sources.live import HomeWizardMeter, LiveSource

        key = os.environ.get("INDEVOLT_KEY") or None
        battery_reader = (
            IndevoltReadClient(cfg.indevolt_ip, key=key, port=cfg.indevolt_port)
            if cfg.indevolt_ip
            else None
        )
        source = LiveSource(
            p1=HomeWizardMeter(cfg.p1_ip),
            solar=HomeWizardMeter(cfg.solar_ip),
            car=HomeWizardMeter(cfg.car_ip),
            battery=battery_reader,
        )
        # No live battery capabilities until the probe (device key) exists -> honest "no driver".
        dev_mode, dry_run, battery_endpoint = "live", True, None
    else:
        source = MockSource()
        dev_mode, dry_run, battery_endpoint = cfg.dev_mode, cfg.dry_run, MockBatteryDriver()

    if cfg.prices_provider == "tibber":
        from ems.sources.tibber import TibberPriceSource

        price_source = TibberPriceSource(os.environ.get("TIBBER_TOKEN", ""), tz=tz)
    else:
        price_source = MockPriceSource(tz)
    return source, price_source, battery_endpoint, dev_mode, dry_run


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
    source, price_source, battery_endpoint, dev_mode, dry_run = _build_sources(cfg, tz)
    recorder = Recorder(source, store, freshness, cycle_seconds=cfg.cycle_seconds)
    solar_forecast = MockSolarForecastSource(tz)  # forecast stays mock (no Solcast key / lat-lon)
    lifecycle = Lifecycle(dry_run=dry_run)
    # The controller's driver is ALWAYS the mock (preview-only, never writes in dry-run). There is
    # deliberately no live battery writer in the codebase — the live path only senses the battery.
    controller = ModeController(MockBatteryDriver(), lifecycle, dry_run=dry_run)
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
