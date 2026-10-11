"""#225 — SmartThings ConsumerPort adapter (mocked HTTP, no live ST calls)."""
from __future__ import annotations

import json

from ems.consumers import ConsumerPort, build_consumer_sources
from ems.consumers.models import ConsumerKind, ConsumerState
from ems.consumers.selection import SMARTTHINGS_DEVICE_IDS_KEY
from ems.consumers.smartthings import (
    SmartThingsConsumerSource,
    classify_laundry,
)
from ems.settings import defaults

WASHER_ID = "washer-aaa"
DRYER_ID = "dryer-bbb"

DEVICES_PAYLOAD = {
    "items": [
        {
            "deviceId": WASHER_ID,
            "label": "Bijkeuken wasmachine",
            "name": "washer",
            "deviceTypeName": "Samsung OCF Washer",
            "components": [
                {
                    "id": "main",
                    "capabilities": [
                        {"id": "washerOperatingState"},
                        {"id": "powerMeter"},
                        {"id": "energyMeter"},
                        {"id": "switch"},
                    ],
                }
            ],
        },
        {
            "deviceId": DRYER_ID,
            "label": "Bijkeuken droger",
            "name": "dryer",
            "components": [
                {
                    "id": "main",
                    "capabilities": [
                        {"id": "dryerOperatingState"},
                        {"id": "powerMeter"},
                    ],
                }
            ],
        },
        {
            "deviceId": "tv-ccc",
            "label": "Living TV",
            "components": [{"id": "main", "capabilities": [{"id": "switch"}]}],
        },
    ]
}

WASHER_STATUS = {
    "components": {
        "main": {
            "washerOperatingState": {"machineState": {"value": "run"}},
            "powerMeter": {"power": {"value": 420}},
            "energyMeter": {"energy": {"value": 1.5}},
            "switch": {"switch": {"value": "on"}},
        }
    }
}

DRYER_STATUS_IDLE = {
    "components": {
        "main": {
            "dryerOperatingState": {"machineState": {"value": "ready"}},
            "powerMeter": {"power": {"value": 0}},
            "switch": {"switch": {"value": "off"}},
        }
    }
}


def _fake_get(url: str, headers: dict[str, str]) -> dict:
    assert headers.get("Authorization", "").startswith("Bearer ")
    if url.endswith("/devices"):
        return DEVICES_PAYLOAD
    if f"/devices/{WASHER_ID}/status" in url:
        return WASHER_STATUS
    if f"/devices/{DRYER_ID}/status" in url:
        return DRYER_STATUS_IDLE
    raise AssertionError(f"unexpected URL {url}")


def test_classify_laundry_from_capabilities_and_name():
    assert classify_laundry(DEVICES_PAYLOAD["items"][0]) is ConsumerKind.WASHER
    assert classify_laundry(DEVICES_PAYLOAD["items"][1]) is ConsumerKind.DRYER
    assert classify_laundry(DEVICES_PAYLOAD["items"][2]) is None


def test_smartthings_port_discovers_only_laundry_and_opt_in():
    settings = {
        SMARTTHINGS_DEVICE_IDS_KEY: json.dumps([WASHER_ID]),
    }
    src = SmartThingsConsumerSource("test-pat", http_get=_fake_get, settings=settings)
    assert isinstance(src, ConsumerPort)
    assert src.integration == "smartthings"
    devices = src.list_devices()
    ids = {d.id for d in devices}
    assert ids == {WASHER_ID, DRYER_ID}
    assert "tv-ccc" not in ids
    opted = [d for d in devices if d.opted_in]
    assert [d.id for d in opted] == [WASHER_ID]


def test_smartthings_status_maps_running_power_energy():
    settings = {SMARTTHINGS_DEVICE_IDS_KEY: json.dumps([WASHER_ID])}
    src = SmartThingsConsumerSource("test-pat", http_get=_fake_get, settings=settings)
    st = src.get_status(WASHER_ID)
    assert st is not None
    assert st.state is ConsumerState.RUNNING
    assert st.power_w == 420.0
    assert st.energy_kwh == 1.5
    idle = src.get_status(DRYER_ID)
    assert idle is not None
    assert idle.state is ConsumerState.IDLE
    assert src.get_status("nope") is None


