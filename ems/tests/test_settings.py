import json

from ems.ev_schedule import default_schedule
from ems.settings import (
    SETTINGS_BY_KEY,
    SETTINGS_SCHEMA,
    VALIDATE_SETTINGS_WITHOUT_DEV_MODE,
    defaults,
    effective_settings,
    live_mock_forecast_in_use,
    live_mock_forecast_provider_active,
    schema_json,
    validate_settings,
)


def test_minimum_reserve_schema_bounds_and_default():
    field = SETTINGS_BY_KEY["battery.min_reserve_soc"]
    shown = next(f for f in schema_json() if f["key"] == field.key)
    assert shown["min"] == field.min
    assert shown["max"] == field.max
    assert shown["step"] == field.step
    assert shown["unit"] == field.unit
    assert shown["default"] == field.default
    assert field.min <= field.default <= field.max


def test_defaults_cover_every_field():
    d = defaults()
    assert set(d) == set(SETTINGS_BY_KEY)
    assert d["planner.round_trip_efficiency"] == 0.90
    assert d["ui.theme"] == "auto"
    assert d["control.allow_export_discharge"] is False


def test_export_model_confirmed_defaults_and_validation():
    """#341 / F-11: flag defaults false, bool-only, hidden from Manage; reads do not confirm."""
    d = defaults()
    assert d["prices.export_model_confirmed"] is False
    assert d["prices.export_price_model"] == "spot_minus_tax"
    field = SETTINGS_BY_KEY["prices.export_model_confirmed"]
    assert field.type == "bool" and field.show_in_settings is False
    assert all(f["key"] != "prices.export_model_confirmed" for f in schema_json())
    clean, errors = validate_settings({"prices.export_model_confirmed": True})
    assert errors == {} and clean["prices.export_model_confirmed"] is True
    clean_f, err_f = validate_settings({"prices.export_model_confirmed": False})
    assert err_f == {} and clean_f["prices.export_model_confirmed"] is False
    _c, err = validate_settings({"prices.export_model_confirmed": "yes"})
    assert "prices.export_model_confirmed" in err
    # Validation of the model must not co-write confirmation — effective_settings reuses it.
    clean, errors = validate_settings({"prices.export_price_model": "net_metering"})
    assert errors == {}
    assert clean["prices.export_price_model"] == "net_metering"
    assert "prices.export_model_confirmed" not in clean
    stored = effective_settings({
        "prices.export_price_model": "net_metering",
        "prices.export_model_confirmed": False,
    })
    assert stored["prices.export_price_model"] == "net_metering"
    assert stored["prices.export_model_confirmed"] is False
    legacy = effective_settings({"prices.export_price_model": "spot_minus_tax"})
    assert legacy["prices.export_model_confirmed"] is False


def test_stale_reserve_buffer_pp_defaults_and_bounds():
    d = defaults()
    assert d["planner.stale_reserve_buffer_pp"] == 5.0
    field = SETTINGS_BY_KEY["planner.stale_reserve_buffer_pp"]
    assert field.advanced is True
    assert field.min == 0.0 and field.max == 20.0
    clean, errors = validate_settings({"planner.stale_reserve_buffer_pp": 20.0})
    assert errors == {} and clean["planner.stale_reserve_buffer_pp"] == 20.0
    _c, err = validate_settings({"planner.stale_reserve_buffer_pp": 21.0})
    assert "planner.stale_reserve_buffer_pp" in err
    _c, err_neg = validate_settings({"planner.stale_reserve_buffer_pp": -1.0})
    assert "planner.stale_reserve_buffer_pp" in err_neg


