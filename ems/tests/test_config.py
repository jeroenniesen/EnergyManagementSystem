from pathlib import Path

from ems.config import Config, load_config


def _no_source_env(monkeypatch):
    # Isolate from a leaked EMS_SOURCES/EMS_PRICES/EMS_CYCLE_SECONDS in the dev/CI shell.
    monkeypatch.delenv("EMS_SOURCES", raising=False)
    monkeypatch.delenv("EMS_PRICES", raising=False)
    monkeypatch.delenv("EMS_CYCLE_SECONDS", raising=False)


def test_load_defaults_when_minimal(tmp_path: Path, monkeypatch):
    _no_source_env(monkeypatch)
    p = tmp_path / "config.yaml"
    p.write_text("site:\n  timezone: Europe/Amsterdam\n")
    cfg = load_config(p)
    assert cfg == Config(
        timezone="Europe/Amsterdam",
        dev_mode="mock",
        dry_run=True,
        web_port=8080,
        db_path="ems/data/ems.sqlite",
        cycle_seconds=300.0,
        retention_days=90,
    )


def test_cycle_seconds_env_override_and_retention(tmp_path: Path, monkeypatch):
    # Production cadence is 300 s by default; EMS_CYCLE_SECONDS lets a dev sample faster without
    # editing the file. retention_days comes from the history section (default 90).
    _no_source_env(monkeypatch)
    p = tmp_path / "config.yaml"
    p.write_text("history:\n  retention_days: 30\n")
    assert load_config(p).cycle_seconds == 300.0
    assert load_config(p).retention_days == 30
    # The control loop is decoupled from the recorder and reacts faster by default (car guard etc.).
    assert load_config(p).control_cycle_seconds == 60.0
    monkeypatch.setenv("EMS_CYCLE_SECONDS", "5")
    assert load_config(p).cycle_seconds == 5.0  # env override wins (dev fast-sampling)


def test_backup_keep_default_and_clamp(tmp_path: Path, monkeypatch):
    # SPEC §11 durability: backup_keep defaults to 7 and is sanity-bounded to [0, 60] on load.
    _no_source_env(monkeypatch)
    p = tmp_path / "config.yaml"
    p.write_text("site:\n  timezone: Europe/Amsterdam\n")
    assert load_config(p).backup_keep == 7  # default when unset

    p.write_text("history:\n  backup_keep: 0\n")
    assert load_config(p).backup_keep == 0  # 0 = disabled, allowed

    p.write_text("history:\n  backup_keep: 999\n")
    assert load_config(p).backup_keep == 60  # clamped to the ceiling

    p.write_text("history:\n  backup_keep: -5\n")
    assert load_config(p).backup_keep == 0  # clamped to the floor


def test_dev_mock_forces_dry_run(tmp_path: Path):
    p = tmp_path / "config.yaml"
    p.write_text("dev:\n  mode: mock\ncontrol:\n  dry_run: false\n")
    cfg = load_config(p)
    assert cfg.dev_mode == "mock"
    assert cfg.dry_run is True  # mock/replay force dry_run regardless of config (SPEC §11.6)


def test_live_mode_respects_dry_run_flag(tmp_path: Path):
    p = tmp_path / "config.yaml"
    p.write_text("dev:\n  mode: live\ncontrol:\n  dry_run: false\nweb:\n  port: 9000\n")
    cfg = load_config(p)
    assert cfg.dev_mode == "live"
    assert cfg.dry_run is False
    assert cfg.web_port == 9000


def test_sources_and_devices_parsed(tmp_path: Path):
    p = tmp_path / "config.yaml"
    p.write_text(
        "sources:\n  mode: live\nprices:\n  provider: tibber\n"
        "devices:\n  p1_ip: 10.0.0.1\n  solar_ip: 10.0.0.2\n  car_ip: 10.0.0.3\n"
        "  indevolt_ip: 10.0.0.4\n  indevolt_port: 9090\n"
    )
    cfg = load_config(p)
    assert cfg.sources_mode == "live"
    assert cfg.prices_provider == "tibber"
    assert (cfg.p1_ip, cfg.solar_ip, cfg.car_ip) == ("10.0.0.1", "10.0.0.2", "10.0.0.3")
    assert cfg.indevolt_ip == "10.0.0.4" and cfg.indevolt_port == 9090


def test_sources_default_to_mock(tmp_path: Path, monkeypatch):
    _no_source_env(monkeypatch)
    p = tmp_path / "config.yaml"
    p.write_text("site:\n  timezone: Europe/Amsterdam\n")
    cfg = load_config(p)
    assert cfg.sources_mode == "mock" and cfg.prices_provider == "mock"


def test_access_token_idle_days_default_is_90(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("site:\n  timezone: Europe/Amsterdam\n")
    assert load_config(p).access_token_idle_days == 90


def test_access_token_idle_days_parsed(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("auth:\n  access_token_idle_days: 45\n")
    assert load_config(p).access_token_idle_days == 45


def test_access_token_idle_days_negative_clamps_to_zero(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("auth:\n  access_token_idle_days: -5\n")
    assert load_config(p).access_token_idle_days == 0
