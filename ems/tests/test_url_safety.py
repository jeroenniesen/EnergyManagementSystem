"""#232: SSRF / outbound URL and device-host hardening."""
from __future__ import annotations

import pytest

from ems.settings import effective_settings, settings_migration_writes, validate_settings
from ems.url_safety import (
    normalize_device_host,
    normalize_ntfy_topic,
    normalize_outbound_http_url,
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
    for raw in ("http://169.254.169.254", "http://metadata.google.internal"):
        with pytest.raises(ValueError):
            normalize_outbound_http_url(raw, allow_private=True)
        _c, err = validate_settings({
            "notify.allow_lan_url": True, "notify.ntfy_url": raw,
        })
        assert "notify.ntfy_url" in err


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
    assert normalize_device_host("2001:db8::1") == "2001:db8::1"
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
