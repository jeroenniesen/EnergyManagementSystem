"""Shared sync ``httpx.Client`` + named timeout profiles (Phase 1).

Almost all EMS outbound I/O is sync (often via ``asyncio.to_thread``). Call sites used to
fire one-shot ``httpx.get``/``httpx.post`` — a new TCP/TLS handshake every time. This module
owns one pooled client for the process, with connect/read budgets selected by named profile.

**No transport-level retries.** Battery SetData keeps application retries in
``IndevoltBatteryDriver`` (``write_attempts``). Sources stay fail-safe on timeout.

Lifecycle: construct an ``HttpRuntime`` in ``build_app``, optionally ``set_default_runtime``
so module defaults pick it up, inject factories where wiring is explicit, and ``close()`` it
from the FastAPI lifespan teardown.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

import httpx

# Named profiles — source of truth for production connect/read budgets.
TIMEOUTS: Mapping[str, httpx.Timeout] = {
    "lan_read": httpx.Timeout(connect=1.5, read=4.0, write=4.0, pool=4.0),
    "lan_write": httpx.Timeout(connect=2.0, read=8.0, write=8.0, pool=8.0),
    "cloud": httpx.Timeout(connect=3.0, read=12.0, write=12.0, pool=12.0),
    # Covers ntfy (~5 s), Open-Meteo (~2.5 s), ElectricityMaps (~10 s).
    "best_effort": httpx.Timeout(connect=2.0, read=10.0, write=10.0, pool=10.0),
}

# One-shot fallbacks when no runtime is installed (scripts / hermetic unit defaults).
_FALLBACK_SECONDS: Mapping[str, float] = {
    "lan_read": 4.0,
    "lan_write": 8.0,
    "cloud": 12.0,
    "best_effort": 10.0,
}


class HttpRuntime:
    """Owns one sync ``httpx.Client`` (thread-safe for concurrent requests)."""

    def __init__(self, client: httpx.Client | None = None) -> None:
        # Default Client has no Transport(retries=…): writes must not be retried here.
        self.client = client or httpx.Client()
        self._closed = False

    def timeout(self, profile: str) -> httpx.Timeout:
        try:
            return TIMEOUTS[profile]
        except KeyError as exc:
            known = ", ".join(sorted(TIMEOUTS))
            raise ValueError(
                f"unknown http timeout profile {profile!r}; expected one of: {known}"
            ) from exc

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.client.close()

    @property
    def closed(self) -> bool:
        return self._closed

    def __enter__(self) -> HttpRuntime:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


_default_runtime: HttpRuntime | None = None


def set_default_runtime(runtime: HttpRuntime | None) -> None:
    """Install (or clear) the process-default runtime used by module-level http helpers."""
    global _default_runtime
    _default_runtime = runtime


def get_default_runtime() -> HttpRuntime | None:
    return _default_runtime


def resolve_timeout(
    profile: str,
    *,
    timeout: float | httpx.Timeout | None = None,
    runtime: HttpRuntime | None = None,
) -> float | httpx.Timeout:
    """Pick a request timeout: explicit override → runtime profile → float fallback."""
    if timeout is not None:
        return timeout
    rt = runtime if runtime is not None else _default_runtime
    if rt is not None:
        return rt.timeout(profile)
    try:
        return _FALLBACK_SECONDS[profile]
    except KeyError as exc:
        raise ValueError(f"unknown http timeout profile {profile!r}") from exc


def request(
    method: str,
    url: str,
    *,
    profile: str,
    client: httpx.Client | None = None,
    timeout: float | httpx.Timeout | None = None,
    runtime: HttpRuntime | None = None,
    **kwargs: Any,
) -> httpx.Response:
    """GET/POST via the shared client when available; otherwise one-shot ``httpx.request``."""
    rt = runtime if runtime is not None else _default_runtime
    c = client if client is not None else (rt.client if rt is not None else None)
    t = resolve_timeout(profile, timeout=timeout, runtime=rt)
    if c is not None:
        return c.request(method, url, timeout=t, **kwargs)
    return httpx.request(method, url, timeout=t, **kwargs)


def make_json_get(
    runtime: HttpRuntime,
    profile: str = "lan_read",
    *,
    timeout: float | httpx.Timeout | None = None,
) -> Callable[[str], dict]:
    """``(url) -> dict`` for HomeWizard / Forecast.Solar-style GETs."""
    t = resolve_timeout(profile, timeout=timeout, runtime=runtime)

    def get(url: str) -> dict:
        r = runtime.client.get(url, timeout=t)
        r.raise_for_status()
        return r.json()

    return get


def make_json_get_headers(
    runtime: HttpRuntime,
    profile: str = "cloud",
    *,
    timeout: float | httpx.Timeout | None = None,
) -> Callable[[str, dict[str, str]], dict]:
    """``(url, headers) -> dict`` for Solcast / ElectricityMaps-style GETs."""
    t = resolve_timeout(profile, timeout=timeout, runtime=runtime)

    def get(url: str, headers: dict[str, str]) -> dict:
        r = runtime.client.get(url, headers=headers, timeout=t)
        r.raise_for_status()
        return r.json()

    return get


def make_json_post(
    runtime: HttpRuntime,
    profile: str = "cloud",
    *,
    timeout: float | httpx.Timeout | None = None,
) -> Callable[..., httpx.Response]:
    """Low-level JSON/body POST bound to the shared client + profile timeout."""
    t = resolve_timeout(profile, timeout=timeout, runtime=runtime)

    def post(url: str, **kwargs: Any) -> httpx.Response:
        return runtime.client.post(url, timeout=t, **kwargs)

    return post


def make_tibber_post(
    runtime: HttpRuntime,
    profile: str = "cloud",
    *,
    timeout: float | httpx.Timeout | None = None,
) -> Callable[[str, str, dict], dict]:
    """``(url, token, body) -> data`` matching ``TibberPriceSource``'s ``GraphQLPost``."""
    t = resolve_timeout(profile, timeout=timeout, runtime=runtime)

    def post(url: str, token: str, body: dict) -> dict:
        r = runtime.client.post(
            url, json=body, headers={"Authorization": f"Bearer {token}"}, timeout=t,
        )
        r.raise_for_status()
        payload = r.json()
        if payload.get("errors"):
            raise RuntimeError(f"Tibber GraphQL error: {payload['errors']}")
        return payload.get("data") or {}

    return post


