from zoneinfo import ZoneInfo

from ems.connection import Wiring, _battery_ips, build_carbon_source, build_wiring
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


def test_ntfy_url_must_be_http_or_https():
    clean, errors = validate_settings({"notify.ntfy_url": "https://ntfy.sh"})
    assert clean == {"notify.ntfy_url": "https://ntfy.sh"} and errors == {}
    _c, e = validate_settings({"notify.ntfy_url": "file:///etc/passwd"})
    assert "notify.ntfy_url" in e
    _c2, e2 = validate_settings({"notify.ntfy_url": ""})
    assert _c2 == {"notify.ntfy_url": ""} and e2 == {}


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


def test_build_wiring_returns_namedtuple_unpackable_as_legacy_7tuple():
    """#138: Wiring is a NamedTuple; positional unpacking keeps working (no behaviour change)."""
    wiring = build_wiring(effective_settings({}), AMS)
    assert isinstance(wiring, Wiring)
    assert issubclass(Wiring, tuple)
    assert Wiring._fields == (
        "source",
        "price_source",
        "solar_forecast",
        "battery_endpoint",
        "controller_driver",
        "dev_mode",
        "dry_run",
    )
    src, price, _fc, batt_ep, _driver, dev_mode, dry_run = wiring
    assert wiring.source is src
    assert wiring.price_source is price
    assert wiring.battery_endpoint is batt_ep
    assert wiring.dev_mode == dev_mode
    assert wiring.dry_run is dry_run


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


def test_build_wiring_solcast_when_configured():
    eff = effective_settings({
        "connection.use_live_devices": True,
        "meters.p1_ip": "192.0.2.10",
        "site.lat": 52.13,
        "site.lon": 5.29,
        "solar.forecast_provider": "solcast",
        "solar.solcast_api_key": "tok-solcast",
        "solar.solcast_resource_id": "rid-123",
    })
    _src, _price, fc, *_ = build_wiring(eff, AMS)
    from ems.sources.solcast import SolcastSource

    assert isinstance(fc, SolcastSource)
    assert fc.resource_id == "rid-123"


def test_build_wiring_solcast_without_creds_falls_back_to_forecast_solar():
    eff = effective_settings({
        "connection.use_live_devices": True,
        "meters.p1_ip": "192.0.2.10",
        "site.lat": 52.13,
        "site.lon": 5.29,
        "solar.forecast_provider": "solcast",  # no key / resource id
    })
    _src, _price, fc, *_ = build_wiring(eff, AMS)
    from ems.sources.forecast_solar import ForecastSolarSource

    assert isinstance(fc, ForecastSolarSource)


def test_build_wiring_default_live_forecast_is_forecast_solar():
    eff = effective_settings({
        "connection.use_live_devices": True,
        "meters.p1_ip": "192.0.2.10",
        "site.lat": 52.13,
        "site.lon": 5.29,
    })
    _src, _price, fc, *_ = build_wiring(eff, AMS)
    from ems.sources.forecast_solar import ForecastSolarSource

    assert isinstance(fc, ForecastSolarSource)


def test_operational_arms_driver_and_lifts_dry_run():
    eff = effective_settings({
        "connection.use_live_devices": True, "meters.p1_ip": "192.0.2.10",
        "battery.indevolt_ip": "192.0.2.20", "control.operational": True,
        "connection.use_live_prices": True, "prices.tibber_token": "tok",
    })
    # force_dry_run=False = config.yaml control.dry_run: false (explicit opt-in to live writes).
    *_, driver, dev_mode, dry_run = build_wiring(eff, AMS, force_dry_run=False)
    assert dev_mode == "live"
    assert driver.armed is True  # operational -> armed with a real SetData transport
    assert dry_run is False


def test_config_dry_run_wins_over_operational():
    # #136 Klaar-als: dry_run: true + operational: true → dry_run stays True (config wins),
    # even when live Tibber prices would otherwise allow arming (#126).
    eff = effective_settings({
        "connection.use_live_devices": True, "meters.p1_ip": "192.0.2.10",
        "battery.indevolt_ip": "192.0.2.20", "control.operational": True,
        "connection.use_live_prices": True, "prices.tibber_token": "tok",
    })
    *_, driver, _dev_mode, dry_run = build_wiring(eff, AMS, force_dry_run=True)
    assert dry_run is True
    assert driver.armed is False  # config dry-run also keeps the writer unarmed


def test_force_dry_run_default_is_fail_safe():
    # Omitting force_dry_run must NOT fail open (#136 F4) — default True keeps watch-only
    # even when live prices would otherwise allow arming (#126).
    eff = effective_settings({
        "connection.use_live_devices": True, "meters.p1_ip": "192.0.2.10",
        "battery.indevolt_ip": "192.0.2.20", "control.operational": True,
        "connection.use_live_prices": True, "prices.tibber_token": "tok",
    })
    *_, driver, _dev_mode, dry_run = build_wiring(eff, AMS)
    assert dry_run is True
    assert driver.armed is False


