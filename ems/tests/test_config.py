from pathlib import Path

from ems.config import Config, load_config


def test_load_defaults_when_minimal(tmp_path: Path):
    p = tmp_path / "config.yaml"
    p.write_text("site:\n  timezone: Europe/Amsterdam\n")
    cfg = load_config(p)
    assert cfg == Config(timezone="Europe/Amsterdam", dev_mode="mock", dry_run=True, web_port=8080)


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
