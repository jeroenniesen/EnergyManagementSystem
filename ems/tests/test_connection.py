from zoneinfo import ZoneInfo

from ems.connection import build_wiring
from ems.settings import (
    SETTINGS_BY_KEY,
    effective_settings,
    public_values,
    schema_json,
    validate_settings,
)
from ems.sources.mock import MockSource
from ems.sources.prices import MockPriceSource

AMS = ZoneInfo("Europe/Amsterdam")


def test_text_and_secret_validation():
    clean, errors = validate_settings({"meters.p1_ip": "192.168.50.92"})
    assert clean["meters.p1_ip"] == "192.168.50.92" and errors == {}
    _c, e = validate_settings({"meters.p1_ip": 123})  # not a string
    assert "meters.p1_ip" in e


def test_blank_secret_is_dropped_not_stored():
    # A blank token means "keep the current value" — it must not overwrite/clear the stored one.
    clean, errors = validate_settings({"prices.tibber_token": ""})
    assert clean == {} and errors == {}
    clean2, _ = validate_settings({"prices.tibber_token": "tok-123"})
    assert clean2 == {"prices.tibber_token": "tok-123"}


def test_public_values_masks_secret():
    eff = effective_settings({"prices.tibber_token": "super-secret"})
    pub = public_values(eff)
    assert pub["prices.tibber_token"] == ""  # never leaked
    assert pub["prices.tibber_token.__set"] is True
    assert public_values(effective_settings({}))["prices.tibber_token.__set"] is False


def test_schema_exposes_advanced_and_applies():
    by_key = {f["key"]: f for f in schema_json()}
    assert by_key["planner.round_trip_efficiency"]["advanced"] is True
    assert by_key["battery.usable_kwh"]["advanced"] is False
    assert by_key["meters.p1_ip"]["applies"] == "restart"
    assert by_key["ui.theme"]["applies"] == "live"
    # every schema field is represented
    assert set(by_key) == set(SETTINGS_BY_KEY)


def test_build_wiring_defaults_to_mock():
    src, price, forecast, batt_ep, driver, dev_mode = build_wiring(effective_settings({}), AMS)
    assert isinstance(src, MockSource)
    assert isinstance(price, MockPriceSource)
    assert dev_mode == "mock"
    assert batt_ep is not None  # mock battery endpoint present


def test_build_wiring_live_devices_when_configured():
    eff = effective_settings({
        "connection.use_live_devices": True,
        "meters.p1_ip": "192.168.50.92",
        "meters.solar_ip": "192.168.50.37",
        "meters.car_ip": "192.168.50.98",
        "battery.indevolt_ip": "192.168.50.53",
    })
    src, _price, _fc, batt_ep, driver, dev_mode = build_wiring(eff, AMS)
    # LiveSource composes the three meters; never touches hardware at construction.
    assert dev_mode == "live"
    assert hasattr(src, "read_sample")  # LiveSource
    assert batt_ep is None  # /api/battery null until probe; driver is the unarmed Indevolt driver
    assert driver.armed is False


def test_build_wiring_live_prices_when_token_present():
    eff = effective_settings({"connection.use_live_prices": True, "prices.tibber_token": "tok"})
    _src, price, *_ = build_wiring(eff, AMS)
    from ems.sources.tibber import TibberPriceSource

    assert isinstance(price, TibberPriceSource)


def test_build_wiring_live_prices_ignored_without_token():
    eff = effective_settings({"connection.use_live_prices": True})  # no token
    _src, price, *_ = build_wiring(eff, AMS)
    assert isinstance(price, MockPriceSource)
