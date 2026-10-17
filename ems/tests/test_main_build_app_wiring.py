"""Hermetic regressions for ``ems.main.build_app()`` wiring not covered elsewhere."""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from unittest.mock import sentinel
from zoneinfo import ZoneInfo

import pytest

from ems.config import Config
from ems.connection import build_wiring as real_build_wiring
from ems.sense import SIGNALS
from ems.storage.control_state import ControlStateStore
from ems.storage.settings import SettingsStore


def _seed_settings(db: str, values: dict) -> None:
    async def go() -> None:
        s = SettingsStore(db)
        await s.init()
        await s.set_many(values)

    asyncio.run(go())


def _mock_cfg(db_path: str, **overrides) -> Config:
    base = {
        "timezone": "Europe/Amsterdam",
        "dev_mode": "mock",
        "dry_run": True,
        "web_port": 8080,
        "db_path": db_path,
        "cycle_seconds": 300.0,
        "retention_days": 90,
        "backup_keep": 3,
        "control_cycle_seconds": 45.0,
        "web_auth_token": "",
    }
    base.update(overrides)
    return Config(**base)


def _patch_load_config(monkeypatch, cfg: Config) -> None:
    from ems import main as main_mod

    monkeypatch.setattr(main_mod, "load_config", lambda _path: cfg)


@pytest.fixture
def main_mod():
    from ems import main as main_mod

    return main_mod


def test_freshness_registers_prices_forecast_only_when_wired(monkeypatch, tmp_path, main_mod):
    db = tmp_path / "ems.sqlite"
    cfg = _mock_cfg(str(db))
    _patch_load_config(monkeypatch, cfg)

    price_sentinel = object()
    forecast_sentinel = object()

    def wrap_unwired(eff, tz, cache_store=None, **kw):
        w = real_build_wiring(eff, tz, cache_store=cache_store, **kw)
        return w._replace(price_source=None, solar_forecast=None)

    monkeypatch.setattr(main_mod, "build_wiring", wrap_unwired)
    app, _ = main_mod.build_app()
    ctx = app.state.application_context
    snap = ctx.freshness.snapshot(datetime.now(UTC))
    assert set(snap) == set(SIGNALS)
    assert "prices" not in snap and "forecast" not in snap

    def wrap_wired(eff, tz, cache_store=None, **kw):
        w = real_build_wiring(eff, tz, cache_store=cache_store, **kw)
        return w._replace(price_source=price_sentinel, solar_forecast=forecast_sentinel)

    monkeypatch.setattr(main_mod, "build_wiring", wrap_wired)
    app2, _ = main_mod.build_app()
    snap2 = app2.state.application_context.freshness.snapshot(datetime.now(UTC))
    assert set(SIGNALS) <= set(snap2)
    assert "prices" in snap2 and "forecast" in snap2


def test_recorder_receives_price_forecast_and_carbon_sources(monkeypatch, tmp_path, main_mod):
    db = tmp_path / "ems.sqlite"
    cfg = _mock_cfg(str(db))
    _patch_load_config(monkeypatch, cfg)

    price_obj = sentinel.PRICE
    forecast_obj = sentinel.FORECAST
    carbon_obj = sentinel.CARBON

    def wrap(eff, tz, cache_store=None, **kw):
        w = real_build_wiring(eff, tz, cache_store=cache_store, **kw)
        return w._replace(price_source=price_obj, solar_forecast=forecast_obj)

    monkeypatch.setattr(main_mod, "build_wiring", wrap)
    monkeypatch.setattr(main_mod, "build_carbon_source", lambda _eff, http=None: carbon_obj)

    app, _ = main_mod.build_app()
    rec = app.state.application_context.recorder
    assert rec.price_source is price_obj
    assert rec.solar_forecast is forecast_obj
    assert rec.carbon_source is carbon_obj


def test_control_state_restored_and_persisted_on_change(monkeypatch, tmp_path, main_mod):
    db = tmp_path / "ems.sqlite"
    cfg = _mock_cfg(str(db))
    _patch_load_config(monkeypatch, cfg)

    store = ControlStateStore(str(db))
    store.init()
    store.save({
        "switches_today": 7,
        "commitment_switches_today": 0,
        "last_switch_at": None,
        "counter_date": None,
        "last_requested_action": None,
        "last_confirmed_action": None,
        "original_vendor_mode": None,
        "last_command_unconfirmed": False,
        "charge_commitment": None,
    })

    app, _ = main_mod.build_app()
    ctl = app.state.application_context.controller
    assert ctl.switches_today == 7
    assert ctl.tz == ZoneInfo("Europe/Amsterdam")

    ctl.switches_today = 8
    ctl._persist()  # noqa: SLF001 — exercises on_state_change → store.save
    reloaded = store.load()
    assert isinstance(reloaded, dict)
    assert reloaded.get("switches_today") == 8


