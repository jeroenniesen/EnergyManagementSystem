"""#136: main must pass cfg.dry_run into build_wiring (wiring must not be droppable)."""
from __future__ import annotations

import asyncio
from zoneinfo import ZoneInfo

from ems.config import Config
from ems.connection import (
    SETTINGS_FORCED_DRY_RUN_REASON,
    effective_connection,
    watch_only_block_reason,
)
from ems.storage.settings import SettingsStore


def _seed_settings(db: str, values: dict) -> None:
    async def go() -> None:
        s = SettingsStore(db)
        await s.init()
        await s.set_many(values)

    asyncio.run(go())


def test_build_app_passes_cfg_dry_run_into_build_wiring(monkeypatch, tmp_path):
    from ems import main as main_mod
    from ems.connection import build_wiring as real_build_wiring

    db = tmp_path / "ems.sqlite"
    cfg = Config(
        timezone="Europe/Amsterdam",
        dev_mode="live",
        dry_run=True,  # the value that must reach build_wiring
        web_port=8080,
        db_path=str(db),
        cycle_seconds=300.0,
        retention_days=90,
    )
    # Seed operational + live battery + live Tibber so a dropped force_dry_run would wrongly arm.
    # Settings watch-only OFF so only config.yaml is forcing (#136 / #171).
    _seed_settings(str(db), {
        "connection.use_live_devices": True,
        "meters.p1_ip": "192.0.2.10",
        "battery.indevolt_ip": "192.0.2.20",
        "control.operational": True,
        "control.dry_run": False,
        "connection.use_live_prices": True,
        "prices.tibber_token": "tok",
    })

    seen: dict[str, object] = {}

    def wrap(eff, tz, cache_store=None, *, force_dry_run=True, config_dry_run=None, **kw):
        seen["force_dry_run"] = force_dry_run
        seen["config_dry_run"] = config_dry_run
        return real_build_wiring(
            eff, tz, cache_store=cache_store,
            force_dry_run=force_dry_run, config_dry_run=config_dry_run, **kw,
        )

    monkeypatch.setattr(main_mod, "load_config", lambda _path: cfg)
    monkeypatch.setattr(main_mod, "build_wiring", wrap)

    app, loaded = main_mod.build_app()
    assert loaded.dry_run is True
    assert seen.get("force_dry_run") is True  # dropping the kwarg would fail this
    assert seen.get("config_dry_run") is True
    assert app.state.application_context.runtime_state["dry_run"] is True


def test_build_app_passes_dry_run_false_when_config_allows_live(monkeypatch, tmp_path):
    from ems import main as main_mod
    from ems.connection import build_wiring as real_build_wiring

    db = tmp_path / "ems.sqlite"
    cfg = Config(
        timezone="Europe/Amsterdam",
        dev_mode="live",
        dry_run=False,
        web_port=8080,
        db_path=str(db),
        cycle_seconds=300.0,
        retention_days=90,
    )
    _seed_settings(str(db), {
        "connection.use_live_devices": True,
        "meters.p1_ip": "192.0.2.10",
        "battery.indevolt_ip": "192.0.2.20",
        "control.operational": True,
        "control.dry_run": False,  # Settings watch-only OFF (#171)
        "connection.use_live_prices": True,
        "prices.tibber_token": "tok",
    })

    seen: dict[str, object] = {}

    def wrap(eff, tz, cache_store=None, *, force_dry_run=True, config_dry_run=None, **kw):
        seen["force_dry_run"] = force_dry_run
        seen["config_dry_run"] = config_dry_run
        return real_build_wiring(
            eff, tz, cache_store=cache_store,
            force_dry_run=force_dry_run, config_dry_run=config_dry_run, **kw,
        )

    monkeypatch.setattr(main_mod, "load_config", lambda _path: cfg)
    monkeypatch.setattr(main_mod, "build_wiring", wrap)

    app, _loaded = main_mod.build_app()
    assert seen.get("force_dry_run") is False
    assert seen.get("config_dry_run") is False
    assert app.state.application_context.runtime_state["dry_run"] is False


