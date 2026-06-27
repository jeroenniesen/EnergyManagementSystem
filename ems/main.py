"""Entrypoint: build the app from config + a source, run uvicorn."""
from __future__ import annotations

from pathlib import Path
from zoneinfo import ZoneInfo

import uvicorn

from ems.config import load_config
from ems.freshness import FreshnessTracker
from ems.sense import SIGNALS, Recorder
from ems.sources.mock import MockSource
from ems.sources.prices import MockPriceSource
from ems.storage.history import HistoryStore
from ems.web.api import create_app

_REPO_ROOT = Path(__file__).parent.parent
# Built SPA (ems/web/static/dist) — present after `npm run build`; absent in pure-API dev.
_STATIC_DIR = Path(__file__).parent / "web" / "static" / "dist"


def build_app():
    cfg = load_config("config.yaml")
    # M0a: only the mock source exists; live sources arrive with the HA client (later M0a task).
    source = MockSource()
    # Anchor a relative db_path to the repo root so it doesn't depend on the process CWD.
    db_path = Path(cfg.db_path)
    if not db_path.is_absolute():
        db_path = _REPO_ROOT / db_path
    db_path.parent.mkdir(parents=True, exist_ok=True)
    store = HistoryStore(str(db_path))
    freshness = FreshnessTracker()
    freshness.register(*SIGNALS)
    recorder = Recorder(source, store, freshness, cycle_seconds=cfg.cycle_seconds)
    price_source = MockPriceSource(ZoneInfo(cfg.timezone))
    app = create_app(
        source,
        dry_run=cfg.dry_run,
        dev_mode=cfg.dev_mode,
        store=store,
        freshness=freshness,
        recorder=recorder,
        price_source=price_source,
        static_dir=_STATIC_DIR,
    )
    return app, cfg


app, _cfg = build_app()


def main() -> None:
    # Reuse the already-built config; don't rebuild the app (which would make a 2nd store/recorder).
    uvicorn.run("ems.main:app", host="0.0.0.0", port=_cfg.web_port)


if __name__ == "__main__":
    main()