@pytest.mark.parametrize(
    ("env_token", "yaml_token", "expected"),
    [
        ("env-wins-token", "yaml-token", "env-wins-token"),
        ("", "yaml-only-token", "yaml-only-token"),
        ("", "", None),
    ],
)
def test_web_auth_token_precedence(
    monkeypatch, tmp_path, main_mod, env_token, yaml_token, expected,
):
    db = tmp_path / "ems.sqlite"
    cfg = _mock_cfg(str(db), web_auth_token=yaml_token)
    _patch_load_config(monkeypatch, cfg)
    monkeypatch.setenv("EMS_WEB_TOKEN", env_token)

    captured: dict = {}

    real_create = main_mod.create_app

    def spy_create_app(*args, **kwargs):
        captured.update(kwargs)
        return real_create(*args, **kwargs)

    monkeypatch.setattr(main_mod, "create_app", spy_create_app)
    main_mod.build_app()
    assert captured.get("web_auth_token") == expected


@pytest.mark.parametrize(
    ("env_value", "expected_grace"),
    [
        ("5", 5.0),
        ("", 120.0),
        (None, 120.0),
    ],
)
def test_startup_grace_seconds_from_env(monkeypatch, tmp_path, main_mod, env_value, expected_grace):
    db = tmp_path / "ems.sqlite"
    cfg = _mock_cfg(str(db))
    _patch_load_config(monkeypatch, cfg)
    if env_value is None:
        monkeypatch.delenv("EMS_STARTUP_GRACE_SECONDS", raising=False)
    else:
        monkeypatch.setenv("EMS_STARTUP_GRACE_SECONDS", env_value)

    app, _ = main_mod.build_app()
    grace = app.state.application_context.controller.lifecycle.startup_grace_seconds
    assert grace == expected_grace


def test_db_path_relative_anchored_absolute_unchanged(monkeypatch, tmp_path, main_mod):
    anchor = tmp_path / "repo_root"
    anchor.mkdir()
    monkeypatch.setattr(main_mod, "_REPO_ROOT", anchor)

    rel_cfg = _mock_cfg("nested/rel.sqlite")
    _patch_load_config(monkeypatch, rel_cfg)
    app_rel, _ = main_mod.build_app()
    rel_store = app_rel.state.application_context.recorder.store.db_path
    assert rel_store == str(anchor / "nested" / "rel.sqlite")

    abs_db = tmp_path / "absolute.sqlite"
    abs_cfg = _mock_cfg(str(abs_db))
    _patch_load_config(monkeypatch, abs_cfg)
    app_abs, _ = main_mod.build_app()
    abs_store = app_abs.state.application_context.recorder.store.db_path
    assert abs_store == str(abs_db)


def test_create_app_kwargs_from_build_app(monkeypatch, tmp_path, main_mod):
    db = tmp_path / "ems.sqlite"
    cfg = _mock_cfg(str(db), dry_run=True)
    _patch_load_config(monkeypatch, cfg)

    wiring_http: list[object] = []
    captured: dict = {}

    def wrap_wiring(
        eff, tz, cache_store=None, *, force_dry_run=True, config_dry_run=None, http=None, **kw,
    ):
        wiring_http.append(http)
        return real_build_wiring(
            eff, tz, cache_store=cache_store,
            force_dry_run=force_dry_run, config_dry_run=config_dry_run, http=http, **kw,
        )

    real_create = main_mod.create_app

    def spy_create_app(*args, **kwargs):
        captured.update(kwargs)
        return real_create(*args, **kwargs)

    monkeypatch.setattr(main_mod, "build_wiring", wrap_wiring)
    monkeypatch.setattr(main_mod, "create_app", spy_create_app)

    main_mod.build_app()

    assert captured["static_dir"] == main_mod._STATIC_DIR
    assert captured["config_dry_run"] is True
    assert captured["control_cycle_seconds"] == cfg.control_cycle_seconds
    assert captured["history_retention_days"] == cfg.retention_days
    assert captured["history_backup_keep"] == cfg.backup_keep
    assert captured["http_runtime"] is wiring_http[0]
