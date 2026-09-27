"""Startup wiring of telemetry sources from the runtime settings (SPEC §9.4 / §5).

Connection settings (which devices/services to use + their addresses) live in the settings store so
they are editable in the UI. They are read **synchronously at startup** here and turned into the
concrete source objects. On first boot the store is seeded from config.yaml + env so the app works
out of the box; thereafter the UI is authoritative. Connection changes take effect on restart.

SAFETY: default path builds READ paths + an UNARMED battery driver with dry_run on. A live writer
is armed only when config ``control.dry_run`` is false, UI ``control.operational`` is on, a live
Indevolt is configured, AND a live Tibber price source is wired (config dry_run and mock prices
win — #136 / #126 / SPEC §11.6).
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
from zoneinfo import ZoneInfo

from ems.battery_profile import normalize_tower_ips
from ems.settings import effective_settings

_log = logging.getLogger("ems.connection")


def _seed_from_config(cfg) -> dict:
    """Connection values seeded from config.yaml + env (used only for keys not already stored)."""
    seed: dict[str, object] = {
        "connection.use_live_devices": cfg.sources_mode == "live",
        "connection.use_live_prices": cfg.prices_provider == "tibber",
        "meters.p1_ip": cfg.p1_ip,
        "meters.solar_ip": cfg.solar_ip,
        "meters.car_ip": cfg.car_ip,
        "battery.indevolt_ip": cfg.indevolt_ip,
        "battery.indevolt_ips_extra": cfg.indevolt_ips_extra,
        "battery.indevolt_port": cfg.indevolt_port,
    }
    token = os.environ.get("TIBBER_TOKEN")
    if token:
        seed["prices.tibber_token"] = token
    solcast_key = os.environ.get("SOLCAST_API_KEY")
    if solcast_key:
        seed["solar.solcast_api_key"] = solcast_key
    solcast_rid = os.environ.get("SOLCAST_RESOURCE_ID")
    if solcast_rid:
        seed["solar.solcast_resource_id"] = solcast_rid
    # Don't seed empty strings (they'd just hide the schema default and clutter the store).
    return {k: v for k, v in seed.items() if v not in ("", None)}


def _read_store(db_path: str) -> dict:
    try:
        con = sqlite3.connect(db_path)
        try:
            rows = con.execute("SELECT key, value FROM settings").fetchall()
        finally:
            con.close()
        out = {}
        for k, v in rows:
            try:
                out[k] = json.loads(v)
            except (ValueError, TypeError):
                continue
        return out
    except sqlite3.Error:
        return {}  # table not created yet (first boot) -> empty


def _seed_store(db_path: str, seed: dict) -> None:
    """Write seed values for keys not already present (idempotent first-boot seed)."""
    if not seed:
        return
    con = sqlite3.connect(db_path)
    try:
        con.execute(
            "CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        existing = {r[0] for r in con.execute("SELECT key FROM settings").fetchall()}
        for key, value in seed.items():
            if key not in existing:
                con.execute(
                    "INSERT INTO settings (key, value) VALUES (?, ?)", (key, json.dumps(value))
                )
        con.commit()
    finally:
        con.close()


def _battery_ips(main_ip: str, extra: object) -> list[str]:
    """Ordered, de-duplicated tower IPs: the master first, then any comma-separated extras.
    Blanks are dropped; the master is never listed twice."""
    return list(normalize_tower_ips(main_ip, extra))


def effective_connection(db_path: str, cfg) -> dict:
    """Effective settings (defaults + store), after seeding connection values from config/env."""
    _seed_store(db_path, _seed_from_config(cfg))
    return effective_settings(_read_store(db_path))


def build_wiring(
    eff: dict,
    tz: ZoneInfo,
    cache_store: object | None = None,
    *,
    force_dry_run: bool = False,
):
    """Build (source, price_source, solar_forecast, battery_endpoint, controller_driver, dev_mode,
    dry_run) from effective settings. The battery driver is unarmed and dry_run is True UNLESS
    control.operational is on AND a live Indevolt is configured AND a live Tibber price source is
    wired AND ``force_dry_run`` is False (then armed + dry_run False). ``force_dry_run`` comes from
    ``config.yaml`` ``control.dry_run`` (default true) and always wins over the UI
    ``control.operational`` toggle (#136). Mock/demo prices can never lift dry_run (#126).

    `cache_store` (optional) is handed to the rate-limited external sources (Tibber, Forecast.Solar)
    so they warm-start from a persisted snapshot after a restart and don't immediately refetch."""
    from ems.sources.battery import MockBatteryDriver
    from ems.sources.mock import MockSource
    from ems.sources.prices import MockPriceSource

    use_live_devices = bool(eff.get("connection.use_live_devices")) and bool(
        eff.get("meters.p1_ip")
    )
    token = (eff.get("prices.tibber_token") or "").strip()
    live_prices = bool(eff.get("connection.use_live_prices")) and bool(token)
    # Operational requires a real battery AND live prices. force_dry_run (config control.dry_run)
    # always wins over the UI operational toggle (#136). Mock/demo prices keep dry_run (#126).
    operational = False
    if use_live_devices:
        from ems.sources.indevolt import (
            DeviceQuiesce,
            IndevoltClusterReader,
            IndevoltReadClient,
        )
        from ems.sources.indevolt_driver import IndevoltBatteryDriver, make_setdata_post
        from ems.sources.live import HomeWizardMeter, LiveSource

        ip = eff.get("battery.indevolt_ip") or ""
        port = int(eff.get("battery.indevolt_port") or 8080)
        operational = (
            bool(eff.get("control.operational"))
            and bool(ip)
            and live_prices
            and not force_dry_run
        )
        # F1: ONE DeviceQuiesce per master, shared by the cluster reader and the write driver below,
        # so reads back off while a SetData sequence (+ settle tail) lands on the device's single
        # embedded HTTP server (the charge-fails-under-car-load root cause).
        quiesce = DeviceQuiesce()
        # Read the whole cluster (master + any extra towers) as one logical battery; the
        # dashboard SoC is the capacity-weighted average. Writes still target the master (`ip`).
        tower_ips = _battery_ips(ip, eff.get("battery.indevolt_ips_extra"))
        # A snappy timeout so one flaky tower fails fast instead of stalling the dashboard; the
        # cluster reader just aggregates over whatever responds.
        battery_reader = (
            IndevoltClusterReader([IndevoltReadClient(a, port=port, timeout=2.5)
                                   for a in tower_ips], quiesce=quiesce)
            if tower_ips
            else None
        )
        # A missing solar/car meter is left absent (None) — NEVER substituted with the P1 IP. P1 is
        # net grid flow, not PV production or EV load; impersonating corrupts load reconstruction,
        # the EV guard and forecast learning (energy review #2). The signal just degrades.
        solar_ip = eff.get("meters.solar_ip")
        car_ip = eff.get("meters.car_ip")
        source = LiveSource(
            p1=HomeWizardMeter(eff["meters.p1_ip"]),
            solar=HomeWizardMeter(str(solar_ip)) if solar_ip else None,
            car=HomeWizardMeter(str(car_ip)) if car_ip else None,
            battery=battery_reader,
        )
        if operational:
            # Arm the writer with cluster topology. The driver commands realtime modes through the
            # master and uses every tower only when returning to vendor self-consumption.
            controller_driver = IndevoltBatteryDriver(
                ip, port=port, armed=True,
                charge_power_w=int(eff.get("battery.max_charge_w") or 2000),
                discharge_power_w=int(eff.get("battery.max_discharge_w") or 2000),
                extra_ips=tower_ips[1:],
                # Generous write timeout + retry: the device is slow under shared load (HA + app +
                # cluster) and a too-tight timeout false-failed the charge, triggering the AUTO-
                # revert spiral. A timeout now raises BatteryWriteUnconfirmed (hold, don't revert).
                post_factory=lambda a, _p=port: make_setdata_post(a, _p, timeout=8.0),
                quiesce=quiesce,  # F1: same lock as the reader → reads quiesce while a write lands
            )
        elif ip:
            controller_driver = IndevoltBatteryDriver(
                ip, port=port, armed=False,
                charge_power_w=int(eff.get("battery.max_charge_w") or 2000),
                discharge_power_w=int(eff.get("battery.max_discharge_w") or 2000),
            )
        else:
            controller_driver = MockBatteryDriver()
        dev_mode, battery_endpoint = "live", None
    else:
        source = MockSource()
        controller_driver = MockBatteryDriver()
        dev_mode, battery_endpoint = "mock", MockBatteryDriver()

    if live_prices:
        from ems.sources.tibber import TibberPriceSource

        price_source = TibberPriceSource(token, tz=tz, cache_store=cache_store)
    else:
        price_source = MockPriceSource(tz)

    # Solar forecast via the adapter registry (SPEC §6.3 / B-14). Live devices + lat/lon unlock
    # the live path; otherwise the built-in model. Provider selection + Solcast→Forecast.Solar
    # fallback live in ems.sources.forecast_factory — not an if/elif chain here.
    from ems.sources.forecast_factory import build_solar_forecast

    solar_forecast = build_solar_forecast(
        eff, tz, cache_store=cache_store, use_live=use_live_devices,
    )
    # Live writes only when operational (battery + live prices) AND config did not force dry-run.
    # force_dry_run alone keeps dry_run True even if the UI asked for operational (#136 / #126).
    dry_run = not operational or force_dry_run
    return (source, price_source, solar_forecast, battery_endpoint, controller_driver, dev_mode,
            dry_run)


def build_carbon_source(eff: dict):
    """Build the CarbonSource (roadmap F3, Insights reporting only — never touches control) per
    `reporting.carbon_signal`: `static` (default) is the flat `reporting.grid_co2_factor`, always
    available; `electricitymaps` is the optional live signal, only when a personal API key is set —
    a configured-but-keyless live signal falls back to static with a one-line warning rather than
    silently doing nothing. Kept OUT of `build_wiring`'s return tuple deliberately: several callers
    unpack that tuple by fixed position/arity, and this is wired into the Recorder only, not the
    battery/price/forecast read paths."""
    from ems.sources.carbon import ElectricityMapsCarbonSource, StaticCarbonSource

    factor = float(eff.get("reporting.grid_co2_factor") or 0.27)
    if eff.get("reporting.carbon_signal") == "electricitymaps":
        api_key = eff.get("reporting.electricitymaps_api_key") or ""
        if api_key:
            return ElectricityMapsCarbonSource(api_key)
        _log.warning(
            "reporting.carbon_signal=electricitymaps but no API key is set; "
            "using the flat grid CO2 factor instead"
        )
    return StaticCarbonSource(factor)
