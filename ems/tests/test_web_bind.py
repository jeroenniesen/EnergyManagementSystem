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


def test_onboarding_client_allowed():
    assert onboarding_client_allowed("198.51.100.10", shared_token=None) is False
    assert onboarding_client_allowed("127.0.0.1", shared_token=None) is True
    assert onboarding_client_allowed("198.51.100.10", shared_token="secret") is True


def test_is_loopback_client():
    assert is_loopback_client("127.0.0.1")
    assert is_loopback_client("::1")
    assert not is_loopback_client("192.168.1.5")
    assert is_loopback_client("testclient")


def test_onboard_still_works_via_testclient_without_token(tmp_path):
    db = str(tmp_path / "ems.sqlite")
    with TestClient(create_app(MockSource(), dry_run=True, dev_mode="mock",
                              settings_store=SettingsStore(db), auth_store=AuthStore(db))) as c:
        assert c.post("/api/auth/onboard",
                      json={"username": "admin", "password": "pw12345678"}).status_code == 200


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
