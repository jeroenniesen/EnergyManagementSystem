import pytest

from ems.sources.indevolt import BatteryUnavailable, IndevoltReadClient, signed_battery_power


def _client(response):
    # rpc_post ignores the requested keys and returns a canned GetData response.
    return IndevoltReadClient("192.168.50.53", rpc_post=lambda keys: response)


def test_signed_battery_power_helper():
    # Domain sign: +discharge / -charge; static -> 0. (magnitude, state) -> signed W
    assert signed_battery_power(800, 1002) == 800.0  # discharging
    assert signed_battery_power(800, 1001) == -800.0  # charging
    assert signed_battery_power(800, 1000) == 0.0  # static
    assert signed_battery_power(-800, 1001) == -800.0  # magnitude taken first, then signed


def test_reads_soc_and_signed_power_discharging():
    power, soc = _client({"6002": 40, "6000": 800, "6001": 1002}).read_power_soc()
    assert soc == 40.0 and power == 800.0


def test_charging_is_negative():
    power, soc = _client({"6002": 64, "6000": 1200, "6001": 1001}).read_power_soc()
    assert power == -1200.0 and soc == 64.0


def test_static_is_zero_power():
    assert _client({"6002": 50, "6000": 0, "6001": 1000}).read_power_soc() == (0.0, 50.0)


def test_empty_response_raises():
    with pytest.raises(BatteryUnavailable):
        _client({}).read_power_soc()


def test_missing_key_raises():
    with pytest.raises(BatteryUnavailable):
        _client({"6002": 40}).read_power_soc()  # power absent


def test_non_numeric_raises():
    with pytest.raises(BatteryUnavailable):
        _client({"6002": "n/a", "6000": 0, "6001": 1000}).read_power_soc()


def test_transport_error_is_wrapped():
    def boom(_keys):
        raise OSError("connection refused")

    with pytest.raises(BatteryUnavailable):
        IndevoltReadClient("x", rpc_post=boom).read_power_soc()


def test_module_has_no_write_surface():
    import ems.sources.indevolt as mod

    src = open(mod.__file__).read()
    # The read client must never construct the write endpoint (the docstring may mention SetData).
    assert "Indevolt.SetData" not in src
