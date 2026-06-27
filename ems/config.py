"""Minimal effective-config loader (SPEC §9). Expanded in later milestones."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class Config:
    timezone: str
    dev_mode: str
    dry_run: bool
    web_port: int
    db_path: str
    cycle_seconds: float


def load_config(path: str | Path) -> Config:
    data = yaml.safe_load(Path(path).read_text()) or {}
    site = data.get("site", {}) or {}
    dev = data.get("dev", {}) or {}
    control = data.get("control", {}) or {}
    web = data.get("web", {}) or {}
    history = data.get("history", {}) or {}

    dev_mode = dev.get("mode", "mock")
    dry_run = bool(control.get("dry_run", True))
    if dev_mode in ("mock", "replay"):
        dry_run = True  # SPEC §11.6: simulated modes can never write

    return Config(
        timezone=site.get("timezone", "Europe/Amsterdam"),
        dev_mode=dev_mode,
        dry_run=dry_run,
        web_port=int(web.get("port", 8080)),
        db_path=str(history.get("db_path", "ems/data/ems.sqlite")),
        cycle_seconds=float(control.get("cycle_seconds", 300)),
    )
