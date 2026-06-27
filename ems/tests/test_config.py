from pathlib import Path

from ems.config import Config, load_config


def _no_source_env(monkeypatch):
    # Isolate from a leaked EMS_SOURCES/EMS_PRICES in the dev/CI shell (load_config reads them).
    monkeypatch.delenv("EMS_SOURCES", raising=False)
    monkeypatch.delenv("EMS_PRICES", raising=False)


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
    )


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
