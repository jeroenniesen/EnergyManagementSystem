"""First-admin bind/onboard safety (#234).

Fresh installs historically bound `0.0.0.0` with an auth-exempt
`POST /api/auth/onboard`, so any LAN client could seize the first admin when no
`EMS_WEB_TOKEN` / `web.auth_token` was configured.

Rules:
- Open bind (`0.0.0.0` / non-loopback) is only the default when a shared/bootstrap
  token is already present (install generates one).
- Without a shared token, `main()` defaults to loopback listen, and onboard
  rejects non-loopback clients (localhost create-admin still works).
"""
from __future__ import annotations

from ipaddress import ip_address

# Explicit bind override for operators who intentionally open the port.
ENV_WEB_BIND = "EMS_WEB_BIND"


def is_loopback_host(host: str | None) -> bool:
    """True for IPv4/IPv6 loopback (and the literal name ``localhost``)."""
    if not host:
        return False
    h = host.strip().lower()
    if h.startswith("[") and h.endswith("]"):
        h = h[1:-1]
    if h == "localhost":
        return True
    try:
        return bool(ip_address(h).is_loopback)
    except ValueError:
        return False


def resolve_listen_host(
    *,
    explicit: str | None,
    has_shared_token: bool,
    default_open: str = "0.0.0.0",
    default_local: str = "127.0.0.1",
) -> str:
    """Pick uvicorn listen host for a fresh process.

    - ``EMS_WEB_BIND`` / ``explicit`` wins when set (operator override).
    - Shared/bootstrap token present → open bind (LAN UI + iOS).
    - Otherwise → loopback until a token is configured or an admin exists
      (operator then restarts with an open bind / token).
    """
    if explicit is not None and str(explicit).strip():
        return str(explicit).strip()
    return default_open if has_shared_token else default_local