def make_bytes_post(
    runtime: HttpRuntime,
    profile: str = "best_effort",
    *,
    timeout: float | httpx.Timeout | None = None,
) -> Callable[[str, bytes, dict], None]:
    """``(url, body, headers) -> None`` matching ``Notifier``'s ``PostFn``."""
    t = resolve_timeout(profile, timeout=timeout, runtime=runtime)

    def post(url: str, data: bytes, headers: dict) -> None:
        r = runtime.client.post(url, content=data, headers=headers, timeout=t)
        r.raise_for_status()

    return post


def make_cloud_cover_get(
    runtime: HttpRuntime,
    profile: str = "best_effort",
    *,
    timeout: float | httpx.Timeout | None = None,
) -> Callable[[float, float, float], dict]:
    """``(lat, lon, timeout) -> dict`` matching ``weather.CloudGet`` (ignores per-call timeout)."""
    t = resolve_timeout(profile, timeout=timeout, runtime=runtime)
    url = "https://api.open-meteo.com/v1/forecast"

    def get(lat: float, lon: float, _timeout: float) -> dict:
        r = runtime.client.get(
            url, params={"latitude": lat, "longitude": lon, "current": "cloud_cover"}, timeout=t,
        )
        r.raise_for_status()
        return r.json()

    return get


def make_indevolt_getdata_post(
    runtime: HttpRuntime,
    ip: str,
    port: int = 8080,
    *,
    profile: str = "lan_read",
    timeout: float | httpx.Timeout | None = None,
) -> Callable[[Any], dict]:
    """``(keys) -> dict`` matching ``IndevoltReadClient``'s ``GetDataPost``."""
    import json

    url = f"http://{ip}:{port}/rpc/Indevolt.GetData"
    t = resolve_timeout(profile, timeout=timeout, runtime=runtime)

    def post(keys: Any) -> dict:
        config = json.dumps({"t": list(keys)}).replace(" ", "")
        r = runtime.client.post(url, params={"config": config}, timeout=t)
        r.raise_for_status()
        return r.json()

    return post
