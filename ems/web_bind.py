"""Web listen bind + onboarding client posture (#234).

Fresh installs with no admin and no shared access token must not expose onboarding on all
interfaces. Uvicorn binds loopback until onboarded or a shared token is configured; onboard
also rejects non-loopback clients when no shared token is set (defense in depth).
"""
from __future__ import annotations

import asyncio
import ipaddress
import logging
import os

from ems.storage.auth import AuthStore
from ems.storage.settings import SettingsStore

_log = logging.getLogger(__name__)

# Starlette TestClient uses host "testclient" (in-process ASGI).
_TRUSTED_ONBOARD_HOSTS = frozenset({"127.0.0.1", "::1", "testclient"})


def onboarding_client_allowed(host: str | None, *, shared_token: str | None) -> bool:
    """Whether POST /api/auth/onboard may proceed from this client IP."""
    if shared_token is not None:
        return True
    return is_loopback_client(host)


def is_loopback_client(host: str | None) -> bool:
    if not host:
        return False
    if host in _TRUSTED_ONBOARD_HOSTS:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def resolve_bind_host(
    *,
    configured: str,
    users_exist: bool,
    shared_token_configured: bool,
) -> str:
    """Host address for uvicorn to bind."""
    override = (os.environ.get("EMS_WEB_BIND") or "").strip()
    if override:
        return override
    if users_exist or shared_token_configured:
        return configured
    return "127.0.0.1"


async def _boot_onboarding_posture_async(
    db_path: str, env_web_token: str | None,
) -> tuple[bool, bool]:
    auth = AuthStore(db_path)
    await auth.init()
    users_exist = (await auth.user_count()) > 0
    if (env_web_token or "").strip():
        return users_exist, True
    settings = SettingsStore(db_path)
    await settings.init()
    stored = await settings.all()
    ui_tok = (stored.get("web.auth_token") or "").strip()
    return users_exist, bool(ui_tok)


def boot_onboarding_posture(db_path: str, env_web_token: str | None) -> tuple[bool, bool]:
    """Return (users_exist, shared_token_configured) for bind resolution at process start."""
    return asyncio.run(_boot_onboarding_posture_async(db_path, env_web_token))


def log_bind_choice(host: str, *, users_exist: bool, shared_token_configured: bool) -> None:
    if host == "127.0.0.1" and not users_exist and not shared_token_configured:
        _log.info(
            "Web UI listening on 127.0.0.1 only until the first admin is created "
            "(or set EMS_WEB_TOKEN / web.auth_token, then restart). Restart after onboarding "
            "to enable LAN access."
        )