def test_trading_settings_defaults_match_e11_lock():
    """B-103 / #209: trading keys exist, default OFF / spot_minus_tax / €0.50 / uncapped."""
    d = defaults()
    assert d["planner.trading_enabled"] is False
    assert d["planner.trading_min_extra_eur"] == 0.50
    assert d["planner.max_export_kwh_per_day"] == 0.0
    assert d["planner.min_export_kwh"] == 0.5
    assert d["planner.export_mode"] == "peak_slice"
    assert d["planner.max_cycles_per_day"] == 1.5
    assert d["planner.daily_min_savings_eur"] == 0.20
    assert d["prices.export_price_model"] == "spot_minus_tax"
    assert d["control.allow_export_discharge"] is False
    field = SETTINGS_BY_KEY["planner.export_mode"]
    assert field.options == ("peak_slice", "full_dump")
    clean, errors = validate_settings({
        "planner.trading_enabled": True,
        "planner.trading_min_extra_eur": 0.75,
        "planner.max_export_kwh_per_day": 3.0,
        "planner.export_mode": "full_dump",
    })
    assert errors == {}
    assert clean["planner.trading_enabled"] is True
    assert clean["planner.export_mode"] == "full_dump"
    _c, err = validate_settings({"planner.export_mode": "surplus_fill"})
    assert "planner.export_mode" in err

def test_control_dry_run_setting_defaults_fail_safe():
    """#171: Settings control.dry_run defaults True (watch-only) and is restart-tagged."""
    d = defaults()
    assert d["control.dry_run"] is True
    field = SETTINGS_BY_KEY["control.dry_run"]
    assert field.type == "bool"
    assert field.applies == "restart"
    assert field.default is True
    # Missing store key → effective True (fail-safe), never silently armed.
    assert effective_settings({})["control.dry_run"] is True
    clean, errors = validate_settings({"control.dry_run": False})
    assert clean["control.dry_run"] is False and "control.dry_run" not in errors
    # Non-bool is rejected by validate; effective_settings drops invalid → schema default True.
    _c, err = validate_settings({"control.dry_run": "maybe"})
    assert "control.dry_run" in err


def test_anti_flap_control_knob_defaults():
    d = defaults()
    assert d["control.intent_persistence_cycles"] == 2
    assert d["control.max_commitment_switches_per_day"] == 4
    assert d["control.commitment_reserve"] == 3  # deprecated key, still loads
    assert SETTINGS_BY_KEY["control.intent_persistence_cycles"].type == "int"
    assert SETTINGS_BY_KEY["control.intent_persistence_cycles"].min == 1
    field = SETTINGS_BY_KEY["control.max_commitment_switches_per_day"]
    assert field.type == "int" and field.min == 1 and field.max == 10
    assert SETTINGS_BY_KEY["control.commitment_reserve"].type == "int"


def test_grid_limit_setting_default():
    """#133 / #197: watt ceiling defaults to 1×25 A; fuse defaults to unset."""
    d = defaults()
    assert d["control.grid_limit_w"] == 5750.0
    assert d["control.grid_fuse"] == "unset"
    field = SETTINGS_BY_KEY["control.grid_limit_w"]
    assert field.type == "number" and field.min == 0.0 and field.unit == "W"
    assert field.visible_when == (("control.grid_fuse", "custom"),)
    fuse = SETTINGS_BY_KEY["control.grid_fuse"]
    assert fuse.type == "enum"
    assert fuse.options == ("unset", "1x25", "1x35", "3x25", "3x35", "custom")
    clean, errors = validate_settings({"control.grid_limit_w": 0})
    assert clean["control.grid_limit_w"] == 0.0 and "control.grid_limit_w" not in errors
    clean, errors = validate_settings({"control.grid_limit_w": 17250})
    assert clean["control.grid_limit_w"] == 17250.0
    _clean, errors = validate_settings({"control.grid_limit_w": -1})
    assert "control.grid_limit_w" in errors


