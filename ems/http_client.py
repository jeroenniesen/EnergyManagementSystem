"""Shared sync ``httpx.Client`` + named timeout profiles (Phase 1) + read retries (#175).

Almost all EMS outbound I/O is sync (often via ``asyncio.to_thread``). Call sites used to
fire one-shot ``httpx.get``/``httpx.post`` — a new TCP/TLS handshake every time. This module
owns one pooled client for the process, with connect/read budgets selected by named profile.

**Read retries (tenacity):** profiles ``lan_read`` / ``cloud`` / ``best_effort`` retry the
HTTP transport only on ``httpx.TimeoutException`` / ``httpx.ConnectError``. Business/source
methods still run once. After exhaustion the exception is re-raised (fail-safe / stale).

**No retries on ``lan_write``.** Battery SetData keeps application retries in
``IndevoltBatteryDriver`` (``write_attempts``). Never nest tenacity with that path.

Lifecycle: construct an ``HttpRuntime`` in ``build_app``, optionally ``set_default_runtime``
so module defaults pick it up, inject factories where wiring is explicit, and ``close()`` it
from the FastAPI lifespan teardown.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

import httpx
from tenacity import (
    Retrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
    wait_none,
)

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

# Profiles that may tenacity-retry the HTTP attempt (never lan_write).
READ_RETRY_PROFILES: frozenset[str] = frozenset({"lan_read", "cloud", "best_effort"})

# Bounded + exponential backoff. Tests may monkeypatch WAIT to wait_none().
READ_RETRY_ATTEMPTS = 3
READ_RETRY_WAIT = wait_exponential(multiplier=0.25, min=0.25, max=2.0)
READ_RETRY_EXCEPTIONS: tuple[type[BaseException], ...] = (
    httpx.TimeoutException,
    httpx.ConnectError,
)


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

    def request(
        self,
        method: str,
        url: str,
        *,
        profile: str,
        timeout: float | httpx.Timeout | None = None,
        **kwargs: Any,
    ) -> httpx.Response:
        """Issue one HTTP call via the pooled client; read profiles may tenacity-retry."""
        return request(
            method, url, profile=profile, timeout=timeout, runtime=self, client=self.client,
            **kwargs,
        )

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


def _call_with_read_retry(profile: str, fn: Callable[[], httpx.Response]) -> httpx.Response:
    """Run ``fn`` once for writes; for read profiles, retry Timeout/ConnectError only."""
    if profile not in READ_RETRY_PROFILES:
        return fn()
    retrying = Retrying(
        stop=stop_after_attempt(READ_RETRY_ATTEMPTS),
        wait=READ_RETRY_WAIT,
        retry=retry_if_exception_type(READ_RETRY_EXCEPTIONS),
        reraise=True,
    )
    return retrying(fn)


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
    """GET/POST via the shared client when available; otherwise one-shot ``httpx.request``.

    Read profiles (``lan_read`` / ``cloud`` / ``best_effort``) may retry the transport call
    on Timeout/ConnectError. ``lan_write`` never retries here.
    """
    rt = runtime if runtime is not None else _default_runtime
    c = client if client is not None else (rt.client if rt is not None else None)
    t = resolve_timeout(profile, timeout=timeout, runtime=rt)

    def _once() -> httpx.Response:
        if c is not None:
            return c.request(method, url, timeout=t, **kwargs)
        return httpx.request(method, url, timeout=t, **kwargs)

    return _call_with_read_retry(profile, _once)


def make_json_get(
    runtime: HttpRuntime,
    profile: str = "lan_read",
    *,
    timeout: float | httpx.Timeout | None = None,
) -> Callable[[str], dict]:
    """``(url) -> dict`` for HomeWizard / Forecast.Solar-style GETs."""

    def get(url: str) -> dict:
        r = request("GET", url, profile=profile, timeout=timeout, runtime=runtime)
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

    def get(url: str, headers: dict[str, str]) -> dict:
        r = request(
            "GET", url, profile=profile, headers=headers, timeout=timeout, runtime=runtime,
        )
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

    def post(url: str, **kwargs: Any) -> httpx.Response:
        return request(
            "POST", url, profile=profile, timeout=timeout, runtime=runtime, **kwargs,
        )

    return post


def make_tibber_post(
    runtime: HttpRuntime,
    profile: str = "cloud",
    *,
    timeout: float | httpx.Timeout | None = None,
) -> Callable[[str, str, dict], dict]:
    """``(url, token, body) -> data`` matching ``TibberPriceSource``'s ``GraphQLPost``."""

    def post(url: str, token: str, body: dict) -> dict:
        r = request(
            "POST", url, profile=profile, timeout=timeout, runtime=runtime,
            json=body, headers={"Authorization": f"Bearer {token}"},
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

    def post(url: str, data: bytes, headers: dict) -> None:
        r = request(
            "POST", url, profile=profile, timeout=timeout, runtime=runtime,
            content=data, headers=headers,
        )
        r.raise_for_status()

    return post


def make_cloud_cover_get(
    runtime: HttpRuntime,
    profile: str = "best_effort",
    *,
    timeout: float | httpx.Timeout | None = None,
) -> Callable[[float, float, float], dict]:
    """``(lat, lon, timeout) -> dict`` matching ``weather.CloudGet`` (ignores per-call timeout)."""
    url = "https://api.open-meteo.com/v1/forecast"

    def get(lat: float, lon: float, _timeout: float) -> dict:
        r = request(
            "GET", url, profile=profile, timeout=timeout, runtime=runtime,
            params={"latitude": lat, "longitude": lon, "current": "cloud_cover"},
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

    def post(keys: Any) -> dict:
        config = json.dumps({"t": list(keys)}).replace(" ", "")
        r = request(
            "POST", url, profile=profile, timeout=timeout, runtime=runtime,
            params={"config": config},
        )
        r.raise_for_status()
        return r.json()

    return post


# Re-export for tests that want a no-wait patch without importing tenacity themselves.
__all__ = [
    "TIMEOUTS",
    "READ_RETRY_PROFILES",
    "READ_RETRY_ATTEMPTS",
    "READ_RETRY_WAIT",
    "READ_RETRY_EXCEPTIONS",
    "HttpRuntime",
    "set_default_runtime",
    "get_default_runtime",
    "resolve_timeout",
    "request",
    "make_json_get",
    "make_json_get_headers",
    "make_json_post",
    "make_tibber_post",
    "make_bytes_post",
    "make_cloud_cover_get",
    "make_indevolt_getdata_post",
    "wait_none",
]
