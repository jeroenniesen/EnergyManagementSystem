"""Tests for GET /api/cars (ems/web/api.py) — static, cacheable, read-only car-picker data that
wires ems/cars.py (CARS, brands, to_dict) into the API for the Settings "Car" group. No settings
or store dependency: the dataset is static, so a bare MockSource app is enough."""
from __future__ import annotations

from fastapi.testclient import TestClient

from ems.cars import CARS, brands
from ems.sources.mock import MockSource
from ems.web.api import create_app


def _client() -> TestClient:
    return TestClient(create_app(MockSource(), dry_run=True, dev_mode="mock"))


def test_cars_endpoint_shape():
    r = _client().get("/api/cars")
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"brands", "cars"}
    assert isinstance(body["brands"], list)
    assert isinstance(body["cars"], list)


def test_cars_endpoint_has_at_least_45_cars():
    body = _client().get("/api/cars").json()
    assert len(body["cars"]) >= 45
    assert len(body["cars"]) == len(CARS)


def test_cars_endpoint_brands_sorted_and_matches_dataset():
    body = _client().get("/api/cars").json()
    assert body["brands"] == sorted(body["brands"])
    assert body["brands"] == brands()


def test_cars_endpoint_car_dict_shape():
    body = _client().get("/api/cars").json()
    car = body["cars"][0]
    assert set(car) == {"id", "brand", "model", "battery_net_kwh", "max_ac_kw", "years"}


def test_cars_endpoint_matches_source_dataset():
    body = _client().get("/api/cars").json()
    ids = {c["id"] for c in body["cars"]}
    assert ids == {c.id for c in CARS}
