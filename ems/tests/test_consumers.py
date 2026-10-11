"""#223 — consumer port, device model, opt-in selection (no cloud/LAN)."""
from __future__ import annotations

import json

from ems.consumers import (
    ConsumerCapabilities,
    ConsumerDevice,
    ConsumerKind,
    ConsumerPort,
    apply_opt_in,
    filter_opted_in,
    opted_in_ids,
    parse_device_ids,
)
from ems.consumers.mock import MockConsumerSource
from ems.consumers.selection import (
    HA_DEVICE_IDS_KEY,
    MOCK_DEVICE_IDS_KEY,
    SMARTTHINGS_DEVICE_IDS_KEY,
)
from ems.settings import SETTINGS_BY_KEY, defaults, schema_json, validate_settings


def test_parse_device_ids_tolerant() -> None:
    assert parse_device_ids(None) == ()
    assert parse_device_ids("") == ()
    assert parse_device_ids("[]") == ()
    assert parse_device_ids('["a", "b"]') == ("a", "b")
    assert parse_device_ids(["a", "a", " b "]) == ("a", "b")
    assert parse_device_ids("a, b, a") == ("a", "b")
    assert parse_device_ids("{not-json") == ()
    assert parse_device_ids(42) == ()


def test_opt_in_default_is_empty() -> None:
    settings = defaults()
    assert settings[SMARTTHINGS_DEVICE_IDS_KEY] == "[]"
    assert settings[HA_DEVICE_IDS_KEY] == "[]"
    assert settings[MOCK_DEVICE_IDS_KEY] == "[]"
    assert opted_in_ids(settings, "smartthings") == frozenset()
    assert opted_in_ids(settings, "ha") == frozenset()
    assert opted_in_ids(settings, "mock") == frozenset()


def test_consumer_settings_hidden_from_settings_schema() -> None:
    """#223: keys exist for persistence but Manage UI is a later slice (#224)."""
    keys = {row["key"] for row in schema_json()}
    assert SMARTTHINGS_DEVICE_IDS_KEY not in keys
    assert HA_DEVICE_IDS_KEY not in keys
    assert MOCK_DEVICE_IDS_KEY not in keys
    assert SMARTTHINGS_DEVICE_IDS_KEY in SETTINGS_BY_KEY


def test_apply_opt_in_marks_selected_devices() -> None:
    devices = [
        ConsumerDevice(
            id="st-1", name="Washer", kind=ConsumerKind.WASHER,
            integration="smartthings",
            capabilities=ConsumerCapabilities(supports_delayed_start=True),
        ),
        ConsumerDevice(
            id="st-2", name="Dryer", kind=ConsumerKind.DRYER,
            integration="smartthings",
            capabilities=ConsumerCapabilities(),
        ),
        ConsumerDevice(
            id="ha-1", name="Dish", kind=ConsumerKind.DISHWASHER,
            integration="ha",
            capabilities=ConsumerCapabilities(reports_power=True),
        ),
    ]
    settings = {
        SMARTTHINGS_DEVICE_IDS_KEY: json.dumps(["st-1"]),
        HA_DEVICE_IDS_KEY: "[]",
    }
    marked = apply_opt_in(devices, settings)
    assert [d.id for d in marked if d.opted_in] == ["st-1"]
    assert filter_opted_in(marked) == [marked[0]]


def test_mock_consumer_port_conformance_and_status() -> None:
    src = MockConsumerSource(settings={MOCK_DEVICE_IDS_KEY: '["mock-washer-1"]'})
    assert isinstance(src, ConsumerPort)
    assert src.integration == "mock"
    devices = src.list_devices()
    assert {d.id for d in devices} == {"mock-washer-1", "mock-dryer-1"}
    assert [d.id for d in devices if d.opted_in] == ["mock-washer-1"]
    status = src.get_status("mock-washer-1")
    assert status is not None
    assert status.state == "idle"
    assert status.can_start is True
    assert src.get_status("nope") is None


def test_mock_never_exposes_battery_write_surface() -> None:
    """Hard limit: consumers path must not look like a battery writer."""
    src = MockConsumerSource()
    assert not hasattr(src, "apply")
    assert not hasattr(src, "configure_power_limits")
    assert not hasattr(src, "armed")


def test_validate_accepts_device_ids_json() -> None:
    clean, errors = validate_settings({
        SMARTTHINGS_DEVICE_IDS_KEY: '["device-abc"]',
    })
    assert errors == {}
    assert clean[SMARTTHINGS_DEVICE_IDS_KEY] == '["device-abc"]'
