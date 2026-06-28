"""Startup wiring of telemetry sources from the runtime settings (SPEC §9.4 / §5).

Connection settings (which devices/services to use + their addresses) live in the settings store so
they are editable in the UI. They are read **synchronously at startup** here and turned into the
concrete source objects. On first boot the store is seeded from config.yaml + env so the app works
out of the box; thereafter the UI is authoritative. Connection changes take effect on restart.

SAFETY: this only ever builds READ paths + an UNARMED battery driver. dry_run stays forced on; no
live battery writer is constructed (arming is a separate, deliberate step — SPEC §11.6).
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
from zoneinfo import ZoneInfo

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
        "battery.indevolt_port": cfg.indevolt_port,
    }
    token = os.environ.get("TIBBER_TOKEN")
    if token:
        seed["prices.tibber_token"] = token
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


def effective_connection(db_path: str, cfg) -> dict:
    """Effective settings (defaults + store), after seeding connection values from config/env."""
    _seed_store(db_path, _seed_from_config(cfg))
    return effective_settings(_read_store(db_path))


def build_wiring(eff: dict, tz: ZoneInfo):
    """Build (source, price_source, solar_forecast, battery_endpoint, controller_driver, dev_mode,
    dry_run) from effective settings. The battery driver is unarmed and dry_run is True UNLESS
    control.operational is on AND a live Indevolt is configured (then armed + dry_run False)."""
    from ems.sources.battery import MockBatteryDriver
    from ems.sources.forecast import MockSolarForecastSource
    from ems.sources.mock import MockSource
    from ems.sources.prices import MockPriceSource

    use_live_devices = bool(eff.get("connection.use_live_devices")) and bool(
        eff.get("meters.p1_ip")
    )
    # Operational mode only means anything with a real battery to command. It ARMS the driver with
    # a real SetData transport and lifts dry_run. Default off -> dry_run, battery never written.
    operational = False
    if use_live_devices:
        from ems.sources.indevolt import IndevoltReadClient
        from ems.sources.indevolt_driver import IndevoltBatteryDriver, make_setdata_post
        from ems.sources.live import HomeWizardMeter, LiveSource

        ip = eff.get("battery.indevolt_ip") or ""
        port = int(eff.get("battery.indevolt_port") or 8080)
        operational = bool(eff.get("control.operational")) and bool(ip)
        battery_reader = IndevoltReadClient(ip, port=port) if ip else None
        source = LiveSource(
            p1=HomeWizardMeter(eff["meters.p1_ip"]),
            solar=HomeWizardMeter(eff.get("meters.solar_ip") or eff["meters.p1_ip"]),
            car=HomeWizardMeter(eff.get("meters.car_ip") or eff["meters.p1_ip"]),
            battery=battery_reader,
        )
        if operational:
            controller_driver = IndevoltBatteryDriver(
                ip, port=port, armed=True, rpc_post=make_setdata_post(ip, port)
            )
        elif ip:
            controller_driver = IndevoltBatteryDriver(ip, port=port, armed=False)
        else:
            controller_driver = MockBatteryDriver()
        dev_mode, battery_endpoint = "live", None
    else:
        source = MockSource()
        controller_driver = MockBatteryDriver()
        dev_mode, battery_endpoint = "mock", MockBatteryDriver()

    token = eff.get("prices.tibber_token") or ""
    if eff.get("connection.use_live_prices") and token:
        from ems.sources.tibber import TibberPriceSource

        price_source = TibberPriceSource(token, tz=tz)
    else:
        price_source = MockPriceSource(tz)

    # Solar forecast: live Forecast.Solar (keyless, from the configured location) when live devices
    # are on; otherwise the model curve. Forecast.Solar is cached + falls back to the model.
    if use_live_devices and eff.get("site.lat") is not None and eff.get("site.lon") is not None:
        from ems.sources.forecast_solar import ForecastSolarSource

        solar_forecast = ForecastSolarSource(
            tz=tz, lat=float(eff["site.lat"]), lon=float(eff["site.lon"]),
            tilt=float(eff["site.tilt"]), azimuth=float(eff["site.azimuth"]),
            kwp=float(eff["site.kwp"]),
        )
    else:
        solar_forecast = MockSolarForecastSource(tz)
    dry_run = not operational  # operational (armed + live battery) is the ONLY way dry_run lifts
    return (source, price_source, solar_forecast, battery_endpoint, controller_driver, dev_mode,
            dry_run)