def test_grid_fuse_preset_sets_watt_ceiling():
    """#197: choosing a NL preset on save also writes the matching watt ceiling."""
    from ems.settings import GRID_FUSE_PRESET_W

    for token, watts in GRID_FUSE_PRESET_W.items():
        clean, errors = validate_settings({"control.grid_fuse": token})
        assert errors == {}
        assert clean["control.grid_fuse"] == token
        assert clean["control.grid_limit_w"] == watts
    # unset keeps the conservative schema default watts.
    clean, errors = validate_settings({"control.grid_fuse": "unset"})
    assert errors == {} and clean["control.grid_fuse"] == "unset"
    assert clean["control.grid_limit_w"] == 5750.0
    # custom leaves watts alone unless submitted with the fuse.
    clean, errors = validate_settings({"control.grid_fuse": "custom"})
    assert errors == {} and clean == {"control.grid_fuse": "custom"}
    clean, errors = validate_settings(
        {"control.grid_fuse": "custom", "control.grid_limit_w": 20000},
    )
    assert errors == {}
    assert clean["control.grid_fuse"] == "custom"
    assert clean["control.grid_limit_w"] == 20000.0


def test_effective_settings_infers_fuse_from_stored_watts():
    """#197: an older install that already stored watts (no fuse key) is treated as confirmed."""
    from ems.settings import fuse_from_watts

    assert fuse_from_watts(17250.0) == "3x25"
    assert fuse_from_watts(9999.0) == "custom"
    # Stored watts only → infer fuse; do not leave unset.
    eff = effective_settings({"control.grid_limit_w": 17250})
    assert eff["control.grid_fuse"] == "3x25"
    assert eff["control.grid_limit_w"] == 17250.0
    # Custom stored watts → custom fuse.
    eff = effective_settings({"control.grid_limit_w": 9000})
    assert eff["control.grid_fuse"] == "custom"
    assert eff["control.grid_limit_w"] == 9000.0
    # Empty store → unset (do not pretend the 5750 default was chosen).
    eff = effective_settings({})
    assert eff["control.grid_fuse"] == "unset"
    assert eff["control.grid_limit_w"] == 5750.0
    # Explicit preset in store aligns watts even if an old mismatched watt row exists.
    eff = effective_settings({"control.grid_fuse": "3x25", "control.grid_limit_w": 5750})
    assert eff["control.grid_fuse"] == "3x25"
    assert eff["control.grid_limit_w"] == 17250.0


def test_schema_json_shape():
    rows = schema_json()
    shown = {f.key for f in SETTINGS_SCHEMA if f.show_in_settings}
    assert {r["key"] for r in rows} == shown
    # Trading owns confirm-to-arm; the key stays valid but is not a Manage checkbox.
    assert "control.allow_export_discharge" in SETTINGS_BY_KEY
    assert "control.allow_export_discharge" not in shown
    theme = next(r for r in rows if r["key"] == "ui.theme")
    assert theme["type"] == "enum"
    assert theme["options"] == ["auto", "dark", "light"]


def test_default_forecast_provider_is_forecast_solar():
    assert defaults()["solar.forecast_provider"] == "forecast_solar"
    assert SETTINGS_BY_KEY["solar.forecast_provider"].options[0] == "forecast_solar"


def test_forecast_provider_schema_options_come_from_registry():
    """#113: UI enum is the live forecast adapter registry, not a second hard-coded list."""
    from ems.sources.forecast_factory import registered_providers

    row = next(r for r in schema_json() if r["key"] == "solar.forecast_provider")
    assert tuple(row["options"]) == registered_providers()
    assert "forecast_solar" in row["options"]
    assert "solcast" in row["options"]


def test_carbon_signal_schema_options_come_from_registry():
    from ems.sources.carbon_factory import registered_providers

    row = next(r for r in schema_json() if r["key"] == "reporting.carbon_signal")
    assert tuple(row["options"]) == registered_providers()
    assert set(row["options"]) >= {"static", "electricitymaps"}


def test_validate_accepts_registry_forecast_provider_mock():
    """Internal/read path: dev_mode=None deliberately allows mock (see #242 contract)."""
    clean, errors = validate_settings({"solar.forecast_provider": "mock"})
    assert errors == {}
    assert clean["solar.forecast_provider"] == "mock"


def test_live_schema_disables_mock_forecast_provider():
    """#242: live installs must not offer mock as a selectable solar provider."""
    row = next(r for r in schema_json(dev_mode="live") if r["key"] == "solar.forecast_provider")
    assert "mock" in row["options"]
    assert "mock" in row["disabled_options"]


