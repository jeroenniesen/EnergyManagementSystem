"""#232: SSRF / outbound URL and device-host hardening."""
from __future__ import annotations

import ipaddress

import pytest

from ems.settings import effective_settings, settings_migration_writes, validate_settings
from ems.url_safety import (
    guard_outbound_url,
    hostname_is_private_ip,
    normalize_device_host,
    normalize_ntfy_topic,
    normalize_outbound_http_url,
    normalize_private_ipv4,
    prepare_outbound_request,
)


def test_ntfy_url_accepts_public_https():
    assert normalize_outbound_http_url("https://ntfy.sh/") == "https://ntfy.sh"
    clean, err = validate_settings({"notify.ntfy_url": "https://ntfy.sh"})
    assert clean["notify.ntfy_url"] == "https://ntfy.sh" and not err


def test_ntfy_url_rejects_private_without_opt_in():
    with pytest.raises(ValueError, match="private"):
        normalize_outbound_http_url("http://192.168.1.50", allow_private=False)
    _c, err = validate_settings({"notify.ntfy_url": "http://192.168.1.50"})
    assert "notify.ntfy_url" in err


def test_ntfy_url_allows_private_with_opt_in():
    assert normalize_outbound_http_url(
        "http://192.168.1.50", allow_private=True,
    ) == "http://192.168.1.50"
    clean, err = validate_settings({
        "notify.allow_lan_url": True,
        "notify.ntfy_url": "http://192.168.1.50",
    })
    assert not err
    assert clean["notify.ntfy_url"] == "http://192.168.1.50"
    assert clean["notify.allow_lan_url"] is True


def test_ntfy_url_always_rejects_link_local_and_metadata():
    for raw in (
        "http://169.254.169.254",
        "http://metadata.google.internal",
        "http://100.100.100.200",
        "http://168.63.129.16",
        "http://instance-data",
        "http://instance-data.ec2.internal",
    ):
        with pytest.raises(ValueError):
            normalize_outbound_http_url(raw, allow_private=True)
        _c, err = validate_settings({
            "notify.allow_lan_url": True, "notify.ntfy_url": raw,
        })
        assert "notify.ntfy_url" in err


def test_weird_ipv4_literals_rejected_as_loopback():
    """H1: decimal/hex/short forms that glibc dials as 127.0.0.1 must not pass save-time."""
    for raw in (
        "http://2130706433",
        "http://0x7f000001",
        "http://0x7f.0.0.1",
        "http://127.1",
        "http://0",
    ):
        with pytest.raises(ValueError):
            normalize_outbound_http_url(raw, allow_private=False)
        with pytest.raises(ValueError):
            normalize_device_host(raw.removeprefix("http://"))


def test_public_ipv4_literal_does_not_raise(monkeypatch):
    """M1: ``is_site_local`` must not AttributeError on IPv4."""
    assert normalize_outbound_http_url("http://1.1.1.1") == "http://1.1.1.1"
    assert hostname_is_private_ip("1.1.1.1") is False
    assert hostname_is_private_ip("8.8.8.8") is False
    clean, err = validate_settings({"notify.ntfy_url": "http://1.1.1.1"})
    assert not err and clean["notify.ntfy_url"] == "http://1.1.1.1"


def test_explainer_base_url_keeps_path_rejects_private():
    assert normalize_outbound_http_url(
        "https://api.minimax.io/v1/", allow_path=True,
    ) == "https://api.minimax.io/v1"
    clean, err = validate_settings({
        "explainer.base_url": "https://api.minimax.io/v1",
    })
    assert clean["explainer.base_url"] == "https://api.minimax.io/v1" and not err
    _c, err2 = validate_settings({"explainer.base_url": "http://10.0.0.5/v1"})
    assert "explainer.base_url" in err2


def test_ntfy_topic_rejects_path_manipulators():
    assert normalize_ntfy_topic("my-secret-topic") == "my-secret-topic"
    for bad in ("a/b", "a?x=1", "a#frag", "a\\b"):
        with pytest.raises(ValueError):
            normalize_ntfy_topic(bad)
        _c, err = validate_settings({"notify.ntfy_topic": bad})
        assert "notify.ntfy_topic" in err


