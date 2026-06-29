from zoneinfo import ZoneInfo

from ems.connection import _battery_ips, build_wiring
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
    clean, errors = validate_settings({"meters.p1_ip": "192.0.2.10"})
    assert clean["meters.p1_ip"] == "192.0.2.10" and errors == {}
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
    src, price, _fc, batt_ep, _driver, dev_mode, dry_run = build_wiring(effective_settings({}), AMS)
    assert isinstance(src, MockSource)
    assert isinstance(price, MockPriceSource)
    assert dev_mode == "mock"
    assert batt_ep is not None  # mock battery endpoint present
    assert dry_run is True  # default is always safe dry-run


def test_build_wiring_live_devices_when_configured():
    eff = effective_settings({
        "connection.use_live_devices": True,
        "meters.p1_ip": "192.0.2.10",
        "meters.solar_ip": "192.0.2.11",
        "meters.car_ip": "192.0.2.12",
        "battery.indevolt_ip": "192.0.2.20",
    })
    src, _price, _fc, batt_ep, driver, dev_mode, dry_run = build_wiring(eff, AMS)
    # LiveSource composes the three meters; never touches hardware at construction.
    assert dev_mode == "live"
    assert hasattr(src, "read_sample")  # LiveSource
    assert batt_ep is None  # /api/battery null until probe; driver is the unarmed Indevolt driver
    assert driver.armed is False
    assert dry_run is True  # operational not enabled -> still dry-run


def test_build_wiring_omits_missing_solar_car_meters_no_p1_impersonation():
    # Only P1 configured → solar/car meters must be ABSENT (None), never the P1 meter reused.
    eff = effective_settings({
        "connection.use_live_devices": True,
        "meters.p1_ip": "192.0.2.10",
        "meters.solar_ip": "",
        "meters.car_ip": "",
    })
    src, *_ = build_wiring(eff, AMS)
    assert src.solar is None and src.car is None  # degraded, not impersonated
    assert src.p1 is not None and src.p1.ip == "192.0.2.10"


def test_build_wiring_live_prices_when_token_present():
    eff = effective_settings({"connection.use_live_prices": True, "prices.tibber_token": "tok"})
    _src, price, *_ = build_wiring(eff, AMS)
    from ems.sources.tibber import TibberPriceSource

    assert isinstance(price, TibberPriceSource)


def test_build_wiring_live_prices_ignored_without_token():
    eff = effective_settings({"connection.use_live_prices": True})  # no token
    _src, price, *_ = build_wiring(eff, AMS)
    assert isinstance(price, MockPriceSource)


def test_operational_arms_driver_and_lifts_dry_run():
    eff = effective_settings({
        "connection.use_live_devices": True, "meters.p1_ip": "192.0.2.10",
        "battery.indevolt_ip": "192.0.2.20", "control.operational": True,
    })
    *_, driver, dev_mode, dry_run = build_wiring(eff, AMS)
    assert dev_mode == "live"
    assert driver.armed is True  # operational -> armed with a real SetData transport
    assert dry_run is False


def test_operational_without_a_battery_stays_dry_run():
    # Operational only means something with a real battery to command — else stay safe.
    eff = effective_settings({
        "connection.use_live_devices": True, "meters.p1_ip": "192.0.2.10",
        "control.operational": True,  # but no battery.indevolt_ip
    })
    *_, _driver, _dev_mode, dry_run = build_wiring(eff, AMS)
    assert dry_run is True


def test_operational_ignored_without_live_devices():
    eff = effective_settings({"control.operational": True})  # mock devices
    *_, _driver, _dev_mode, dry_run = build_wiring(eff, AMS)
    assert dry_run is True


def test_battery_ips_orders_master_first_and_dedupes():
    assert _battery_ips("192.0.2.20", "192.0.2.21, 192.0.2.99") == [
        "192.0.2.20", "192.0.2.21", "192.0.2.99",
    ]
    # blanks dropped, master never duplicated, whitespace trimmed
    assert _battery_ips("10.0.0.1", " 10.0.0.1 , , 10.0.0.2 ") == ["10.0.0.1", "10.0.0.2"]
    assert _battery_ips("", None) == []


def test_live_devices_build_a_multi_tower_cluster_reader():
    eff = effective_settings({
        "connection.use_live_devices": True, "meters.p1_ip": "192.0.2.10",
        "battery.indevolt_ip": "192.0.2.20",
        "battery.indevolt_ips_extra": "192.0.2.21",
    })
    src, *_ = build_wiring(eff, AMS)
    # LiveSource holds a cluster reader spanning both towers (never touches hardware at build).
    assert [c.ip for c in src.battery._clients] == ["192.0.2.20", "192.0.2.21"]
