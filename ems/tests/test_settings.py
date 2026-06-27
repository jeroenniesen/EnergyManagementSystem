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
