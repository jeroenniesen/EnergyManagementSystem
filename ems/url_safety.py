"""Outbound URL / device-host hardening (#232).

Blocks authenticated SSRF via settings that the EMS later GETs/POSTs to:

- ``notify.ntfy_url`` / ``explainer.base_url`` — http(s) only; reject credentials,
  link-local, loopback, metadata, and (by default) RFC1918 private targets.
- Device meter/battery address fields — host/IP only (no URL shaping).
- ``notify.ntfy_topic`` — no path/query/fragment characters (topic is appended to the URL).

Save-time checks cover IP literals (including glibc-accepted decimal/hex/short forms).
Hostnames are also checked at connect time via :func:`guard_outbound_url` /
:func:`prepare_outbound_request` so DNS names that resolve to loopback or metadata
(``*.nip.io``, ``localtest.me``, …) cannot be dialed.
"""
from __future__ import annotations

import ipaddress
import re
import socket
from urllib.parse import urlparse, urlunparse

# Cloud / link-local metadata endpoints — never a legitimate ntfy/AI/device target.
_BLOCKED_HOSTNAMES = frozenset({
    "localhost",
    "metadata",
    "metadata.google.internal",
    "metadata.goog",
    "instance-data",
    "instance-data.ec2.internal",
})

# Well-known cloud metadata addresses — always blocked, even with LAN opt-in.
_METADATA_V4 = frozenset({
    ipaddress.IPv4Address("169.254.169.254"),
    ipaddress.IPv4Address("100.100.100.200"),  # Alibaba
    ipaddress.IPv4Address("168.63.129.16"),  # Azure WireServer
})
_METADATA_V6 = frozenset({
    ipaddress.IPv6Address("fd00:ec2::254"),  # AWS IMDS
})

_CGNAT_V4 = ipaddress.IPv4Network("100.64.0.0/10")

_HOSTNAME_RE = re.compile(
    r"^(?=.{1,253}$)([a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)"
    r"(\.([a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?))*$"
)

# glibc-style alternate IPv4 encodings (decimal, hex, short dotted) — not accepted by
# ``ipaddress.ip_address`` but resolved by getaddrinfo / dialed by httpx.
_WEIRD_IPV4_RE = re.compile(
    r"^(?:0x[0-9a-f]+|\d+)(?:\.(?:0x[0-9a-f]+|\d+)){0,3}$",
    re.IGNORECASE,
)

# Topic becomes a path segment: ``{url}/{topic}`` — reject manipulators.
_TOPIC_BAD = frozenset("/?#\\")


