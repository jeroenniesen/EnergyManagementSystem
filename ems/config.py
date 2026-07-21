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
    retention_days: int  # history older than this is purged daily (0 = keep forever)
    # Daily rotated online DB backups kept in <db_dir>/backups (SPEC §11). 0 = backups disabled;
    # clamped to [0, 60] on load. Has a default so existing Config(...) construction stays valid.
    backup_keep: int = 7
    # Live device integration (read-only sensing). Defaults keep the credential-free mock path.
    sources_mode: str = "mock"  # mock | live  (live reads the HomeWizard/Indevolt device IPs)
    prices_provider: str = "mock"  # mock | tibber  (tibber needs TIBBER_TOKEN env)
    p1_ip: str = ""
    solar_ip: str = ""
    car_ip: str = ""
    indevolt_ip: str = ""
    indevolt_ips_extra: str = ""  # additional tower IPs, comma-separated (multi-battery cluster)
    indevolt_port: int = 8080
    # The operational control loop re-evaluates every this-many seconds — DECOUPLED from the
    # (slower) recorder cadence so safety reactions like the car-charging guard engage promptly,
    # without forcing a high history-write rate. Only matters in operational mode.
    control_cycle_seconds: float = 60.0
    # Slice 5: an access token unused for this many days stops resolving (idle auto-revoke).
    # 0 disables the check (tokens never idle-expire); clamped >= 0 on load.
    access_token_idle_days: int = 90


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
    auth = data.get("auth", {}) or {}

    dev_mode = dev.get("mode", "mock")
    dry_run = bool(control.get("dry_run", True))
    if dev_mode in ("mock", "replay"):
        dry_run = True  # SPEC §11.6: simulated modes can never write

    # Env overrides let you flip on live sensing for a run without editing config.yaml.
    sources_mode = os.environ.get("EMS_SOURCES") or sources.get("mode", "mock")
    prices_provider = os.environ.get("EMS_PRICES") or prices.get("provider", "mock")
    # EMS_DB_PATH points the whole app at an alternate SQLite file. Used by the e2e harness to boot
    # against an isolated throwaway DB so tests never read the operator's persisted settings or live
    # devices (energy review §VerificationRun / #8). Empty/unset keeps the configured db_path.
    db_path = os.environ.get("EMS_DB_PATH") or str(history.get("db_path", "ems/data/ems.sqlite"))

    # Sampling cadence: production default is 300 s (one history row / 5 min, low device + write
    # load). EMS_CYCLE_SECONDS lets a developer sample faster locally (e.g. =5) without editing the
    # shipped config. The live dashboard tiles update independently via the 30 s coalesced read, so
    # a slow recorder cadence does NOT make the UI feel stale.
    cycle_seconds = float(os.environ.get("EMS_CYCLE_SECONDS") or control.get("cycle_seconds", 300))

    return Config(
        timezone=site.get("timezone", "Europe/Amsterdam"),
        dev_mode=dev_mode,
        dry_run=dry_run,
        web_port=int(web.get("port", 8080)),
        db_path=db_path,
        cycle_seconds=cycle_seconds,
        retention_days=int(history.get("retention_days", 90)),
        # Sanity-bound the retained-backup count (0 disables, 60 ceiling) at the config boundary.
        backup_keep=max(0, min(60, int(history.get("backup_keep", 7)))),
        control_cycle_seconds=float(control.get("control_cycle_seconds", 60)),
        sources_mode=sources_mode,
        prices_provider=prices_provider,
        p1_ip=str(devices.get("p1_ip", "")),
        solar_ip=str(devices.get("solar_ip", "")),
        car_ip=str(devices.get("car_ip", "")),
        indevolt_ip=str(devices.get("indevolt_ip", "")),
        indevolt_ips_extra=str(devices.get("indevolt_ips_extra", "")),
        indevolt_port=int(devices.get("indevolt_port", 8080)),
        access_token_idle_days=max(0, int(auth.get("access_token_idle_days", 90))),
    )