def test_build_app_settings_watch_only_forces_dry_run_when_config_allows(monkeypatch, tmp_path):
    """#171: Settings control.dry_run True keeps watch-only even when yaml dry_run is false."""
    from ems import main as main_mod
    from ems.connection import build_wiring as real_build_wiring

    db = tmp_path / "ems.sqlite"
    cfg = Config(
        timezone="Europe/Amsterdam",
        dev_mode="live",
        dry_run=False,
        web_port=8080,
        db_path=str(db),
        cycle_seconds=300.0,
        retention_days=90,
    )
    _seed_settings(str(db), {
        "connection.use_live_devices": True,
        "meters.p1_ip": "192.0.2.10",
        "battery.indevolt_ip": "192.0.2.20",
        "control.operational": True,
        "control.dry_run": True,  # Settings watch-only ON
        "connection.use_live_prices": True,
        "prices.tibber_token": "tok",
    })

    seen: dict[str, object] = {}

    def wrap(eff, tz, cache_store=None, *, force_dry_run=True, config_dry_run=None, **kw):
        seen["force_dry_run"] = force_dry_run
        return real_build_wiring(
            eff, tz, cache_store=cache_store,
            force_dry_run=force_dry_run, config_dry_run=config_dry_run, **kw,
        )

    monkeypatch.setattr(main_mod, "load_config", lambda _path: cfg)
    monkeypatch.setattr(main_mod, "build_wiring", wrap)

    app, _loaded = main_mod.build_app()
    assert seen.get("force_dry_run") is True
    assert app.state.application_context.runtime_state["dry_run"] is True
    # Reason string is closed over into create_app; recompute the same way main does.
    eff = effective_connection(str(db), cfg)
    assert watch_only_block_reason(eff, config_dry_run=False) == SETTINGS_FORCED_DRY_RUN_REASON


def test_load_config_mock_forces_dry_run_even_when_yaml_false(tmp_path):
    # Combined gate (#136 F1): shipped mock + dry_run:false still yields cfg.dry_run True.
    from ems.config import load_config

    p = tmp_path / "config.yaml"
    p.write_text("dev:\n  mode: mock\ncontrol:\n  dry_run: false\n")
    assert load_config(p).dry_run is True


def test_load_config_live_allows_dry_run_false(tmp_path):
    from ems.config import load_config

    p = tmp_path / "config.yaml"
    p.write_text("dev:\n  mode: live\ncontrol:\n  dry_run: false\n")
    assert load_config(p).dry_run is False


def test_status_exposes_primary_watching_cause():
    """#178: /api/status dry_run_reason names the primary cause (not only the narrow block)."""
    from fastapi.testclient import TestClient

    from ems.connection import CONFIG_WATCH_REASON, MOCK_WATCH_REASON, UNARMED_WATCH_REASON
    from ems.sources.mock import MockSource
    from ems.web.api import create_app

    # Mock mode wins even when config_dry_run is false.
    app_mock = create_app(
        MockSource(), dry_run=True, config_dry_run=False, dry_run_block_reason=None,
        dev_mode="mock", tz=ZoneInfo("Europe/Amsterdam"),
    )
    with TestClient(app_mock) as client:
        body = client.get("/api/status").json()
    assert body["dry_run"] is True
    assert body["dry_run_cause"] == "mock"
    assert body["dry_run_reason"] == MOCK_WATCH_REASON
    assert body["live_writing"] is False
    assert body["live_writing_state"] == "no"

    # Config yaml floor.
    app_cfg = create_app(
        MockSource(), dry_run=True, config_dry_run=True, dry_run_block_reason="ignored-narrow",
        dev_mode="live", tz=ZoneInfo("Europe/Amsterdam"),
    )
    with TestClient(app_cfg) as client:
        body = client.get("/api/status").json()
    assert body["dry_run_cause"] == "config_dry_run"
    assert body["dry_run_reason"] == CONFIG_WATCH_REASON
    assert body["live_writing"] is False
    assert body["live_writing_state"] == "no"

    # Unarmed: config allows live, Settings dry_run off, operational default false.
    app_unarmed = create_app(
        MockSource(), dry_run=True, config_dry_run=False, dry_run_block_reason=None,
        dev_mode="live", tz=ZoneInfo("Europe/Amsterdam"),
    )
    # Override settings cache so Settings watch-only is OFF (schema default is True).
    app_unarmed.state.application_context.settings["control.dry_run"] = False
    app_unarmed.state.application_context.settings["control.operational"] = False
    with TestClient(app_unarmed) as client:
        body = client.get("/api/status").json()
    assert body["dry_run_cause"] == "unarmed"
    assert body["dry_run_reason"] == UNARMED_WATCH_REASON
    assert body["live_writing"] is False
    assert body["live_writing_state"] == "no"


