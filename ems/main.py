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
from ems.logging_setup import configure_logging
from ems.sense import SIGNALS, Recorder
from ems.storage.audit import AuditStore
from ems.storage.cache import CacheStore
from ems.storage.control_state import ControlStateStore
from ems.storage.history import HistoryStore
from ems.storage.settings import SettingsStore
from ems.web.api import create_app

_REPO_ROOT = Path(__file__).parent.parent
# Built SPA (ems/web/static/dist) — present after `npm run build`; absent in pure-API dev.
_STATIC_DIR = Path(__file__).parent / "web" / "static" / "dist"

# Route logs to a size-rotated file (24/7 install) at import, so it applies whether the process is
# launched via main() or `uvicorn ems.main:app`. No-op unless EMS_LOG_FILE is set.
configure_logging()


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
    audit_store = AuditStore(str(db_path))
    cache_store = CacheStore(str(db_path))
    cache_store.init()  # ensure the table exists before sources warm-start from it below
    freshness = FreshnessTracker()
    freshness.register(*SIGNALS)
    tz = ZoneInfo(cfg.timezone)
    # Connection + run-mode come from the settings store (UI), seeded from config.yaml + env on
    # first boot. dry_run is True (battery untouched) UNLESS control.operational is on with a live
    # Indevolt — only then is the driver armed and the control loop started.
    eff = effective_connection(str(db_path), cfg)
    source, price_source, solar_forecast, battery_endpoint, controller_driver, dev_mode, dry_run = (
        build_wiring(eff, tz, cache_store=cache_store)
    )
    recorder = Recorder(source, store, freshness, cycle_seconds=cfg.cycle_seconds,
                        price_source=price_source)
    # Startup grace (observe-before-act) is 120s by default; EMS_STARTUP_GRACE_SECONDS lets a
    # debug/test run reach CONTROLLING quickly without waiting two minutes.
    _grace = float(os.environ.get("EMS_STARTUP_GRACE_SECONDS") or 120)
    lifecycle = Lifecycle(dry_run=dry_run, startup_grace_seconds=_grace)
    # Persist the controller's safety counters/dwell/last-action across restarts (SPEC §13.3) so a
    # reboot doesn't reset the daily switch cap or min-dwell, then reload them.
    control_state_store = ControlStateStore(str(db_path))
    control_state_store.init()
    controller = ModeController(controller_driver, lifecycle, dry_run=dry_run,
                                on_state_change=control_state_store.save)
    controller.restore_state(control_state_store.load())
    app = create_app(
        source,
        dry_run=dry_run,
        dev_mode=dev_mode,
        tz=tz,
        store=store,
        freshness=freshness,
        recorder=recorder,
        price_source=price_source,
        solar_forecast=solar_forecast,
        battery=battery_endpoint,
        controller=controller,
        settings_store=settings_store,
        override_store=override_store,
        audit_store=audit_store,
        cache_store=cache_store,
        control_cycle_seconds=cfg.control_cycle_seconds,
        history_retention_days=cfg.retention_days,
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
