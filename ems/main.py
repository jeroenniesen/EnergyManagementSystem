"""Entrypoint: build the app from config + a source, run uvicorn."""
from __future__ import annotations

from pathlib import Path

import uvicorn

from ems.config import load_config
from ems.freshness import FreshnessTracker
from ems.sense import SIGNALS, Recorder
from ems.sources.mock import MockSource
from ems.storage.history import HistoryStore
from ems.web.api import create_app

# Built SPA (ems/web/static/dist) — present after `npm run build`; absent in pure-API dev.
_STATIC_DIR = Path(__file__).parent / "web" / "static" / "dist"


def build_app():
    cfg = load_config("config.yaml")
    # M0a: only the mock source exists; live sources arrive with the HA client (later M0a task).
    source = MockSource()
    Path(cfg.db_path).parent.mkdir(parents=True, exist_ok=True)
    store = HistoryStore(cfg.db_path)
    freshness = FreshnessTracker()
    freshness.register(*SIGNALS)
    recorder = Recorder(source, store, freshness, cycle_seconds=cfg.cycle_seconds)
    app = create_app(
        source,
        dry_run=cfg.dry_run,
        dev_mode=cfg.dev_mode,
        store=store,
        freshness=freshness,
        recorder=recorder,
        static_dir=_STATIC_DIR,
    )
    return app, cfg


app, _cfg = build_app()


def main() -> None:
    _, cfg = build_app()
    uvicorn.run("ems.main:app", host="0.0.0.0", port=cfg.web_port)


if __name__ == "__main__":
    main()
