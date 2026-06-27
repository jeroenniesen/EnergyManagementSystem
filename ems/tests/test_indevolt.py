import pytest

from ems.sources.indevolt import BatteryUnavailable, IndevoltReadClient


def _client(payload, registers=None):
    return IndevoltReadClient(
        "192.168.50.53", registers=registers, rpc_get=lambda _url: payload
    )


def test_reads_power_and_soc_from_flat_registers():
    c = _client({"47016": -1200, "47017": 64})
    power, soc = c.read_power_soc()
    assert power == -1200.0  # charging (sign per device; passed through)
    assert soc == 64.0


def test_reads_from_nested_value_shape():
    c = _client({"47016": {"value": 800}, "47017": {"value": 55}})
    assert c.read_power_soc() == (800.0, 55.0)


def test_empty_response_raises_battery_unavailable():
    # This is the live state today: GetData returns {} (OpenData not provisioned / no key).
    with pytest.raises(BatteryUnavailable):
        _client({}).read_power_soc()


def test_missing_register_raises_battery_unavailable():
    with pytest.raises(BatteryUnavailable):
        _client({"47016": 100}).read_power_soc()  # soc register absent


def test_non_numeric_register_value_raises_battery_unavailable():
    with pytest.raises(BatteryUnavailable):
        _client({"47016": "N/A", "47017": 64}).read_power_soc()


def test_transport_error_is_wrapped_as_battery_unavailable():
    def boom(_url):
        raise OSError("connection refused")

    c = IndevoltReadClient("192.168.50.53", rpc_get=boom)
    with pytest.raises(BatteryUnavailable):
        c.read_power_soc()


def test_custom_registers_and_url():
    c = IndevoltReadClient(
        "10.0.0.5", port=8080, config="all", registers={"soc": "S", "power": "P"},
        rpc_get=lambda url: {"S": 90, "P": -50} if "10.0.0.5:8080" in url else {},
    )
    assert c.read_power_soc() == (-50.0, 90.0)


def test_module_has_no_write_methods():
    # Safety: the read-only client must expose nothing that could command the battery.
    import ems.sources.indevolt as mod

    src = (mod.__file__ and open(mod.__file__).read()) or ""
    for forbidden in ("SetData", "indevolt.charge", "indevolt.discharge", "def apply", "def write"):
        assert forbidden not in src, f"unexpected write surface: {forbidden}"