def _is_site_local(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """``is_site_local`` exists on IPv6 only (raises AttributeError on IPv4 in 3.12)."""
    return bool(getattr(ip, "is_site_local", False))


def _unwrap_mapped(
    ip: ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        return ip.ipv4_mapped
    return ip


def _format_ip_for_url(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> str:
    if ip.version == 6:
        return f"[{ip}]"
    return str(ip)


def _ip_blocked(
    ip: ipaddress.IPv4Address | ipaddress.IPv6Address, *, allow_private: bool,
) -> str | None:
    ip = _unwrap_mapped(ip)
    if ip.is_unspecified or ip.is_multicast or ip.is_reserved:
        return "must not target reserved or multicast addresses"
    if ip.is_loopback:
        return "must not target loopback"
    if ip.is_link_local:
        return "must not target link-local addresses"
    if ip in _METADATA_V4 or ip in _METADATA_V6:
        return "must not target cloud metadata"
    # CGNAT overlaps Alibaba metadata; never a home ntfy/AI target.
    if isinstance(ip, ipaddress.IPv4Address) and ip in _CGNAT_V4:
        return "must not target cloud metadata"
    # Deprecated IPv6 site-local (fec0::/10) — not ULA; block even with LAN opt-in.
    if _is_site_local(ip) and not ip.is_private:
        return "must not target deprecated site-local addresses"
    if not allow_private and (ip.is_private or _is_site_local(ip)):
        return (
            "must not target a private LAN address "
            "(enable “Allow LAN ntfy server” for a self-hosted ntfy on your network)"
        )
    return None


def _parse_ip_literal(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """Parse a host as an IP, including glibc alternate IPv4 encodings."""
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        pass
    if not _WEIRD_IPV4_RE.fullmatch(host):
        return None
    # Decimal / hex / short forms: ask the resolver with NUMERICHOST when possible,
    # else plain getaddrinfo (same path httpx/glibc use).
    try:
        infos = socket.getaddrinfo(
            host, None, type=socket.SOCK_STREAM, flags=getattr(socket, "AI_NUMERICHOST", 0),
        )
    except socket.gaierror:
        try:
            infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
        except socket.gaierror:
            return None
    if not infos:
        return None
    try:
        return ipaddress.ip_address(infos[0][4][0])
    except ValueError:
        return None


def host_blocked(hostname: str, *, allow_private: bool) -> str | None:
    """Return a homeowner-facing error if ``hostname`` is unsafe, else ``None``.

    Non-IP hostnames return ``None`` here (lexical metadata names already checked);
    call :func:`guard_outbound_url` immediately before dialing to check resolved addresses.
    """
    host = (hostname or "").strip().lower().rstrip(".")
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    if not host:
        return "must include a host"
    if "%" in host:  # zone id
        return "must not target link-local addresses"
    if host in _BLOCKED_HOSTNAMES:
        return "must not target this host"
    # Cloud metadata DNS suffixes stay blocked even with LAN opt-in.
    if host.endswith((".localhost",)) and not allow_private:
        return (
            "must not target a local/private hostname "
            "(enable “Allow LAN ntfy server” for a self-hosted ntfy on your network)"
        )
    if host.endswith((".local", ".lan")) and not allow_private:
        return (
            "must not target a local/private hostname "
            "(enable “Allow LAN ntfy server” for a self-hosted ntfy on your network)"
        )
    # ``.internal`` is commonly cloud metadata (ec2) — always reject for outbound URLs.
    if host.endswith(".internal"):
        return "must not target this host"
    ip = _parse_ip_literal(host)
    if ip is None:
        return None  # hostname — connect-time guard required
    return _ip_blocked(ip, allow_private=allow_private)


def resolve_host_ips(hostname: str) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    """Resolve ``hostname`` to every A/AAAA address (always includes IP literals)."""
    host = (hostname or "").strip().lower().rstrip(".")
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    if not host:
        raise ValueError("must include a host")
    literal = _parse_ip_literal(host)
    if literal is not None:
        return [literal]
    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ValueError("could not resolve host") from exc
    ips: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
    seen: set[str] = set()
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            continue
        key = str(ip)
        if key not in seen:
            seen.add(key)
            ips.append(ip)
    if not ips:
        raise ValueError("could not resolve host")
    return ips


def guard_resolved_ips(
    ips: list[ipaddress.IPv4Address | ipaddress.IPv6Address], *, allow_private: bool,
) -> None:
    """Raise ``ValueError`` if any resolved address is unsafe."""
    for ip in ips:
        reason = _ip_blocked(ip, allow_private=allow_private)
        if reason:
            raise ValueError(reason)


def guard_outbound_url(url: str, *, allow_private: bool = False) -> None:
    """Resolve the URL host and reject unsafe addresses (connect-time SSRF guard)."""
    try:
        parsed = urlparse(url)
    except ValueError as exc:
        raise ValueError("must be an http(s) URL with a host") from exc
    host = parsed.hostname
    if not host:
        raise ValueError("must include a host")
    lexical = host_blocked(host, allow_private=allow_private)
    if lexical:
        raise ValueError(lexical)
    guard_resolved_ips(resolve_host_ips(host), allow_private=allow_private)


def prepare_outbound_request(
    url: str, *, allow_private: bool = False,
) -> tuple[str, dict[str, str]]:
    """Resolve, validate, and (for ``http``) pin the URL to a safe IP.

    Returns ``(request_url, extra_headers)``. For ``http``, the URL host is replaced with a
    checked IP literal and ``Host`` carries the original hostname (closes DNS rebinding for
    cleartext LAN targets). For ``https``, the original URL is kept so TLS certificate
    verification / SNI still work; addresses are still validated immediately before dial.
    """
    try:
        parsed = urlparse(url)
    except ValueError as exc:
        raise ValueError("must be an http(s) URL with a host") from exc
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("must be an http(s) URL with a host")
    host = parsed.hostname
    lexical = host_blocked(host, allow_private=allow_private)
    if lexical:
        raise ValueError(lexical)
    ips = resolve_host_ips(host)
    guard_resolved_ips(ips, allow_private=allow_private)
    if parsed.scheme != "http":
        return url, {}
    # Prefer IPv4 for cleartext pin (device/ntfy LAN); fall back to IPv6.
    chosen = next((ip for ip in ips if ip.version == 4), ips[0])
    host_for_header = host
    if parsed.port:
        netloc = f"{_format_ip_for_url(chosen)}:{parsed.port}"
        # Host header with non-default port.
        if not (parsed.port == 80):
            host_for_header = f"{host}:{parsed.port}"
    else:
        netloc = _format_ip_for_url(chosen)
    pinned = urlunparse((
        parsed.scheme, netloc, parsed.path, parsed.params, parsed.query, parsed.fragment,
    ))
    return pinned, {"Host": host_for_header}


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
    # Canonicalise weird IPv4 literals so stored settings are not dial-obfuscated forms.
    lit = _parse_ip_literal(parsed.hostname)
    if lit is not None:
        host_out = _format_ip_for_url(lit)
        port = f":{parsed.port}" if parsed.port else ""
        return f"{parsed.scheme}://{host_out}{port}{path}"
    return f"{parsed.scheme}://{parsed.netloc}{path}"


def normalize_device_host(raw: str) -> str:
    """IPv4 / IPv6 / simple hostname for meter and battery address fields.

    Rejects URL schemes, paths, credentials, and link-local/metadata/loopback literals.
    IPv6 is returned in bracketed form so ``http://{host}/...`` builders stay valid.
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
        left, right = host.split(":", 1)
        if right.isdigit():
            raise ValueError("must be an IP or hostname without a port")
    ip = _parse_ip_literal(host)
    if ip is None:
        if not _HOSTNAME_RE.match(host):
            raise ValueError("must be a valid IP address or hostname") from None
        if host.lower() in _BLOCKED_HOSTNAMES or host.lower().endswith(".internal"):
            raise ValueError("must not target this host") from None
        return host
    # LAN devices are expected (RFC1918), but still reject loopback / link-local / metadata.
    reason = _ip_blocked(ip, allow_private=True)
    if reason and (
        "link-local" in reason or "loopback" in reason or "metadata" in reason
        or "reserved" in reason or "multicast" in reason or "site-local" in reason
    ):
        raise ValueError(reason)
    return _format_ip_for_url(ip)


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


_RFC1918_V4 = (
    ipaddress.IPv4Network("10.0.0.0/8"),
    ipaddress.IPv4Network("172.16.0.0/12"),
    ipaddress.IPv4Network("192.168.0.0/16"),
)

_PRIVATE_IPV4_MSG = (
    "Vul een privé IPv4-adres in (10.x.x.x, 172.16–172.31.x.x of 192.168.x.x)."
)


def _is_rfc1918_v4(ip: ipaddress.IPv4Address) -> bool:
    return any(ip in net for net in _RFC1918_V4)


def normalize_private_ipv4(raw: str) -> str:
    """RFC1918 IPv4 only — for onboarding setup saves (#291). No hostnames, no public IPs."""
    text = str(raw).strip()
    if not text:
        raise ValueError(_PRIVATE_IPV4_MSG)
    if "://" in text or any(ch in text for ch in "/?#@\\"):
        raise ValueError(_PRIVATE_IPV4_MSG)
    host = text[1:-1] if text.startswith("[") and text.endswith("]") else text
    if host.count(":") == 1 and not host.startswith("["):
        left, right = host.split(":", 1)
        if right.isdigit():
            raise ValueError(_PRIVATE_IPV4_MSG)
    ip = _parse_ip_literal(host)
    if ip is None or not isinstance(ip, ipaddress.IPv4Address):
        raise ValueError(_PRIVATE_IPV4_MSG)
    ip = _unwrap_mapped(ip)
    if ip.is_loopback or ip.is_link_local or ip in _METADATA_V4:
        raise ValueError(_PRIVATE_IPV4_MSG)
    if isinstance(ip, ipaddress.IPv4Address) and ip in _CGNAT_V4:
        raise ValueError(_PRIVATE_IPV4_MSG)
    if not _is_rfc1918_v4(ip):
        raise ValueError(_PRIVATE_IPV4_MSG)
    return str(ip)


def hostname_is_private_ip(hostname: str) -> bool:
    """True when ``hostname`` is an IP literal in a private/site-local range."""
    host = (hostname or "").strip().lower()
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    ip = _parse_ip_literal(host)
    if ip is None:
        return False
    ip = _unwrap_mapped(ip)
    return bool(ip.is_private or _is_site_local(ip))
