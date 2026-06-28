"""Minimal effective-config loader (SPEC §9). Expanded in later milestones."""
from __future__ import annotations

import os
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
    # Live device integration (read-only sensing). Defaults keep the credential-free mock path.
    sources_mode: str = "mock"  # mock | live  (live reads the HomeWizard/Indevolt device IPs)
    prices_provider: str = "mock"  # mock | tibber  (tibber needs TIBBER_TOKEN env)
    p1_ip: str = ""
    solar_ip: str = ""
    car_ip: str = ""
    indevolt_ip: str = ""
    indevolt_ips_extra: str = ""  # additional tower IPs, comma-separated (multi-battery cluster)
    indevolt_port: int = 8080


def load_config(path: str | Path) -> Config:
    data = yaml.safe_load(Path(path).read_text()) or {}
    site = data.get("site", {}) or {}
    dev = data.get("dev", {}) or {}
    control = data.get("control", {}) or {}
    web = data.get("web", {}) or {}
    history = data.get("history", {}) or {}
    sources = data.get("sources", {}) or {}
    prices = data.get("prices", {}) or {}
    devices = data.get("devices", {}) or {}

    dev_mode = dev.get("mode", "mock")
    dry_run = bool(control.get("dry_run", True))
    if dev_mode in ("mock", "replay"):
        dry_run = True  # SPEC §11.6: simulated modes can never write

    # Env overrides let you flip on live sensing for a run without editing config.yaml.
    sources_mode = os.environ.get("EMS_SOURCES") or sources.get("mode", "mock")
    prices_provider = os.environ.get("EMS_PRICES") or prices.get("provider", "mock")

    return Config(
        timezone=site.get("timezone", "Europe/Amsterdam"),
        dev_mode=dev_mode,
        dry_run=dry_run,
        web_port=int(web.get("port", 8080)),
        db_path=str(history.get("db_path", "ems/data/ems.sqlite")),
        cycle_seconds=float(control.get("cycle_seconds", 300)),
        sources_mode=sources_mode,
        prices_provider=prices_provider,
        p1_ip=str(devices.get("p1_ip", "")),
        solar_ip=str(devices.get("solar_ip", "")),
        car_ip=str(devices.get("car_ip", "")),
        indevolt_ip=str(devices.get("indevolt_ip", "")),
        indevolt_ips_extra=str(devices.get("indevolt_ips_extra", "")),
        indevolt_port=int(devices.get("indevolt_port", 8080)),
    )