def test_config_forced_dry_run_reason_when_operational_blocked(caplog):
    import logging

    from ems.connection import CONFIG_FORCED_DRY_RUN_REASON, config_forced_dry_run_reason

    eff = effective_settings({
        "connection.use_live_devices": True, "meters.p1_ip": "192.0.2.10",
        "battery.indevolt_ip": "192.0.2.20", "control.operational": True,
        "connection.use_live_prices": True, "prices.tibber_token": "tok",
    })
    assert config_forced_dry_run_reason(eff, force_dry_run=True) == CONFIG_FORCED_DRY_RUN_REASON
    assert config_forced_dry_run_reason(eff, force_dry_run=False) is None
    # Without live prices, #126 would block anyway — no config-dry-run reason.
    no_prices = effective_settings({
        "connection.use_live_devices": True, "meters.p1_ip": "192.0.2.10",
        "battery.indevolt_ip": "192.0.2.20", "control.operational": True,
    })
    assert config_forced_dry_run_reason(no_prices, force_dry_run=True) is None
    with caplog.at_level(logging.WARNING, logger="ems.connection"):
        build_wiring(eff, AMS, force_dry_run=True)
    assert any("config forces watch-only" in r.message for r in caplog.records)


def test_live_indevolt_driver_uses_configured_cluster_power_limits():
    eff = effective_settings({
        "connection.use_live_devices": True,
        "meters.p1_ip": "192.0.2.10",
        "battery.indevolt_ip": "192.0.2.20",
        "battery.max_charge_w": 4800.0,
        "battery.max_discharge_w": 3600.0,
        "control.operational": True,
        "connection.use_live_prices": True, "prices.tibber_token": "tok",
    })
    *_, driver, _dev_mode, _dry_run = build_wiring(eff, AMS, force_dry_run=False)
    assert driver.charge_power_w == 4800
    assert driver.discharge_power_w == 3600


def test_operational_without_a_battery_stays_dry_run():
    # Operational only means something with a real battery to command — else stay safe.
    eff = effective_settings({
        "connection.use_live_devices": True, "meters.p1_ip": "192.0.2.10",
        "control.operational": True,  # but no battery.indevolt_ip
        "connection.use_live_prices": True, "prices.tibber_token": "tok",
    })
    *_, _driver, _dev_mode, dry_run = build_wiring(eff, AMS)
    assert dry_run is True


def test_operational_ignored_without_live_devices():
    eff = effective_settings({
        "control.operational": True,
        "connection.use_live_prices": True, "prices.tibber_token": "tok",
    })  # mock devices
    *_, _driver, _dev_mode, dry_run = build_wiring(eff, AMS)
    assert dry_run is True


def test_operational_with_mock_prices_stays_dry_run():
    """#126: operational without a live Tibber source must never lift dry_run."""
    eff = effective_settings({
        "connection.use_live_devices": True, "meters.p1_ip": "192.0.2.10",
        "battery.indevolt_ip": "192.0.2.20", "control.operational": True,
        # no use_live_prices / tibber token → MockPriceSource
    })
    _src, price, _fc, _batt, driver, _dev_mode, dry_run = build_wiring(eff, AMS)
    assert isinstance(price, MockPriceSource)
    assert driver.armed is False
    assert dry_run is True


def test_operational_with_live_prices_flag_but_no_token_stays_dry_run():
    """#126: use_live_prices without a token falls back to mock → stay dry-run."""
    eff = effective_settings({
        "connection.use_live_devices": True, "meters.p1_ip": "192.0.2.10",
        "battery.indevolt_ip": "192.0.2.20", "control.operational": True,
        "connection.use_live_prices": True,  # token missing
    })
    _src, price, _fc, _batt, driver, _dev_mode, dry_run = build_wiring(eff, AMS)
    assert isinstance(price, MockPriceSource)
    assert driver.armed is False
    assert dry_run is True


def test_battery_ips_orders_master_first_and_dedupes():
    assert _battery_ips("192.0.2.20", "192.0.2.21, 192.0.2.99") == [
        "192.0.2.20", "192.0.2.21", "192.0.2.99",
    ]
    # blanks dropped, master never duplicated, whitespace trimmed
    assert _battery_ips("10.0.0.1", " 10.0.0.1 , , 10.0.0.2 ") == ["10.0.0.1", "10.0.0.2"]
    assert _battery_ips("", None) == []


def test_build_carbon_source_defaults_to_static():
    from ems.sources.carbon import StaticCarbonSource

    cs = build_carbon_source(effective_settings({}))
    assert isinstance(cs, StaticCarbonSource)
    assert cs.factor == 0.27


def test_build_carbon_source_uses_configured_flat_factor():
    from ems.sources.carbon import StaticCarbonSource

    cs = build_carbon_source(effective_settings({"reporting.grid_co2_factor": 0.15}))
    assert isinstance(cs, StaticCarbonSource)
    assert cs.factor == 0.15


def test_build_carbon_source_electricitymaps_with_key():
    from ems.sources.carbon import ElectricityMapsCarbonSource

    eff = effective_settings({
        "reporting.carbon_signal": "electricitymaps",
        "reporting.electricitymaps_api_key": "key-123",
    })
    cs = build_carbon_source(eff)
    assert isinstance(cs, ElectricityMapsCarbonSource)
    assert cs.api_key == "key-123"


def test_build_carbon_source_electricitymaps_without_key_falls_back_to_static():
    from ems.sources.carbon import StaticCarbonSource

    eff = effective_settings({"reporting.carbon_signal": "electricitymaps"})  # no key configured
    cs = build_carbon_source(eff)
    assert isinstance(cs, StaticCarbonSource)


def test_live_devices_build_a_multi_tower_cluster_reader():
    eff = effective_settings({
        "connection.use_live_devices": True, "meters.p1_ip": "192.0.2.10",
        "battery.indevolt_ip": "192.0.2.20",
        "battery.indevolt_ips_extra": "192.0.2.21",
    })
    src, *_ = build_wiring(eff, AMS)
    # LiveSource holds a cluster reader spanning both towers (never touches hardware at build).
    assert [c.ip for c in src.battery._clients] == ["192.0.2.20", "192.0.2.21"]