def test_status_exposes_observing_grace_when_armed():
    """#178: when dry_run is False but lifecycle is still OBSERVING, reason = observing."""
    from datetime import UTC, datetime

    from fastapi.testclient import TestClient

    from ems.connection import OBSERVING_WATCH_REASON
    from ems.control.mode_controller import ModeController
    from ems.lifecycle import Lifecycle, OwnershipState
    from ems.sources.battery import MockBatteryDriver
    from ems.sources.mock import MockSource
    from ems.web.api import create_app

    lc = Lifecycle(dry_run=False, startup_grace_seconds=120.0)
    lc.start(datetime.now(UTC))
    assert lc.state is OwnershipState.OBSERVING
    ctl = ModeController(MockBatteryDriver(), lc, dry_run=False)
    app = create_app(
        MockSource(), dry_run=False, config_dry_run=False, dry_run_block_reason=None,
        dev_mode="live", tz=ZoneInfo("Europe/Amsterdam"), controller=ctl,
    )
    with TestClient(app) as client:
        body = client.get("/api/status").json()
    assert body["dry_run"] is False
    assert body["dry_run_cause"] == "observing"
    assert body["dry_run_reason"] == OBSERVING_WATCH_REASON
    # F-47: grace is not live writing, and the reason is not Watch-only copy.
    assert body["live_writing"] is False
    assert body["live_writing_state"] == "starting"
    assert "Watch only" not in body["dry_run_reason"]
    assert "Opstarten" in body["dry_run_reason"]


def test_status_live_writing_per_floor_and_when_controlling():
    """#314: /api/status live_writing matches each watch floor, and yes only while controlling."""
    from fastapi.testclient import TestClient

    from ems.control.mode_controller import ModeController
    from ems.lifecycle import Lifecycle, OwnershipState
    from ems.sources.battery import MockBatteryDriver
    from ems.sources.mock import MockSource
    from ems.web.api import create_app

    def status_for(**kwargs) -> dict:
        settings = kwargs.pop("settings", None)
        app = create_app(MockSource(), tz=ZoneInfo("Europe/Amsterdam"), **kwargs)
        if settings:
            app.state.application_context.settings.update(settings)
        with TestClient(app) as client:
            return client.get("/api/status").json()

    replay = status_for(
        dry_run=True, config_dry_run=False, dry_run_block_reason=None, dev_mode="replay",
    )
    assert replay["dry_run_cause"] == "mock"
    assert replay["live_writing"] is False
    assert replay["live_writing_state"] == "no"

    settings_watch = status_for(
        dry_run=True, config_dry_run=False, dry_run_block_reason=None, dev_mode="live",
        settings={
            "control.dry_run": True,
            "control.operational": True,
            "connection.use_live_devices": True,
            "meters.p1_ip": "192.0.2.10",
            "battery.indevolt_ip": "192.0.2.20",
            "connection.use_live_prices": True,
            "prices.tibber_token": "tok",
        },
    )
    assert settings_watch["dry_run_cause"] == "settings_dry_run"
    assert settings_watch["live_writing_state"] == "no"

    no_devices = status_for(
        dry_run=True, config_dry_run=False, dry_run_block_reason=None, dev_mode="live",
        settings={
            "control.dry_run": False,
            "control.operational": True,
            "connection.use_live_devices": False,
            "battery.indevolt_ip": "",
            "connection.use_live_prices": True,
            "prices.tibber_token": "tok",
        },
    )
    assert no_devices["dry_run_cause"] == "no_live_devices"
    assert no_devices["live_writing"] is False

    no_prices = status_for(
        dry_run=True, config_dry_run=False, dry_run_block_reason=None, dev_mode="live",
        settings={
            "control.dry_run": False,
            "control.operational": True,
            "connection.use_live_devices": True,
            "meters.p1_ip": "192.0.2.10",
            "battery.indevolt_ip": "192.0.2.20",
            "connection.use_live_prices": False,
            "prices.tibber_token": "",
        },
    )
    assert no_prices["dry_run_cause"] == "no_live_prices"
    assert no_prices["live_writing_state"] == "no"

    lc = Lifecycle(dry_run=False, startup_grace_seconds=0)
    lc.state = OwnershipState.CONTROLLING
    ctl = ModeController(MockBatteryDriver(), lc, dry_run=False)
    live = status_for(
        dry_run=False, config_dry_run=False, dry_run_block_reason=None, dev_mode="live",
        controller=ctl,
    )
    assert live["dry_run"] is False
    assert live["dry_run_cause"] is None
    assert live["dry_run_reason"] is None
    # A forged controlling lifecycle without live prices and a probed capability is not live.
    assert live["live_writing"] is False
    assert live["live_writing_state"] == "no"
