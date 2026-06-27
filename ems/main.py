"""Entrypoint: build the app from config + a source, run uvicorn."""
from __future__ import annotations

import uvicorn

from ems.config import load_config
from ems.sources.mock import MockSource
from ems.web.api import create_app


def build_app():
    cfg = load_config("config.yaml")
    # M0a: only the mock source exists; live sources arrive with the HA client (later M0a task).
    source = MockSource()
    return create_app(source, dry_run=cfg.dry_run, dev_mode=cfg.dev_mode), cfg


app, _cfg = build_app()


def main() -> None:
    _, cfg = build_app()
    uvicorn.run("ems.main:app", host="0.0.0.0", port=cfg.web_port)


if __name__ == "__main__":
    main()