def test_mock_dev_mode_schema_allows_mock_forecast_provider():
    row = next(r for r in schema_json(dev_mode="mock") if r["key"] == "solar.forecast_provider")
    assert "mock" in row["options"]
    assert "mock" not in row["disabled_options"]


def test_validate_rejects_mock_forecast_provider_when_live():
    clean, errors = validate_settings({"solar.forecast_provider": "mock"}, dev_mode="live")
    assert "solar.forecast_provider" not in clean
    assert "solar.forecast_provider" in errors
    assert "mock" in errors["solar.forecast_provider"].lower()


def test_validate_accepts_mock_forecast_provider_in_mock_dev_mode():
    clean, errors = validate_settings({"solar.forecast_provider": "mock"}, dev_mode="mock")
    assert errors == {}
    assert clean["solar.forecast_provider"] == "mock"


def test_replay_dev_mode_schema_allows_mock_forecast_provider():
    row = next(r for r in schema_json(dev_mode="replay") if r["key"] == "solar.forecast_provider")
    assert "mock" not in row["disabled_options"]


def test_validate_accepts_mock_forecast_provider_in_replay_dev_mode():
    clean, errors = validate_settings({"solar.forecast_provider": "mock"}, dev_mode="replay")
    assert errors == {}
    assert clean["solar.forecast_provider"] == "mock"


def test_mock_forecast_provider_gate_normalises_dev_mode():
    row = next(
        r for r in schema_json(dev_mode="MOCK") if r["key"] == "solar.forecast_provider"
    )
    assert "mock" not in row["disabled_options"]
    row_live = next(
        r for r in schema_json(dev_mode=" live ") if r["key"] == "solar.forecast_provider"
    )
    assert "mock" in row_live["disabled_options"]


def test_live_mock_forecast_provider_active_on_stored_mock():
    eff = effective_settings({"solar.forecast_provider": "mock"})
    assert live_mock_forecast_provider_active(eff, "live") is True
    assert live_mock_forecast_provider_active(eff, "mock") is False
    assert live_mock_forecast_provider_active(eff, None) is False


def test_live_mock_forecast_in_use_covers_restart_window():
    """Saved Forecast.Solar but process still serving MockSolarForecastSource → still gated."""
    from zoneinfo import ZoneInfo

    from ems.sources.forecast import MockSolarForecastSource

    eff = effective_settings({"solar.forecast_provider": "forecast_solar"})
    mock_src = MockSolarForecastSource(ZoneInfo("Europe/Amsterdam"))
    assert live_mock_forecast_provider_active(eff, "live") is False
    assert live_mock_forecast_in_use(mock_src, eff, "live") is True
    assert live_mock_forecast_in_use(mock_src, eff, "mock") is False
    assert live_mock_forecast_in_use(None, eff, "live") is False


def test_live_mock_forecast_provider_marks_data_quality_degraded():
    from ems.alerts import data_quality

    fresh = {"grid": "fresh", "soc": "fresh", "solar": "fresh", "prices": "fresh"}
    assert data_quality(fresh, prices_ok=True, forecast_ok=False) == "degraded"


def test_validate_settings_without_dev_mode_contract_is_documented():
    assert VALIDATE_SETTINGS_WITHOUT_DEV_MODE == frozenset({
        "effective_settings",
        "plan_preview",
        "replay",
        "whatif",
    })


def test_default_planner_mode_is_rule_based():
    assert defaults()["planner.mode"] == "rule_based"
    assert SETTINGS_BY_KEY["planner.mode"].options == ("rule_based", "ml", "advisory")
    assert SETTINGS_BY_KEY["planner.mode"].disabled_options == ("ml", "advisory")
    row = next(r for r in schema_json() if r["key"] == "planner.mode")
    assert row["disabled_options"] == ["ml", "advisory"]


