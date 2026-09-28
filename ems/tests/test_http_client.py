"""Shared sync httpx client + named timeout profiles (#173) + read retries (#175)."""
from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient
from tenacity import wait_none

from ems import http_client as http_client_mod
from ems.http_client import (
    READ_RETRY_ATTEMPTS,
    READ_RETRY_PROFILES,
    TIMEOUTS,
    HttpRuntime,
    get_default_runtime,
    make_json_get,
    make_tibber_post,
    request,
    resolve_timeout,
    set_default_runtime,
)
from ems.sources.indevolt_driver import make_setdata_post
from ems.sources.mock import MockSource
from ems.web.api import create_app


@pytest.fixture(autouse=True)
def _no_retry_wait(monkeypatch):
    """Keep retry tests fast — production still uses exponential backoff."""
    monkeypatch.setattr(http_client_mod, "READ_RETRY_WAIT", wait_none())


def test_named_timeout_profiles():
    assert set(TIMEOUTS) == {"lan_read", "lan_write", "cloud", "best_effort"}
    assert TIMEOUTS["lan_read"].connect == 1.5
    assert TIMEOUTS["lan_write"].read == 8.0
    assert TIMEOUTS["cloud"].connect == 3.0
    assert TIMEOUTS["best_effort"].read == 10.0
    assert READ_RETRY_PROFILES == frozenset({"lan_read", "cloud", "best_effort"})
    assert "lan_write" not in READ_RETRY_PROFILES


def test_unknown_profile_raises():
    rt = HttpRuntime()
    with pytest.raises(ValueError, match="unknown http timeout profile"):
        rt.timeout("nope")
    rt.close()


def test_resolve_timeout_override_and_fallback():
    assert resolve_timeout("cloud", timeout=7.0) == 7.0
    assert resolve_timeout("lan_write") == 8.0  # no runtime → float fallback
    with HttpRuntime() as rt:
        t = resolve_timeout("lan_read", runtime=rt)
        assert isinstance(t, httpx.Timeout)
        assert t.connect == 1.5


def test_runtime_close_is_idempotent():
    rt = HttpRuntime()
    assert not rt.closed
    rt.close()
    assert rt.closed
    rt.close()  # second close must not raise
    assert rt.closed


def test_set_default_runtime_used_by_request(monkeypatch):
    calls: list[str] = []

    class _FakeClient:
        def request(self, method, url, **kwargs):
            calls.append(method)
            return httpx.Response(200, json={"ok": True}, request=httpx.Request(method, url))

        def close(self):
            pass

    rt = HttpRuntime(client=_FakeClient())  # type: ignore[arg-type]
    set_default_runtime(rt)
    try:
        r = request("GET", "http://example.test/x", profile="lan_read")
        assert r.json() == {"ok": True}
        assert calls == ["GET"]
    finally:
        set_default_runtime(None)
        rt.close()


def test_make_json_get_uses_shared_client():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/data"
        return httpx.Response(200, json={"active_power_w": 42})

    transport = httpx.MockTransport(handler)
    with HttpRuntime(client=httpx.Client(transport=transport)) as rt:
        get = make_json_get(rt, "lan_read")
        assert get("http://192.0.2.1/api/v1/data") == {"active_power_w": 42}


def test_make_tibber_post_raises_on_graphql_errors():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"errors": [{"message": "nope"}]})

    transport = httpx.MockTransport(handler)
    with HttpRuntime(client=httpx.Client(transport=transport)) as rt:
        post = make_tibber_post(rt, "cloud")
        with pytest.raises(RuntimeError, match="Tibber GraphQL error"):
            post("https://api.tibber.com/v1-beta/gql", "tok", {"query": "{}"})


def test_make_setdata_post_uses_lan_write_profile_no_transport_retries():
    """Write transport must use shared client + lan_write; no Transport(retries)."""
    seen: list[httpx.Timeout | float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport)
    with HttpRuntime(client=client) as rt:
        set_default_runtime(rt)
        try:
            post = make_setdata_post("192.0.2.10", timeout=None, client=rt.client)
            orig = rt.client.request

            def _wrap(method, url, **kw):
                seen.append(kw.get("timeout"))
                return orig(method, url, **kw)

            rt.client.request = _wrap  # type: ignore[method-assign]
            assert post(47005, [1]) == {"ok": True}
            assert len(seen) == 1
            assert seen[0] == TIMEOUTS["lan_write"]
        finally:
            set_default_runtime(None)


