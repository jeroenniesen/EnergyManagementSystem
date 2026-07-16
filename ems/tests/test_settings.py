import json

from ems.ev_schedule import default_schedule
from ems.settings import (
    SETTINGS_BY_KEY,
    defaults,
    effective_settings,
    schema_json,
    validate_settings,
)


def test_defaults_cover_every_field():
    d = defaults()
    assert set(d) == set(SETTINGS_BY_KEY)
    assert d["planner.round_trip_efficiency"] == 0.90
    assert d["ui.theme"] == "auto"
    assert d["control.allow_export_discharge"] is False


def test_anti_flap_control_knob_defaults():
    # The two guardrail-starvation knobs (07-12 incident): intent persistence + commitment reserve.
    d = defaults()
    assert d["control.intent_persistence_cycles"] == 2  # observe once, then act
    assert d["control.commitment_reserve"] == 3         # switches held for committed grid-charge
    # Both are int fields with sensible bounds (1 = legacy persistence; 0 = no reserve).
    assert SETTINGS_BY_KEY["control.intent_persistence_cycles"].type == "int"
    assert SETTINGS_BY_KEY["control.intent_persistence_cycles"].min == 1
    assert SETTINGS_BY_KEY["control.commitment_reserve"].type == "int"
    assert SETTINGS_BY_KEY["control.commitment_reserve"].min == 0


def test_schema_json_shape():
    rows = schema_json()
    assert {r["key"] for r in rows} == set(SETTINGS_BY_KEY)
    theme = next(r for r in rows if r["key"] == "ui.theme")
    assert theme["type"] == "enum"
    assert theme["options"] == ["auto", "dark", "light"]


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


def test_heating_done_field_overlays_onto_effective_settings():
    payload = json.dumps({"dhw_eco": "2026-07-01"})
    eff = effective_settings({"heating.done": payload})
    assert eff["heating.done"] == payload
    # An invalid (non-string) stored value must never break the dashboard — falls back to default.
    eff_bad = effective_settings({"heating.done": 123})
    assert eff_bad["heating.done"] == "{}"