def test_planner_mode_rejects_disabled_ml_and_advisory():
    from ems.settings import validate_settings

    clean, errors = validate_settings({"planner.mode": "ml"})
    assert "planner.mode" not in clean
    assert "planner.mode" in errors
    assert "not available" in errors["planner.mode"]

    clean, errors = validate_settings({"planner.mode": "advisory"})
    assert "planner.mode" not in clean
    assert "not available" in errors["planner.mode"]

    clean, errors = validate_settings({"planner.mode": "rule_based"})
    assert clean["planner.mode"] == "rule_based"
    assert "planner.mode" not in errors


def test_solcast_fields_gated_behind_provider():
    from ems.settings import field_visible

    for key in (
        "solar.solcast_api_key",
        "solar.solcast_resource_id",
        "solar.solcast_daily_call_budget",
    ):
        field = SETTINGS_BY_KEY[key]
        assert field.visible_when == (("solar.forecast_provider", "solcast"),)
        assert field_visible(field, {"solar.forecast_provider": "forecast_solar"}) is False
        assert field_visible(field, {"solar.forecast_provider": "solcast"}) is True
    # Provider itself is always visible.
    assert field_visible(SETTINGS_BY_KEY["solar.forecast_provider"], {}) is True
    row = next(r for r in schema_json() if r["key"] == "solar.solcast_api_key")
    assert row["visible_when"] == {"solar.forecast_provider": "solcast"}


def test_effective_overlays_valid_stored():
    eff = effective_settings({"planner.charge_slots": 8, "ui.theme": "dark"})
    assert eff["planner.charge_slots"] == 8
    assert eff["ui.theme"] == "dark"
    assert eff["planner.discharge_slots"] == 24  # untouched default


def test_effective_drops_invalid_and_unknown_stored():
    # A corrupt/legacy persisted row must never break the dashboard — silently fall back.
    eff = effective_settings(
        {"planner.charge_slots": 999, "ui.theme": "rainbow", "bogus": 1, "_": "x"}
    )
    assert eff["planner.charge_slots"] == 12  # out of range -> default
    assert eff["ui.theme"] == "auto"  # bad enum -> default
    assert "bogus" not in eff


def test_validate_rejects_unknown_key():
    clean, errors = validate_settings({"nope": 1})
    assert clean == {}
    assert errors["nope"] == "unknown setting"


def test_validate_enum_membership():
    _clean, errors = validate_settings({"ui.theme": "neon"})
    assert "ui.theme" in errors
    clean, errors2 = validate_settings({"ui.theme": "light"})
    assert clean == {"ui.theme": "light"} and errors2 == {}


def test_validate_bool_is_not_a_number():
    # bool subclasses int in Python; a number field must reject True, not coerce it to 1.
    _clean, errors = validate_settings({"control.min_dwell_seconds": True})
    assert "control.min_dwell_seconds" in errors


def test_validate_int_rejects_fractional_accepts_whole():
    _c, errors = validate_settings({"planner.charge_slots": 4.5})
    assert "planner.charge_slots" in errors
    clean, _e = validate_settings({"planner.charge_slots": 4.0})
    assert clean["planner.charge_slots"] == 4
    assert isinstance(clean["planner.charge_slots"], int)


def test_validate_range_bounds():
    _c1, e1 = validate_settings({"planner.round_trip_efficiency": 0.4})  # < 0.5
    _c2, e2 = validate_settings({"planner.round_trip_efficiency": 1.5})  # > 1.0
    assert "planner.round_trip_efficiency" in e1
    assert "planner.round_trip_efficiency" in e2
    clean, e3 = validate_settings({"planner.round_trip_efficiency": 0.85})
    assert clean["planner.round_trip_efficiency"] == 0.85 and e3 == {}


def test_validate_non_dict_payload():
    clean, errors = validate_settings([1, 2, 3])
    assert clean == {} and "_" in errors


def test_ev_schedule_field_defaults_to_default_schedule_json():
    field = SETTINGS_BY_KEY["ev.schedule"]
    assert field.type == "text"
    assert field.group == "ev"
    assert json.loads(field.default) == default_schedule()
    assert json.loads(defaults()["ev.schedule"]) == default_schedule()


