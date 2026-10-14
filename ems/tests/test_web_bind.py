"""#234: bind host + onboarding client checks for first-admin anti-seizure."""
from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient

from ems.sources.mock import MockSource
from ems.storage.auth import AuthStore
from ems.storage.settings import SettingsStore
from ems.web.api import create_app
from ems.web_bind import (
    boot_onboarding_posture,
    is_loopback_client,
    normalize_shared_token,
    onboarding_client_allowed,
    resolve_bind_host,
)


def test_resolve_bind_host_loopback_until_onboarded_or_token():
    assert resolve_bind_host(configured="0.0.0.0", users_exist=False,
                             shared_token_configured=False) == "127.0.0.1"
    assert resolve_bind_host(configured="0.0.0.0", users_exist=True,
                             shared_token_configured=False) == "0.0.0.0"
    assert resolve_bind_host(configured="0.0.0.0", users_exist=False,
                             shared_token_configured=True) == "0.0.0.0"


def test_resolve_bind_host_no_dockerenv_open_bind_exception(monkeypatch, tmp_path):
    """#234 Medium: /.dockerenv must not open the configured bind without a real token."""
    dockerenv = tmp_path / ".dockerenv"
    dockerenv.write_text("")
    # Even if a container marker exists, resolve_bind_host no longer special-cases it.
    assert resolve_bind_host(configured="0.0.0.0", users_exist=False,
                             shared_token_configured=False) == "127.0.0.1"


def test_resolve_bind_host_env_override(monkeypatch):
    monkeypatch.setenv("EMS_WEB_BIND", "192.0.2.1")
    assert resolve_bind_host(configured="0.0.0.0", users_exist=False,
                             shared_token_configured=False) == "192.0.2.1"
    monkeypatch.delenv("EMS_WEB_BIND", raising=False)


def test_normalize_shared_token_rejects_blank_and_insecure_defaults():
    assert normalize_shared_token(None) is None
    assert normalize_shared_token("") is None
    assert normalize_shared_token("  ") is None
    assert normalize_shared_token("dev-bootstrap-change-me") is None
    assert normalize_shared_token("legacy") is None
    assert normalize_shared_token("token-urlsafe-ok") == "token-urlsafe-ok"


def test_onboarding_client_allowed():
    assert onboarding_client_allowed("198.51.100.10", shared_token=None) is False
    assert onboarding_client_allowed("127.0.0.1", shared_token=None) is True
    assert onboarding_client_allowed("198.51.100.10", shared_token="secret") is False
    assert onboarding_client_allowed("198.51.100.10", shared_token="") is False
    assert onboarding_client_allowed(
        "198.51.100.10", shared_token="dev-bootstrap-change-me",
    ) is False
    assert onboarding_client_allowed("198.51.100.10", shared_token="bootstrap-secret") is True


def test_is_loopback_client():
    assert is_loopback_client("127.0.0.1")
    assert is_loopback_client("::1")
    assert not is_loopback_client("192.168.1.5")
    assert not is_loopback_client("testclient")


def _fresh_app(tmp_path, **kwargs):
    db = str(tmp_path / "ems.sqlite")
    return create_app(
        MockSource(),
        dry_run=True,
        dev_mode="mock",
        settings_store=SettingsStore(db),
        auth_store=AuthStore(db),
        **kwargs,
    )


def test_onboard_still_works_via_testclient_without_token(tmp_path):
    with TestClient(_fresh_app(tmp_path), client=("127.0.0.1", 0)) as c:
        assert c.post("/api/auth/onboard",
                      json={"username": "admin", "password": "pw12345678"}).status_code == 200


def test_onboard_remote_forbidden_without_shared_token(tmp_path):
    with TestClient(_fresh_app(tmp_path), client=("203.0.113.7", 0)) as c:
        r = c.post("/api/auth/onboard",
                   json={"username": "admin", "password": "pw12345678"})
        assert r.status_code == 403
        assert "localhost" in r.json()["detail"]


def test_onboard_remote_forbidden_with_committed_dev_token(tmp_path):
    """#234 High: the old compose default must not unlock LAN onboard."""
    with TestClient(
        _fresh_app(tmp_path, web_auth_token="dev-bootstrap-change-me"),
        client=("203.0.113.7", 0),
    ) as c:
        r = c.post(
            "/api/auth/onboard",
            json={
                "username": "admin",
                "password": "pw12345678",
                "shared_token": "dev-bootstrap-change-me",
            },
        )
        assert r.status_code == 403


def test_onboard_remote_ok_with_shared_token(tmp_path):
    with TestClient(_fresh_app(tmp_path, web_auth_token="bootstrap-secret"),
                     client=("203.0.113.7", 0)) as c:
        r = c.post(
            "/api/auth/onboard",
            json={
                "username": "admin",
                "password": "pw12345678",
                "shared_token": "bootstrap-secret",
            },
        )
        assert r.status_code == 200
        assert r.json()["user"]["role"] == "admin"


def test_boot_shared_token_from_settings(tmp_path):
    db = str(tmp_path / "ems.sqlite")

    async def seed():
        s = SettingsStore(db)
        await s.init()
        await s.set_many({"web.auth_token": "ui-secret"})

    asyncio.run(seed())
    users, token = boot_onboarding_posture(db, env_web_token=None)
    assert users is False
    assert token is True
    assert resolve_bind_host(configured="0.0.0.0", users_exist=users,
                             shared_token_configured=token) == "0.0.0.0"


def test_boot_rejects_insecure_default_env_token(tmp_path):
    db = str(tmp_path / "ems.sqlite")
    users, token = boot_onboarding_posture(
        db, env_web_token="dev-bootstrap-change-me", config_web_token=None,
    )
    assert users is False
    assert token is False
    assert resolve_bind_host(configured="0.0.0.0", users_exist=users,
                             shared_token_configured=token) == "127.0.0.1"


def test_boot_shared_token_from_config_yaml(tmp_path):
    db = str(tmp_path / "ems.sqlite")
    users, token = boot_onboarding_posture(db, env_web_token=None, config_web_token="yaml-secret")
    assert users is False
    assert token is True
