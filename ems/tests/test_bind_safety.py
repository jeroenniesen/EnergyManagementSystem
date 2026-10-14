"""#234: first-admin bind/onboard safety helpers."""
from __future__ import annotations

from ems.web.bind_safety import is_loopback_host, resolve_listen_host


def test_is_loopback_host_accepts_ipv4_ipv6_localhost():
    assert is_loopback_host("127.0.0.1")
    assert is_loopback_host("::1")
    assert is_loopback_host("[::1]")
    assert is_loopback_host("localhost")
    assert is_loopback_host("127.0.0.2")  # 127/8


def test_is_loopback_host_rejects_lan_and_empty():
    assert not is_loopback_host("192.168.1.10")
    assert not is_loopback_host("10.0.0.2")
    assert not is_loopback_host("0.0.0.0")
    assert not is_loopback_host("testclient")
    assert not is_loopback_host("")
    assert not is_loopback_host(None)


def test_resolve_listen_host_defaults_local_without_token():
    assert resolve_listen_host(explicit=None, has_shared_token=False) == "127.0.0.1"


def test_resolve_listen_host_opens_when_token_present():
    assert resolve_listen_host(explicit=None, has_shared_token=True) == "0.0.0.0"


def test_resolve_listen_host_explicit_wins():
    assert resolve_listen_host(explicit="10.0.0.5", has_shared_token=False) == "10.0.0.5"
    assert resolve_listen_host(explicit="  ::  ", has_shared_token=False) == "::"