def test_ev_schedule_field_accepts_arbitrary_json_string():
    # The schema field is a plain string type — validation stays generic; ems.ev_schedule owns
    # the tolerant parsing/shape rules, applied when the value is actually consumed.
    payload = json.dumps({"mon": {"enabled": True, "min_pct": 90, "ready_by": "06:00"}})
    clean, errors = validate_settings({"ev.schedule": payload})
    assert errors == {}
    assert clean["ev.schedule"] == payload


def test_ev_car_id_field_defaults_to_empty_string():
    field = SETTINGS_BY_KEY["ev.car_id"]
    assert field.type == "text"
    assert field.group == "ev"
    assert field.default == ""
    assert defaults()["ev.car_id"] == ""


def test_ev_car_id_field_accepts_any_slug_string():
    # Validation stays generic here too — ems.cars.by_id owns "is this a known car" at read time,
    # so an id from a future dataset update never gets rejected by a stale settings schema.
    clean, errors = validate_settings({"ev.car_id": "tesla-model-y-long-range"})
    assert errors == {}
    assert clean["ev.car_id"] == "tesla-model-y-long-range"


def test_ev_battery_kwh_field_defaults_and_bounds():
    field = SETTINGS_BY_KEY["ev.battery_kwh"]
    assert field.type == "number"
    assert field.group == "ev"
    assert field.default == 57.5
    assert field.min == 10.0
    assert field.max == 150.0
    assert field.step == 0.5
    assert field.unit == "kWh"
    assert defaults()["ev.battery_kwh"] == 57.5


def test_ev_battery_kwh_validate_range():
    _clean, errors = validate_settings({"ev.battery_kwh": 5.0})  # below min
    assert "ev.battery_kwh" in errors
    _clean2, errors2 = validate_settings({"ev.battery_kwh": 200.0})  # above max
    assert "ev.battery_kwh" in errors2
    clean, errors3 = validate_settings({"ev.battery_kwh": 75.0})
    assert clean["ev.battery_kwh"] == 75.0 and errors3 == {}


def test_ev_charge_efficiency_field_defaults_bounds_and_advanced():
    field = SETTINGS_BY_KEY["ev.charge_efficiency"]
    assert field.type == "number"
    assert field.group == "ev"
    assert field.default == 0.90
    assert field.min == 0.7
    assert field.max == 1.0
    assert field.step == 0.01
    assert field.advanced is True
    assert defaults()["ev.charge_efficiency"] == 0.90


def test_ev_charge_efficiency_validate_range():
    _clean, errors = validate_settings({"ev.charge_efficiency": 0.5})  # below 0.7
    assert "ev.charge_efficiency" in errors
    clean, errors2 = validate_settings({"ev.charge_efficiency": 0.95})
    assert clean["ev.charge_efficiency"] == 0.95 and errors2 == {}


def test_heating_done_field_defaults_to_empty_json_object():
    # App state (which Insights heating-advice cards are marked done, and when) — not a user
    # tunable, but no `hidden` mechanism exists in SettingsField, so it lives in "reporting" with a
    # help note pointing back at where it's actually managed.
    field = SETTINGS_BY_KEY["heating.done"]
    assert field.type == "text"
    assert field.group == "reporting"
    assert field.default == "{}"
    assert json.loads(field.default) == {}
    assert "Insights" in field.help
    assert defaults()["heating.done"] == "{}"


def test_heating_done_field_accepts_arbitrary_json_string():
    # Generic "text" validation only, same as ev.schedule — HeatingAdvice.tsx owns the shape
    # ({itemKey: "YYYY-MM-DD"}), posted as ONE key, immediately, never through the save bar.
    payload = json.dumps({"balancing": "2026-07-15", "flow_temp": "2026-07-16"})
    clean, errors = validate_settings({"heating.done": payload})
    assert errors == {}
    assert clean["heating.done"] == payload
    assert json.loads(clean["heating.done"]) == {
        "balancing": "2026-07-15", "flow_temp": "2026-07-16",
    }


