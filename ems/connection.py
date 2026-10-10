"""Startup wiring of telemetry sources from the runtime settings (SPEC §9.4 / §5).

Connection settings (which devices/services to use + their addresses) live in the settings store so
they are editable in the UI. They are read **synchronously at startup** here and turned into the
concrete source objects. On first boot the store is seeded from config.yaml + env so the app works
out of the box; thereafter the UI is authoritative. Connection changes take effect on restart.

SAFETY: default path builds READ paths + an UNARMED battery driver with dry_run on. A live writer
is armed only when ``dev.mode: live``, config + Settings ``control.dry_run`` are both false,
UI ``control.operational`` is on, a live Indevolt is configured, AND a live Tibber price source
is wired (config dry_run / mock|replay always wins over Settings — #136 / #126 / #171 / SPEC §11.6).
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
from typing import NamedTuple
from zoneinfo import ZoneInfo

from ems.battery_profile import normalize_tower_ips
from ems.settings import effective_settings

_log = logging.getLogger("ems.connection")

# Surfaced in diagnostics /api/status when config blocks an ON operational toggle (#136 F3).
CONFIG_FORCED_DRY_RUN_REASON = (
    "config forces watch-only (need dev.mode: live and control.dry_run: false in config.yaml, "
    "then restart); UI operational is ON but the battery writer stays unarmed"
)

# Settings-store watch-only floor (#171) — daily ops without editing config.yaml.
SETTINGS_FORCED_DRY_RUN_REASON = (
    "Settings → Watch only is ON (turn it off under Control & safety, then Apply & restart); "
    "UI operational is ON but the battery writer stays unarmed"
)

# #178: primary cause strings for the Watching-only badge / dry_run_reason (operators misread
# a bare "Watching only" as "Settings broken"). Narrow block reasons above stay for diagnostics
# when operational is ON but forced; these cover every common watch-only path.
MOCK_WATCH_REASON = (
    "Demo / mock mode (dev.mode is mock or replay) — battery writes stay off until "
    "config.yaml uses dev.mode: live"
)
CONFIG_WATCH_REASON = (
    "config.yaml forces watch-only (set control.dry_run: false and dev.mode: live, then restart)"
)
SETTINGS_WATCH_REASON = (
    "Settings → Watch only is ON — turn it off under Control & safety, then Apply & restart"
)
UNARMED_WATCH_REASON = (
    "Control is unarmed — turn on Settings → Let the system control the battery, "
    "then Apply & restart (and keep Watch only OFF)"
)
NO_LIVE_PRICES_WATCH_REASON = (
    "Live Tibber prices required for control — enable Use live Tibber prices and set a token, "
    "then Apply & restart (#126)"
)
NO_LIVE_DEVICES_WATCH_REASON = (
    "Live devices / Indevolt IP not configured — enable Use live devices, set meter + battery IPs, "
    "then Apply & restart"
)
OBSERVING_WATCH_REASON = (
    "Startup observing grace — sensors/plan are still validating; EMS will not command yet"
)


def resolve_force_dry_run(eff: dict, *, config_dry_run: bool) -> bool:
    """Whether battery writes must stay off (watch-only).

    ``config_dry_run`` (``cfg.dry_run`` after load_config: yaml ``control.dry_run`` OR
    ``dev.mode`` mock/replay) **always wins** (#136). Settings ``control.dry_run`` is a second
    floor for daily ops without editing yaml (#171). Missing or non-bool store value ⇒ True
    (fail-safe: never silently armed).
    """
    if config_dry_run:
        return True
    val = eff.get("control.dry_run", True)
    if not isinstance(val, bool):
        return True
    return val


def _would_otherwise_arm(eff: dict) -> bool:
    """True when live devices + Indevolt IP + live Tibber would allow arming (#126)."""
    use_live = bool(eff.get("connection.use_live_devices")) and bool(eff.get("meters.p1_ip"))
    if not use_live or not (eff.get("battery.indevolt_ip") or ""):
        return False
    token = (eff.get("prices.tibber_token") or "").strip()
    return bool(eff.get("connection.use_live_prices")) and bool(token)


def watch_only_block_reason(eff: dict, *, config_dry_run: bool) -> str | None:
    """Why writes stay off despite UI operational ON, or None if not that case.

    Distinguishes config.yaml / mock|replay (#136) from Settings watch-only (#171). Only reports
    when operational would otherwise have armed (live devices + Indevolt IP + live Tibber — #126).
    Used by diagnostics (warn when UI asks to control but a floor blocks). For the operator-facing
    Watching-only badge, prefer ``watching_only_primary_reason`` (#178).
    """
    if not bool(eff.get("control.operational")) or not _would_otherwise_arm(eff):
        return None
    if config_dry_run:
        return CONFIG_FORCED_DRY_RUN_REASON
    if resolve_force_dry_run(eff, config_dry_run=False):
        return SETTINGS_FORCED_DRY_RUN_REASON
    return None


def watching_only_primary_reason(
    eff: dict,
    *,
    config_dry_run: bool,
    dry_run: bool,
    dev_mode: str = "live",
    lifecycle_state: str | None = None,
) -> tuple[str | None, str | None]:
    """Primary cause for Watching-only / no battery writes (#178).

    Returns ``(cause_code, human_reason)`` or ``(None, None)`` when EMS is live-controlling
    (or about to be — not dry-run and past observing).

    Priority (first match wins): mock/replay → config.yaml dry_run → Settings watch-only →
    unarmed (operational off) → no live devices → no live prices → observing grace.
    """
    if not dry_run:
        if lifecycle_state in ("observing", "inactive"):
            return "observing", OBSERVING_WATCH_REASON
        return None, None

    if dev_mode in ("mock", "replay"):
        return "mock", MOCK_WATCH_REASON
    if config_dry_run:
        return "config_dry_run", CONFIG_WATCH_REASON
    if resolve_force_dry_run(eff, config_dry_run=False):
        return "settings_dry_run", SETTINGS_WATCH_REASON
    if not bool(eff.get("control.operational")):
        return "unarmed", UNARMED_WATCH_REASON
    use_live = bool(eff.get("connection.use_live_devices")) and bool(eff.get("meters.p1_ip"))
    if not use_live or not (eff.get("battery.indevolt_ip") or ""):
        return "no_live_devices", NO_LIVE_DEVICES_WATCH_REASON
    token = (eff.get("prices.tibber_token") or "").strip()
    if not (bool(eff.get("connection.use_live_prices")) and bool(token)):
        return "no_live_prices", NO_LIVE_PRICES_WATCH_REASON
    # dry_run True with all gates open should not happen; keep a honest fallback.
    return "dry_run", "Watch-only — battery writes are disabled"


def config_forced_dry_run_reason(eff: dict, *, force_dry_run: bool) -> str | None:
    """Why combined force_dry_run blocks arming despite UI operational, or None.

    Prefer ``watch_only_block_reason`` when config vs Settings can be distinguished. Kept for
    callers that only know the combined force flag (and for #136 regression tests).
    """
    if not force_dry_run or not bool(eff.get("control.operational")):
        return None
    if not _would_otherwise_arm(eff):
        return None  # #126 would have blocked arming anyway
    return CONFIG_FORCED_DRY_RUN_REASON


class Wiring(NamedTuple):
    """Named startup wiring for telemetry sources + the (unarmed-by-default) battery driver.

    Field order matches the historical positional 7-tuple from `build_wiring`, so existing
    unpacking callers keep working without a behaviour change (#138 / epic #111).
    """

    source: object
    price_source: object
    solar_forecast: object
    battery_endpoint: object | None
    controller_driver: object
    dev_mode: str
    dry_run: bool


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
        # #171: seed Settings watch-only from yaml so a production host with
        # control.dry_run: false keeps that intent after upgrade (schema default is True).
        "control.dry_run": bool(cfg.dry_run),
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
    force_dry_run: bool = True,
    config_dry_run: bool | None = None,
    http: object | None = None,
) -> Wiring:
    """Build a `Wiring` NamedTuple from effective settings.

    Order is (source, price_source, solar_forecast, battery_endpoint, controller_driver, dev_mode,
    dry_run) — same as the historical positional tuple, so unpacking callers stay valid. The battery
    driver is unarmed and dry_run is True UNLESS control.operational is on AND a live Indevolt is
    configured AND a live Tibber price source is wired AND ``force_dry_run`` is False (then armed +
    dry_run False). ``force_dry_run`` is the combined floor from ``resolve_force_dry_run``
    (config.yaml / mock|replay **or** Settings ``control.dry_run``; default True) and always wins
    over the UI ``control.operational`` toggle (#136 / #171). Mock/demo prices can never lift
    dry_run (#126). Default ``force_dry_run=True`` is fail-safe if a caller omits the kwarg.

    ``battery_endpoint`` is the **same** object as ``controller_driver`` so `/api/battery` and
    diagnostics probe the control path (live used to set it to None → false "no battery driver").

    Optional ``config_dry_run`` (raw ``cfg.dry_run``) refines the startup WARNING so config vs
    Settings watch-only can be told apart; when omitted, logging uses the combined force flag.

    `cache_store` (optional) is handed to the rate-limited external sources (Tibber, Forecast.Solar)
    so they warm-start from a persisted snapshot after a restart and don't immediately refetch.

    Optional ``http`` is an ``HttpRuntime`` (shared sync client + named timeout profiles). When
    present, LAN/cloud transports are injected so callers share one connection pool; when absent,
    sources keep their one-shot defaults (tests / scripts)."""
    from ems.sources.battery import MockBatteryDriver
    from ems.sources.mock import MockSource

    use_live_devices = bool(eff.get("connection.use_live_devices")) and bool(
        eff.get("meters.p1_ip")
    )
    token = (eff.get("prices.tibber_token") or "").strip()
    live_prices = bool(eff.get("connection.use_live_prices")) and bool(token)
    # Operational requires a real battery AND live prices. force_dry_run (config control.dry_run /
    # mock|replay OR Settings watch-only) always wins over the UI operational toggle (#136/#171).
    # Mock prices keep dry_run (#126).
    operational = False
    # Optional shared-client factories (Phase 1 httpx pooling).
    hw_get = None
    solcast_get = None
    fs_get = None
    tibber_post = None
    if http is not None:
        from ems.http_client import (
            make_json_get,
            make_json_get_headers,
            make_tibber_post,
        )

        hw_get = make_json_get(http, "lan_read")  # type: ignore[arg-type]
        solcast_get = make_json_get_headers(http, "cloud")  # type: ignore[arg-type]
        fs_get = make_json_get(http, "cloud")  # type: ignore[arg-type]
        tibber_post = make_tibber_post(http, "cloud")  # type: ignore[arg-type]
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
        if config_dry_run is not None:
            blocked = watch_only_block_reason(eff, config_dry_run=config_dry_run)
        else:
            blocked = config_forced_dry_run_reason(eff, force_dry_run=force_dry_run)
        if blocked:
            _log.warning("%s", blocked)
        # F1: ONE DeviceQuiesce per master, shared by the cluster reader and the write driver below,
        # so reads back off while a SetData sequence (+ settle tail) lands on the device's single
        # embedded HTTP server (the charge-fails-under-car-load root cause).
        quiesce = DeviceQuiesce()
        # Read the whole cluster (master + any extra towers) as one logical battery; the
        # dashboard SoC is the capacity-weighted average. Writes still target the master (`ip`).
        tower_ips = _battery_ips(ip, eff.get("battery.indevolt_ips_extra"))
        # A snappy timeout so one flaky tower fails fast instead of stalling the dashboard; the
        # cluster reader just aggregates over whatever responds. Shared client still pools sockets;
        # the 2.5 s float keeps the previous fail-fast read budget.
        if http is not None:
            from ems.http_client import make_indevolt_getdata_post

            readers = [
                IndevoltReadClient(
                    a, port=port, timeout=2.5,
                    rpc_post=make_indevolt_getdata_post(http, a, port, timeout=2.5),  # type: ignore[arg-type]
                )
                for a in tower_ips
            ]
        else:
            readers = [IndevoltReadClient(a, port=port, timeout=2.5) for a in tower_ips]
        battery_reader = (
            IndevoltClusterReader(readers, quiesce=quiesce) if tower_ips else None
        )
        # A missing solar/car meter is left absent (None) — NEVER substituted with the P1 IP. P1 is
        # net grid flow, not PV production or EV load; impersonating corrupts load reconstruction,
        # the EV guard and forecast learning (energy review #2). The signal just degrades.
        solar_ip = eff.get("meters.solar_ip")
        car_ip = eff.get("meters.car_ip")
        source = LiveSource(
            p1=HomeWizardMeter(eff["meters.p1_ip"], http_get=hw_get),
            solar=HomeWizardMeter(str(solar_ip), http_get=hw_get) if solar_ip else None,
            car=HomeWizardMeter(str(car_ip), http_get=hw_get) if car_ip else None,
            battery=battery_reader,
        )
        # Cluster topology always belongs on the driver — armed or not. probe() advertises
        # n × per-tower OpenData ceiling from len(ips); omitting extra_ips on the unarmed path
        # under-reported a 2-tower cluster as 2400 W while settings (and the reader) said 4800
        # (#164). Realtime writes still target the master only when armed (see apply()).
        driver_kwargs = dict(
            port=port,
            charge_power_w=int(eff.get("battery.max_charge_w") or 2000),
            discharge_power_w=int(eff.get("battery.max_discharge_w") or 2000),
            extra_ips=tower_ips[1:],
        )
        if operational:
            # Arm the writer with cluster topology. The driver commands realtime modes through the
            # master and uses every tower only when returning to vendor self-consumption.
            # Write timeouts via shared ``lan_write`` profile (timeout=None); driver keeps its own
            # write_attempts — never httpx Transport retries.
            _http_client = getattr(http, "client", None) if http is not None else None
            controller_driver = IndevoltBatteryDriver(
                ip, armed=True,
                post_factory=lambda a, _p=port, _c=_http_client: make_setdata_post(
                    a, _p, timeout=None, client=_c,
                ),
                quiesce=quiesce,  # F1: same lock as the reader → reads quiesce while a write lands
                **driver_kwargs,
            )
        elif ip:
            controller_driver = IndevoltBatteryDriver(ip, armed=False, **driver_kwargs)
        else:
            controller_driver = MockBatteryDriver()
        # Same driver object for /api/battery + diagnostics probe AND the ModeController writer.
        # Previously live set battery_endpoint=None, so System always showed "no battery driver"
        # even when Indevolt was wired and controlling (energy-expert review #10 / father UI).
        dev_mode, battery_endpoint = "live", controller_driver
    else:
        source = MockSource()
        controller_driver = MockBatteryDriver()
        # One mock instance for both the battery read surface and the controller (not two).
        dev_mode, battery_endpoint = "mock", controller_driver

    # Prices via the adapter registry (#114 slice a). Live Tibber when use_live_prices + token;
    # otherwise the credential-free mock. Fail-safe incomplete Tibber → mock.
    from ems.sources.price_factory import build_price_source

    price_source = build_price_source(
        eff, tz, cache_store=cache_store, use_live=live_prices, http_post=tibber_post,
    )

    # Solar forecast via the adapter registry (SPEC §6.3 / B-14). Live devices + lat/lon unlock
    # the live path; otherwise the built-in model. Provider selection + Solcast→Forecast.Solar
    # fallback live in ems.sources.forecast_factory — not an if/elif chain here.
    from ems.sources.forecast_factory import build_solar_forecast

    solar_forecast = build_solar_forecast(
        eff, tz, cache_store=cache_store, use_live=use_live_devices,
        http_get=fs_get, solcast_http_get=solcast_get,
    )
    # Live writes only when operational (battery + live prices) AND force_dry_run is False.
    # force_dry_run alone keeps dry_run True even if the UI asked for operational (#136 / #171).
    dry_run = not operational or force_dry_run
    return Wiring(
        source=source,
        price_source=price_source,
        solar_forecast=solar_forecast,
        battery_endpoint=battery_endpoint,
        controller_driver=controller_driver,
        dev_mode=dev_mode,
        dry_run=dry_run,
    )


def build_carbon_source(eff: dict, *, http: object | None = None):
    """Build the CarbonSource (roadmap F3, Insights reporting only — never touches control).

    Delegates to `ems.sources.carbon_factory` (#113): `static` (default) is the flat
    `reporting.grid_co2_factor`; `electricitymaps` is the optional live signal when a personal API
    key is set — keyless/unknown names fail safe to static. Kept OUT of `build_wiring`'s return
    tuple deliberately: several callers unpack that tuple by fixed position/arity, and this is
    wired into the Recorder only, not the battery/price/forecast read paths.

    Optional ``http`` (``HttpRuntime``) injects a shared-client GET for ElectricityMaps."""
    from ems.sources.carbon_factory import build_carbon_source as _build

    return _build(eff, http=http)
