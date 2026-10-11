"""#226 — Home Assistant ConsumerPort adapter (mocked HTTP, no live HA)."""
from __future__ import annotations

import json

from ems.consumers import ConsumerPort, build_consumer_sources
from ems.consumers.homeassistant import (
    HomeAssistantConsumerSource,
    classify_ha_entity,
)
from ems.consumers.models import ConsumerKind, ConsumerState
from ems.consumers.selection import HA_DEVICE_IDS_KEY
from ems.settings import SETTINGS_BY_KEY, defaults

WASHER = "switch.bijkeuken_washer"
POWER = "sensor.bijkeuken_washer_power"
ENERGY = "sensor.bijkeuken_washer_energy"
DRYER = "switch.bijkeuken_dryer"
TV = "switch.living_tv"

STATES = [
    {
        "entity_id": WASHER,
        "state": "on",
        "attributes": {"friendly_name": "Bijkeuken wasmachine"},
    },
    {
        "entity_id": POWER,
        "state": "410",
        "attributes": {
            "friendly_name": "Washer power",
            "device_class": "power",
            "unit_of_measurement": "W",
        },
    },
    {
        "entity_id": ENERGY,
        "state": "1.25",
        "attributes": {
            "friendly_name": "Washer energy",
            "device_class": "energy",
            "unit_of_measurement": "kWh",
        },
    },
    {
        "entity_id": DRYER,
        "state": "off",
        "attributes": {"friendly_name": "Bijkeuken droger"},
    },
    {
        "entity_id": TV,
        "state": "on",
        "attributes": {"friendly_name": "Living TV"},
    },
]


def _fake_get(url: str, headers: dict[str, str]):
    assert headers.get("Authorization", "").startswith("Bearer ")
    if url.endswith("/api/states"):
        return STATES
    for ent in STATES:
        if url.endswith(f"/api/states/{ent['entity_id']}"):
            return ent
    raise AssertionError(f"unexpected URL {url}")


def test_classify_ha_entity_laundry_only():
    assert classify_ha_entity(STATES[0]) is ConsumerKind.WASHER
    assert classify_ha_entity(STATES[3]) is ConsumerKind.DRYER
    assert classify_ha_entity(STATES[4]) is None


def test_ha_port_discovers_laundry_and_opt_in():
    settings = {HA_DEVICE_IDS_KEY: json.dumps([WASHER])}
    src = HomeAssistantConsumerSource(
        "http://ha.local:8123", "tok", http_get=_fake_get, settings=settings,
    )
    assert isinstance(src, ConsumerPort)
    assert src.integration == "ha"
    devices = src.list_devices()
    ids = {d.id for d in devices}
    assert WASHER in ids and DRYER in ids
    assert TV not in ids
    # Power/energy sensors are siblings — never listed as appliances.
    assert POWER not in ids and ENERGY not in ids
    assert [d.id for d in devices if d.opted_in] == [WASHER]


def test_set_settings_rediscovers_new_opt_in():
    src = HomeAssistantConsumerSource(
        "http://ha.local:8123", "tok", http_get=_fake_get, settings={},
    )
    assert all(not d.opted_in for d in src.list_devices())
    src.set_settings({HA_DEVICE_IDS_KEY: json.dumps([WASHER])})
    opted = [d.id for d in src.list_devices() if d.opted_in]
    assert opted == [WASHER]


def test_ha_status_maps_running_and_sibling_power_energy():
    settings = {HA_DEVICE_IDS_KEY: json.dumps([WASHER])}
    src = HomeAssistantConsumerSource(
        "http://ha.local:8123", "tok", http_get=_fake_get, settings=settings,
    )
    st = src.get_status(WASHER)
    assert st is not None
    assert st.state is ConsumerState.RUNNING
    assert st.power_w == 410.0
    assert st.energy_kwh == 1.25
    idle = src.get_status(DRYER)
    assert idle is not None
    assert idle.state is ConsumerState.IDLE


def test_ha_list_failure_retries():
    calls = {"n": 0}

    def flaky(url: str, headers: dict[str, str]):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("down")
        return STATES

    src = HomeAssistantConsumerSource("http://ha.local:8123", "tok", http_get=flaky)
    try:
        src.list_devices()
        raise AssertionError("expected raise")
    except RuntimeError:
        pass
    assert {d.id for d in src.list_devices()} >= {WASHER, DRYER}


def test_factory_ha_requires_enabled_url_token():
    base = defaults()
    assert all(s.integration != "ha" for s in build_consumer_sources(base, dev_mode="live"))

    partial = {**base, "consumers.ha.enabled": True, "consumers.ha.url": "http://ha"}
    assert all(s.integration != "ha" for s in build_consumer_sources(partial, dev_mode="live"))

    full = {
        **base,
        "consumers.ha.enabled": True,
        "consumers.ha.url": "http://ha.local:8123",
        "consumers.ha.token": "secret",
    }
    sources = build_consumer_sources(full, dev_mode="live")
    assert any(s.integration == "ha" for s in sources)


def test_ha_settings_require_restart():
    assert SETTINGS_BY_KEY["consumers.ha.enabled"].applies == "restart"
    assert SETTINGS_BY_KEY["consumers.ha.url"].applies == "restart"
    assert SETTINGS_BY_KEY["consumers.ha.token"].applies == "restart"


def test_ha_url_must_be_http_https_host_only():
    """#266: reject file:/credentials/path smuggling; allow LAN hosts."""
    from ems.settings import validate_settings

    clean, errors = validate_settings({"consumers.ha.url": "http://192.168.1.10:8123"})
    assert errors == {}
    assert clean["consumers.ha.url"] == "http://192.168.1.10:8123"

    clean2, err2 = validate_settings({"consumers.ha.url": "https://ha.local/"})
    assert err2 == {}
    assert clean2["consumers.ha.url"] == "https://ha.local"

    for bad in (
        "file:///etc/passwd",
        "http://",
        "ftp://ha.local",
        "http://user:pass@ha.local",
        "http://ha.local/api",
        "http://ha.local?x=1",
        "not-a-url",
    ):
        _c, e = validate_settings({"consumers.ha.url": bad})
        assert "consumers.ha.url" in e, bad

    # Empty stays allowed (clear / unset).
    c3, e3 = validate_settings({"consumers.ha.url": ""})
    assert c3 == {"consumers.ha.url": ""} and e3 == {}


def test_factory_ha_uses_lan_read_profile(monkeypatch):
    """#266: HA GETs should use lan_read timeouts, not cloud."""
    profiles: list[str] = []

    def capture(http, profile):
        profiles.append(profile)
        return lambda url, headers: []

    import ems.http_client as http_client

    monkeypatch.setattr(http_client, "make_json_get_headers", capture)
    full = {
        **defaults(),
        "consumers.ha.enabled": True,
        "consumers.ha.url": "http://192.168.1.5:8123",
        "consumers.ha.token": "secret",
        "consumers.smartthings.enabled": True,
        "consumers.smartthings.pat": "st-pat",
    }
    sources = build_consumer_sources(full, http=object(), dev_mode="live")
    assert {s.integration for s in sources} == {"ha", "smartthings"}
    assert profiles == ["cloud", "lan_read"]


def test_no_battery_or_ha_write_surface():
    src = HomeAssistantConsumerSource("http://ha", "t", http_get=_fake_get)
    assert not hasattr(src, "apply")
    assert not hasattr(src, "start")
    assert not hasattr(src, "call_service")
    assert not hasattr(src, "armed")