def test_smartthings_status_offline_on_http_error():
    def boom(url: str, headers: dict[str, str]) -> dict:
        if url.endswith("/devices"):
            return DEVICES_PAYLOAD
        raise RuntimeError("timeout")

    src = SmartThingsConsumerSource("pat", http_get=boom, settings={
        SMARTTHINGS_DEVICE_IDS_KEY: json.dumps([WASHER_ID]),
    })
    src.list_devices()
    st = src.get_status(WASHER_ID)
    assert st is not None
    assert st.state is ConsumerState.OFFLINE


def test_factory_requires_enabled_and_pat():
    base = defaults()
    assert build_consumer_sources(base, dev_mode="live") == []

    on_no_pat = {**base, "consumers.smartthings.enabled": True}
    assert build_consumer_sources(on_no_pat, dev_mode="live") == []

    on = {
        **base,
        "consumers.smartthings.enabled": True,
        "consumers.smartthings.pat": "secret-pat",
    }
    sources = build_consumer_sources(on, dev_mode="live")
    assert len(sources) == 1
    assert sources[0].integration == "smartthings"


def test_factory_adds_mock_in_mock_mode():
    sources = build_consumer_sources(defaults(), dev_mode="mock")
    assert any(s.integration == "mock" for s in sources)


def test_no_battery_write_surface():
    src = SmartThingsConsumerSource("pat", http_get=_fake_get)
    assert not hasattr(src, "apply")
    assert not hasattr(src, "start")
    assert not hasattr(src, "armed")


def test_power_consumption_report_wh_to_kwh():
    """Samsung PCR.energy is Wh — expose kWh to the UI."""
    payload = {
        "items": [{
            "deviceId": WASHER_ID,
            "label": "Washer",
            "deviceTypeName": "Washer",
            "components": [{
                "id": "main",
                "capabilities": [
                    {"id": "washerOperatingState"},
                    {"id": "powerConsumptionReport"},
                ],
            }],
        }],
    }
    status = {
        "components": {
            "main": {
                "washerOperatingState": {"machineState": {"value": "run"}},
                "powerConsumptionReport": {
                    "powerConsumption": {
                        "value": {"power": 380, "energy": 2500},  # Wh
                    }
                },
            }
        }
    }

    def get(url: str, headers: dict[str, str]) -> dict:
        if url.endswith("/devices"):
            return payload
        return status

    # Nested under .value in some firmware — also accept direct dict on attribute.
    # Our parser reads attribute.value; reshape to ST common shape:
    status["components"]["main"]["powerConsumptionReport"] = {
        "powerConsumption": {"value": json.dumps({"power": 380, "energy": 2500})},
    }
    src = SmartThingsConsumerSource("pat", http_get=get)
    st = src.get_status(WASHER_ID)
    assert st is not None
    assert st.power_w == 380.0
    assert st.energy_kwh == 2.5


def test_list_failure_retries_next_call():
    calls = {"n": 0}

    def flaky(url: str, headers: dict[str, str]) -> dict:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("down")
        return DEVICES_PAYLOAD

    src = SmartThingsConsumerSource("pat", http_get=flaky)
    try:
        src.list_devices()
        raise AssertionError("expected raise")
    except RuntimeError:
        pass
    devices = src.list_devices()
    assert {d.id for d in devices} == {WASHER_ID, DRYER_ID}


def test_classify_ignores_label_only():
    assert classify_laundry({
        "deviceId": "x",
        "label": "Washer room light",
        "components": [{"id": "main", "capabilities": [{"id": "switch"}]}],
    }) is None


def test_smartthings_settings_require_restart():
    from ems.settings import SETTINGS_BY_KEY
    assert SETTINGS_BY_KEY["consumers.smartthings.enabled"].applies == "restart"
    assert SETTINGS_BY_KEY["consumers.smartthings.pat"].applies == "restart"