def test_lifespan_closes_http_runtime():
    rt = HttpRuntime()
    app = create_app(MockSource(), dry_run=True, dev_mode="mock", http_runtime=rt)
    assert not rt.closed
    with TestClient(app):
        assert not rt.closed
    assert rt.closed


def test_get_default_runtime_roundtrip():
    assert get_default_runtime() is None
    with HttpRuntime() as rt:
        set_default_runtime(rt)
        assert get_default_runtime() is rt
        set_default_runtime(None)
    assert get_default_runtime() is None


# --- #175 tenacity read retries ---


class _CountingClient:
    """Fake httpx.Client that counts transport calls and raises a scripted sequence."""

    def __init__(self, outcomes: list[BaseException | httpx.Response]) -> None:
        self.outcomes = list(outcomes)
        self.calls = 0

    def request(self, method: str, url: str, **kwargs):
        self.calls += 1
        if not self.outcomes:
            raise AssertionError("unexpected extra transport call")
        item = self.outcomes.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    def close(self):
        pass


def _ok(method: str = "GET", url: str = "http://example.test/") -> httpx.Response:
    return httpx.Response(200, json={"ok": True}, request=httpx.Request(method, url))


@pytest.mark.parametrize("profile", sorted(READ_RETRY_PROFILES))
def test_read_profile_retries_timeout_then_succeeds(profile: str):
    client = _CountingClient([
        httpx.ConnectTimeout("slow"),
        httpx.ReadTimeout("slow"),
        _ok(),
    ])
    with HttpRuntime(client=client) as rt:  # type: ignore[arg-type]
        r = rt.request("GET", "http://example.test/", profile=profile)
        assert r.json() == {"ok": True}
        assert client.calls == 3


@pytest.mark.parametrize("profile", sorted(READ_RETRY_PROFILES))
def test_read_profile_retries_connect_error_then_succeeds(profile: str):
    client = _CountingClient([
        httpx.ConnectError("refused"),
        _ok(),
    ])
    with HttpRuntime(client=client) as rt:  # type: ignore[arg-type]
        r = request("GET", "http://example.test/", profile=profile, runtime=rt)
        assert r.json() == {"ok": True}
        assert client.calls == 2


@pytest.mark.parametrize("profile", sorted(READ_RETRY_PROFILES))
def test_read_profile_reraises_after_exhaustion(profile: str):
    client = _CountingClient([
        httpx.ConnectTimeout("a"),
        httpx.ConnectTimeout("b"),
        httpx.ConnectTimeout("c"),
    ])
    with HttpRuntime(client=client) as rt:  # type: ignore[arg-type]
        with pytest.raises(httpx.ConnectTimeout):
            request("GET", "http://example.test/", profile=profile, runtime=rt)
        assert client.calls == READ_RETRY_ATTEMPTS


def test_read_does_not_retry_http_status_error():
    """4xx/5xx are not transport failures — no tenacity retry."""
    req = httpx.Request("GET", "http://example.test/")
    resp = httpx.Response(503, request=req)
    client = _CountingClient([httpx.HTTPStatusError("nope", request=req, response=resp)])
    with HttpRuntime(client=client) as rt:  # type: ignore[arg-type]
        with pytest.raises(httpx.HTTPStatusError):
            request("GET", "http://example.test/", profile="cloud", runtime=rt)
        assert client.calls == 1


def test_lan_write_does_not_tenacity_retry_on_timeout():
    """SetData path: transport Timeout must NOT be retried by tenacity (driver owns retries)."""
    client = _CountingClient([httpx.ConnectTimeout("write-timeout")])
    with HttpRuntime(client=client) as rt:  # type: ignore[arg-type]
        set_default_runtime(rt)
        try:
            post = make_setdata_post("192.0.2.10", timeout=None, client=rt.client)
            with pytest.raises(httpx.ConnectTimeout):
                post(47005, [1])
            assert client.calls == 1  # no tenacity second attempt
        finally:
            set_default_runtime(None)


def test_make_json_get_retries_via_shared_request():
    """Factories must go through the retry path (not raw client.get)."""
    client = _CountingClient([
        httpx.ConnectError("blip"),
        httpx.Response(
            200, json={"active_power_w": 7},
            request=httpx.Request("GET", "http://192.0.2.1/api/v1/data"),
        ),
    ])
    with HttpRuntime(client=client) as rt:  # type: ignore[arg-type]
        get = make_json_get(rt, "lan_read")
        assert get("http://192.0.2.1/api/v1/data") == {"active_power_w": 7}
        assert client.calls == 2