def test_device_host_accepts_ip_rejects_url_shaping():
    assert normalize_device_host("192.168.1.10") == "192.168.1.10"
    # M3: IPv6 must stay bracketed for ``http://{host}/...`` builders.
    assert normalize_device_host("2001:db8::1") == "[2001:db8::1]"
    assert normalize_device_host("[2001:db8::1]") == "[2001:db8::1]"
    with pytest.raises(ValueError, match="not a full URL"):
        normalize_device_host("http://192.168.1.10/api")
    with pytest.raises(ValueError, match="port"):
        normalize_device_host("192.168.1.10:80")
    _c, err = validate_settings({"meters.p1_ip": "http://evil/x"})
    assert "meters.p1_ip" in err
    clean, e2 = validate_settings({
        "battery.indevolt_ips_extra": "192.168.1.2, 192.168.1.3",
    })
    assert not e2
    assert clean["battery.indevolt_ips_extra"] == "192.168.1.2, 192.168.1.3"


def test_legacy_private_ntfy_migrates_allow_lan_opt_in():
    writes = settings_migration_writes({
        "notify.ntfy_url": "http://10.0.0.8",
        "notify.ntfy_topic": "hems",
    })
    assert writes.get("notify.allow_lan_url") is True
    eff = effective_settings({
        "notify.ntfy_url": "http://10.0.0.8",
        "notify.ntfy_topic": "hems",
    })
    assert eff["notify.allow_lan_url"] is True
    assert eff["notify.ntfy_url"] == "http://10.0.0.8"


def test_migration_does_not_enable_lan_for_cloud_internal(monkeypatch):
    """M2: ``*.internal`` metadata hosts must not flip allow_lan_url."""
    writes = settings_migration_writes({
        "notify.ntfy_url": "http://instance-data.ec2.internal",
    })
    assert "notify.allow_lan_url" not in writes


def test_prepare_outbound_pins_http_lan_and_sets_host(monkeypatch):
    pinned, headers = prepare_outbound_request(
        "http://192.168.1.50/hems", allow_private=True,
    )
    assert pinned == "http://192.168.1.50/hems"
    assert headers.get("Host") == "192.168.1.50"


def test_guard_rejects_dns_to_loopback(monkeypatch):
    """H1 connect-time: names that resolve to loopback are rejected before dial."""
    def fake_resolve(hostname: str):
        return [ipaddress.IPv4Address("127.0.0.1")]

    monkeypatch.setattr("ems.url_safety.resolve_host_ips", fake_resolve)
    with pytest.raises(ValueError, match="loopback"):
        guard_outbound_url("http://127.0.0.1.nip.io", allow_private=False)
    with pytest.raises(ValueError, match="loopback"):
        prepare_outbound_request("http://localtest.me/x", allow_private=False)


def test_guard_rejects_dns_to_metadata_even_with_lan(monkeypatch):
    def fake_resolve(hostname: str):
        return [ipaddress.IPv4Address("169.254.169.254")]

    monkeypatch.setattr("ems.url_safety.resolve_host_ips", fake_resolve)
    with pytest.raises(ValueError, match="metadata|link-local"):
        prepare_outbound_request(
            "http://169.254.169.254.nip.io/latest", allow_private=True,
        )


def test_homewizard_make_json_get_path_does_not_dial_ssrf_hosts(monkeypatch):
    """Production wiring injects ``make_json_get``; meter ``read()`` must not create_connection."""
    import socket

    from ems.http_client import HttpRuntime, make_json_get
    from ems.sources.live import HomeWizardMeter

    dialed: list[tuple] = []
    real_cc = socket.create_connection

    def spy_cc(address, *args, **kwargs):
        dialed.append(address)
        raise OSError("ssrf-test-block")

    monkeypatch.setattr(socket, "create_connection", spy_cc)

    import httpx

    with HttpRuntime(client=httpx.Client()) as rt:
        getter = make_json_get(rt, "lan_read")
        for host in (
            "127.0.0.1.nip.io",
            "localtest.me",
            "169.254.169.254.nip.io",
            "ip6-localhost",
        ):
            dialed.clear()
            meter = HomeWizardMeter(host, http_get=getter)
            with pytest.raises(ValueError):
                meter.read()
            assert dialed == [], f"{host} reached create_connection: {dialed}"

    # Restore unused binding for linters / clarity (spy already via monkeypatch).
    assert real_cc is not None


@pytest.mark.parametrize(
    "ok",
    ["192.168.1.1", "10.0.0.5", "172.20.3.4"],
)
def test_normalize_private_ipv4_accepts_rfc1918(ok: str):
    assert normalize_private_ipv4(ok) == ok


@pytest.mark.parametrize(
    "bad",
    [
        "127.0.0.1",
        "169.254.1.1",
        "100.64.0.1",
        "8.8.8.8",
        "::1",
        "fe80::1",
        "http://192.168.1.1",
        "1.2.3.4:80",
        "myrouter.local",
        "",
    ],
)
def test_normalize_private_ipv4_rejects_non_private(bad: str):
    with pytest.raises(ValueError):
        normalize_private_ipv4(bad)
