"""#136: main must pass cfg.dry_run into build_wiring (wiring must not be droppable)."""
from __future__ import annotations

import asyncio
from zoneinfo import ZoneInfo

from ems.config import Config
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
    # Seed operational + live battery so a dropped force_dry_run would wrongly arm.
    _seed_settings(str(db), {
        "connection.use_live_devices": True,
        "meters.p1_ip": "192.0.2.10",
        "battery.indevolt_ip": "192.0.2.20",
        "control.operational": True,
    })

    seen: dict[str, object] = {}

    def wrap(eff, tz, cache_store=None, *, force_dry_run=True):
        seen["force_dry_run"] = force_dry_run
        return real_build_wiring(eff, tz, cache_store=cache_store, force_dry_run=force_dry_run)

    monkeypatch.setattr(main_mod, "load_config", lambda _path: cfg)
    monkeypatch.setattr(main_mod, "build_wiring", wrap)

    app, loaded = main_mod.build_app()
    assert loaded.dry_run is True
    assert seen.get("force_dry_run") is True  # dropping the kwarg would fail this
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
    })

    seen: dict[str, object] = {}

    def wrap(eff, tz, cache_store=None, *, force_dry_run=True):
        seen["force_dry_run"] = force_dry_run
        return real_build_wiring(eff, tz, cache_store=cache_store, force_dry_run=force_dry_run)

    monkeypatch.setattr(main_mod, "load_config", lambda _path: cfg)
    monkeypatch.setattr(main_mod, "build_wiring", wrap)

    app, _loaded = main_mod.build_app()
    assert seen.get("force_dry_run") is False
    assert app.state.application_context.runtime_state["dry_run"] is False


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


def test_status_exposes_dry_run_reason_when_blocked():
    from fastapi.testclient import TestClient

    from ems.sources.mock import MockSource
    from ems.web.api import create_app

    reason = "config forces watch-only; UI operational is ON"
    app = create_app(
        MockSource(), dry_run=True, dry_run_block_reason=reason, dev_mode="live",
        tz=ZoneInfo("Europe/Amsterdam"),
    )
    with TestClient(app) as client:
        body = client.get("/api/status").json()
    assert body["dry_run"] is True
    assert body["dry_run_reason"] == reason
