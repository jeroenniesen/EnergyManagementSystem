"""Dataset invariants for the static car database (ems/cars.py). No wiring to
settings/api/frontend here — that's a later iteration; this only guards the data module itself."""
from __future__ import annotations

import re

from ems.cars import CARS, CarModel, brands, by_id, models_for, to_dict

_SLUG_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


def test_dataset_has_at_least_45_entries() -> None:
    assert len(CARS) >= 45


def test_all_ids_unique() -> None:
    ids = [c.id for c in CARS]
    assert len(ids) == len(set(ids))


def test_all_ids_are_slug_format() -> None:
    for c in CARS:
        assert _SLUG_RE.match(c.id), f"{c.id!r} is not a valid slug"


def test_battery_net_kwh_within_plausible_range() -> None:
    for c in CARS:
        assert 15 <= c.battery_net_kwh <= 130, f"{c.id}: {c.battery_net_kwh}"


def test_max_ac_kw_within_plausible_range() -> None:
    for c in CARS:
        assert 3.0 <= c.max_ac_kw <= 22.0, f"{c.id}: {c.max_ac_kw}"


def test_every_brand_non_empty() -> None:
    for c in CARS:
        assert c.brand.strip() != ""
        assert c.model.strip() != ""
        assert c.years.strip() != ""


def test_brands_sorted_unique() -> None:
    b = brands()
    assert b == sorted(set(b))
    assert len(b) == len(set(b))
    assert len(b) > 1


def test_brands_matches_dataset() -> None:
    assert set(brands()) == {c.brand for c in CARS}


def test_models_for_returns_only_that_brand() -> None:
    tesla = models_for("Tesla")
    assert len(tesla) > 0
    assert all(c.brand == "Tesla" for c in tesla)


def test_models_for_unknown_brand_returns_empty() -> None:
    assert models_for("NotARealBrandXYZ") == []


def test_by_id_returns_matching_car() -> None:
    car = CARS[0]
    found = by_id(car.id)
    assert found == car


def test_by_id_unknown_returns_none() -> None:
    assert by_id("not-a-real-car-id") is None


def test_tesla_model_y_long_range_present() -> None:
    candidates = [
        c for c in CARS
        if c.brand == "Tesla" and "Model Y" in c.model and "Long Range" in c.model
    ]
    assert len(candidates) == 1, candidates
    car = candidates[0]
    assert 70 <= car.battery_net_kwh <= 80
    assert car.max_ac_kw == 11


def test_tesla_model_y_rwd_present() -> None:
    candidates = [
        c for c in CARS
        if c.brand == "Tesla" and "Model Y" in c.model and "RWD" in c.model
    ]
    assert len(candidates) == 1, candidates
    car = candidates[0]
    assert 50 <= car.battery_net_kwh <= 65
    assert car.max_ac_kw == 11


def test_renault_zoe_has_22kw_ac_outlier() -> None:
    candidates = [c for c in CARS if c.brand == "Renault" and "Zoe" in c.model]
    assert len(candidates) >= 1, candidates
    assert any(c.max_ac_kw == 22 for c in candidates)


def test_to_dict_roundtrips_fields() -> None:
    car = CARS[0]
    d = to_dict(car)
    assert d == {
        "id": car.id,
        "brand": car.brand,
        "model": car.model,
        "battery_net_kwh": car.battery_net_kwh,
        "max_ac_kw": car.max_ac_kw,
        "years": car.years,
    }


def test_carmodel_is_frozen() -> None:
    car = CARS[0]
    try:
        car.brand = "Nope"  # type: ignore[misc]
    except AttributeError:
        pass
    else:
        raise AssertionError("CarModel should be frozen (immutable)")


def test_carmodel_dataclass_fields() -> None:
    car = CarModel(
        id="test-car-2024",
        brand="Test",
        model="Car",
        battery_net_kwh=50.0,
        max_ac_kw=11.0,
        years="2024–present",
    )
    assert car.id == "test-car-2024"
    assert car.battery_net_kwh == 50.0