def test_max_buy_eur_per_kwh_bounds_and_strategy_group():
    """AC3 (#324): planner.max_buy_eur_per_kwh — bounds and visible in Strategy (not advanced)."""
    field = SETTINGS_BY_KEY["planner.max_buy_eur_per_kwh"]
    assert field.type == "number"
    assert field.group == "strategy"
    assert field.unit == "€/kWh"
    assert field.advanced is False
    assert field.min <= field.default <= field.max
    assert field.default == 0.0 and field.min == 0.0 and field.max == 1.0
    _low, err_low = validate_settings({"planner.max_buy_eur_per_kwh": -0.01})
    _high, err_high = validate_settings({"planner.max_buy_eur_per_kwh": 1.01})
    assert "planner.max_buy_eur_per_kwh" in err_low
    assert "planner.max_buy_eur_per_kwh" in err_high
    clean, errors = validate_settings({"planner.max_buy_eur_per_kwh": 0.20})
    assert errors == {} and clean["planner.max_buy_eur_per_kwh"] == 0.20
    row = next(r for r in schema_json() if r["key"] == field.key)
    assert row["advanced"] is False and row["group"] == "strategy"


def test_max_daily_grid_charge_bounds_and_slider():
    """AC4: planner.max_daily_grid_charge_kwh — min ≤ default ≤ max; min−1 and max+1 rejected."""
    field = SETTINGS_BY_KEY["planner.max_daily_grid_charge_kwh"]
    assert field.type == "number"
    assert field.group == "strategy"
    assert field.unit == "kWh"
    assert field.slider is True
    assert field.advanced is False
    assert field.min <= field.default <= field.max
    assert field.default == 12.0 and field.min == 1.0 and field.max == 40.0
    assert defaults()["planner.max_daily_grid_charge_kwh"] == 12.0
    _low, err_low = validate_settings({"planner.max_daily_grid_charge_kwh": field.min - 1})
    _high, err_high = validate_settings({"planner.max_daily_grid_charge_kwh": field.max + 1})
    assert "planner.max_daily_grid_charge_kwh" in err_low
    assert "planner.max_daily_grid_charge_kwh" in err_high
    clean, errors = validate_settings({"planner.max_daily_grid_charge_kwh": 12})
    assert errors == {} and clean["planner.max_daily_grid_charge_kwh"] == 12
    row = next(r for r in schema_json() if r["key"] == field.key)
    assert row["slider"] is True and row["advanced"] is False and row["group"] == "strategy"


def test_heating_done_field_overlays_onto_effective_settings():
    payload = json.dumps({"dhw_eco": "2026-07-01"})
    eff = effective_settings({"heating.done": payload})
    assert eff["heating.done"] == payload
    # An invalid (non-string) stored value must never break the dashboard — falls back to default.
    eff_bad = effective_settings({"heating.done": 123})
    assert eff_bad["heating.done"] == "{}"


def test_consumers_quiet_hours_and_preferred_windows_validation():
    clean, errors = validate_settings({"consumers.quiet_hours": "22:00-07:00"})
    assert errors == {}
    assert clean["consumers.quiet_hours"] == "22:00-07:00"
    clean_empty, err_empty = validate_settings({"consumers.quiet_hours": "  "})
    assert err_empty == {}
    assert clean_empty["consumers.quiet_hours"] == ""
    _c, err = validate_settings({"consumers.quiet_hours": "not-a-range"})
    assert "consumers.quiet_hours" in err
    _c, err_same = validate_settings({"consumers.quiet_hours": "22:00-22:00"})
    assert "consumers.quiet_hours" in err_same
    clean2, err2 = validate_settings({
        "consumers.preferred_windows": '{"mock-washer-1": "09:00-17:00"}',
    })
    assert err2 == {}
    assert "mock-washer-1" in clean2["consumers.preferred_windows"]
    _c, err_partial = validate_settings({
        "consumers.preferred_windows": '{"washer": "09:00-17:00", "dryer": "nope"}',
    })
    assert "consumers.preferred_windows" in err_partial
    _c, err_wrap = validate_settings({
        "consumers.preferred_windows": '{"washer": "22:00-07:00"}',
    })
    assert "consumers.preferred_windows" in err_wrap
