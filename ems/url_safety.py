"""Outbound URL / device-host hardening (#232).

Blocks authenticated SSRF via settings that the EMS later GETs/POSTs to:

- ``notify.ntfy_url`` / ``explainer.base_url`` — http(s) only; reject credentials,
  link-local, loopback, metadata, and (by default) RFC1918 private targets.
- Device meter/battery address fields — host/IP only (no URL shaping).
- ``notify.ntfy_topic`` — no path/query/fragment characters (topic is appended to the URL).

DNS is **not** resolved at validation time (no TOCTOU, no blocking lookups). Hostname
rules are lexical; IP literals are checked with :mod:`ipaddress`.
"""
from __future__ import annotations

import ipaddress
import re
from urllib.parse import urlparse

# Cloud / link-local metadata endpoints — never a legitimate ntfy/AI target.
_BLOCKED_HOSTNAMES = frozenset({
    "localhost",
    "metadata",
    "metadata.google.internal",
    "metadata.goog",
})

_HOSTNAME_RE = re.compile(
    r"^(?=.{1,253}$)([a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)"
    r"(\.([a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?))*$"
)

# Topic becomes a path segment: ``{url}/{topic}`` — reject manipulators.
_TOPIC_BAD = frozenset("/?#\\")


def _ip_blocked(
    ip: ipaddress.IPv4Address | ipaddress.IPv6Address, *, allow_private: bool,
) -> str | None:
    if ip.is_unspecified or ip.is_multicast or ip.is_reserved:
        return "must not target reserved or multicast addresses"
    if ip.is_loopback:
        return "must not target loopback"
    if ip.is_link_local:
        return "must not target link-local addresses"
    # Explicit cloud metadata well-known addresses.
    if ip.version == 4 and str(ip) == "169.254.169.254":
        return "must not target cloud metadata"
    if not allow_private and (ip.is_private or ip.is_site_local):
        return (
            "must not target a private LAN address "
            "(enable “Allow LAN ntfy server” for a self-hosted ntfy on your network)"
        )
    return None


def host_blocked(hostname: str, *, allow_private: bool) -> str | None:
    """Return a homeowner-facing error if ``hostname`` is unsafe, else ``None``."""
    host = (hostname or "").strip().lower().rstrip(".")
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    if not host:
        return "must include a host"
    if host in _BLOCKED_HOSTNAMES:
        return "must not target this host"
    if host.endswith((".localhost", ".local", ".internal", ".lan")) and not allow_private:
        return (
            "must not target a local/private hostname "
            "(enable “Allow LAN ntfy server” for a self-hosted ntfy on your network)"
        )
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return None  # non-IP hostname — lexical blocks above already applied
    return _ip_blocked(ip, allow_private=allow_private)


def normalize_outbound_http_url(
    raw: str,
    *,
    allow_private: bool = False,
    allow_path: bool = False,
) -> str:
    """Canonical http(s) URL for EMS-initiated outbound calls.

    - Always rejects credentials, query, fragment.
    - ``allow_path=False`` (ntfy base): host only, trailing slash stripped.
    - ``allow_path=True`` (AI endpoint): path kept (e.g. ``/v1``), trailing slash stripped
      except when the path is ``/``.
    Raises ``ValueError`` with a homeowner-facing message.
    """
    text = str(raw).strip()
    if not text:
        return ""
    try:
        parsed = urlparse(text)
    except ValueError as exc:
        raise ValueError("must be an http(s) URL with a host") from exc
    if parsed.scheme not in ("http", "https") or not parsed.netloc or not parsed.hostname:
        raise ValueError("must be an http(s) URL with a host")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("must not include credentials in the URL")
    if parsed.query or parsed.fragment:
        raise ValueError("must not include a query or fragment")
    path = parsed.path or ""
    if not allow_path:
        if path not in ("", "/"):
            raise ValueError("must be an http(s) URL with a host only (no path)")
        path = ""
    else:
        if path in ("", "/"):
            path = ""
        else:
            path = path.rstrip("/")
    reason = host_blocked(parsed.hostname, allow_private=allow_private)
    if reason:
        raise ValueError(reason)
    return f"{parsed.scheme}://{parsed.netloc}{path}"


def normalize_device_host(raw: str) -> str:
    """IPv4 / IPv6 / simple hostname for meter and battery address fields.

    Rejects URL schemes, paths, credentials, and link-local/metadata literals.
    Empty string stays empty. Raises ``ValueError`` with a homeowner-facing message.
    """
    text = str(raw).strip()
    if not text:
        return ""
    if "://" in text or any(ch in text for ch in "/?#@\\"):
        raise ValueError("must be an IP address or hostname — not a full URL")
    # Bracketed IPv6.
    host = text[1:-1] if text.startswith("[") and text.endswith("]") else text
    # Optional :port for IPv4/hostname — reject (device APIs use fixed ports).
    if host.count(":") == 1 and not host.startswith("["):
        # Could be IPv6 without brackets (many colons) or host:port (one colon).
        left, right = host.split(":", 1)
        if right.isdigit():
            raise ValueError("must be an IP or hostname without a port")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        if not _HOSTNAME_RE.match(host):
            raise ValueError("must be a valid IP address or hostname") from None
        if host.lower() in _BLOCKED_HOSTNAMES:
            raise ValueError("must not target this host") from None
        return host
    reason = _ip_blocked(ip, allow_private=True)  # LAN devices are expected
    # Device fields: still reject link-local / loopback / metadata (not useful meters).
    if reason and ("link-local" in reason or "loopback" in reason or "metadata" in reason
                   or "reserved" in reason or "multicast" in reason):
        raise ValueError(reason)
    return str(ip)


def normalize_ntfy_topic(raw: str) -> str:
    """Topic name only — no path/query/fragment characters (#232)."""
    text = str(raw).strip()
    if not text:
        return ""
    if any(ch in text for ch in _TOPIC_BAD) or ".." in text:
        raise ValueError("topic must not contain /, ?, #, or \\")
    if len(text) > 128:
        raise ValueError("topic is too long (max 128 characters)")
    return text


def hostname_is_private_ip(hostname: str) -> bool:
    """True when ``hostname`` is an IP literal in a private/site-local range."""
    host = (hostname or "").strip().lower()
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return bool(ip.is_private or ip.is_site_local)
