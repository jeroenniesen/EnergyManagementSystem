"""Home Assistant base URL validation (#266 / #263).

Kept separate from ``ems.settings`` so consumer adapters can normalize before GETs
without import cycles (``settings`` imports ``consumers.selection``).
"""
from __future__ import annotations

from urllib.parse import urlparse


def normalize_ha_url(raw: str) -> str:
    """Canonical Home Assistant base URL for settings + wizard.

    Requires http/https + host; rejects credentials, path, query, fragment.
    Strips trailing slash. Raises ``ValueError`` with a homeowner-facing message.
    """
    try:
        parsed = urlparse(str(raw).strip())
    except ValueError:
        raise ValueError(
            "must be an http(s) URL with a host only "
            "(no path, query, fragment, or credentials)"
        ) from None
    path = (parsed.path or "").rstrip("/")
    if (
        parsed.scheme not in ("http", "https")
        or not parsed.netloc
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or path not in ("",)
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(
            "must be an http(s) URL with a host only "
            "(no path, query, fragment, or credentials)"
        )
    return f"{parsed.scheme}://{parsed.netloc}"
