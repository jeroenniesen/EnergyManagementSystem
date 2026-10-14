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
from pathlib import Path

from ems.storage.auth import AuthStore
from ems.storage.settings import SettingsStore

_log = logging.getLogger(__name__)

_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1"})


def onboarding_client_allowed(host: str | None, *, shared_token: str | None) -> bool:
    """Whether POST /api/auth/onboard may proceed from this client IP."""
    if shared_token is not None:
        return True
    return is_loopback_client(host)


def is_loopback_client(host: str | None) -> bool:
    if not host:
        return False
    if host in _LOOPBACK_HOSTS:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _in_container() -> bool:
    return Path("/.dockerenv").is_file()


def resolve_bind_host(
    *,
    configured: str,
    users_exist: bool,
    shared_token_configured: bool,
) -> str:
    """Host address for uvicorn to bind."""
    override = (os.environ.get("EMS_WEB_BIND") or "").strip()
    if override:
        if not users_exist and not shared_token_configured:
            _log.warning(
                "EMS_WEB_BIND=%s while no admin and no shared token are configured — "
                "onboarding may be reachable from the LAN (POST /api/auth/onboard still "
                "requires loopback unless a shared token is set).",
                override,
            )
        return override
    if users_exist or shared_token_configured:
        return configured
    if _in_container():
        return configured
    return "127.0.0.1"


async def _boot_onboarding_posture_async(
    db_path: str,
    env_web_token: str | None,
    config_web_token: str | None,
) -> tuple[bool, bool]:
    auth = AuthStore(db_path)
    await auth.init()
    users_exist = (await auth.user_count()) > 0
    if (env_web_token or "").strip() or (config_web_token or "").strip():
        return users_exist, True
    settings = SettingsStore(db_path)
    await settings.init()
    stored = await settings.all()
    ui_tok = (stored.get("web.auth_token") or "").strip()
    return users_exist, bool(ui_tok)


def boot_onboarding_posture(
    db_path: str,
    env_web_token: str | None,
    config_web_token: str | None = None,
) -> tuple[bool, bool]:
    """Return (users_exist, shared_token_configured) for bind resolution at process start."""
    return asyncio.run(
        _boot_onboarding_posture_async(db_path, env_web_token, config_web_token),
    )


def log_bind_choice(
    host: str,
    *,
    users_exist: bool,
    shared_token_configured: bool,
    configured: str,
) -> None:
    if host == "127.0.0.1" and not users_exist and not shared_token_configured:
        _log.warning(
            "Web UI listening on 127.0.0.1 only until the first admin is created "
            "(or set EMS_WEB_TOKEN / web.auth_token in config or settings, then restart). "
            "Restart after onboarding to enable LAN access (configured bind=%s).",
            configured,
        )
        return
    reason = "onboarded_or_token"
    if (os.environ.get("EMS_WEB_BIND") or "").strip():
        reason = "EMS_WEB_BIND"
    elif _in_container() and not users_exist and not shared_token_configured:
        reason = "container_default_bind"
    _log.info(
        "Web UI listening on %s (%s; users_exist=%s shared_token=%s configured_bind=%s)",
        host,
        reason,
        users_exist,
        shared_token_configured,
        configured,
    )
