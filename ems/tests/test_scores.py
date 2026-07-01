"""The three Insights scores — pure, 0..100 (100 = best), each self-explaining. Canned flows."""
from ems.energy_flow import EnergyFlows
from ems.scores import best_price_score, co2_score, self_consumption_score

_BASE = dict(
    date="2026-07-01", has_data=True, partial=False,
    solar_to_home=0.0, solar_to_car=0.0, solar_to_battery=0.0, solar_to_grid=0.0,
    grid_to_home=0.0, grid_to_car=0.0, grid_to_battery=0.0,
    battery_to_home=0.0, battery_to_car=0.0, battery_to_grid=0.0,
    solar_kwh=0.0, grid_import_kwh=0.0, grid_export_kwh=0.0,
    battery_charge_kwh=0.0, battery_discharge_kwh=0.0, home_kwh=0.0, car_kwh=0.0,
    self_sufficiency_pct=None, solar_self_consumption_pct=None, car_guard_leak_kwh=0.0,
)


def _flows(**kw) -> EnergyFlows:
    return EnergyFlows(**{**_BASE, **kw})


# --- self-consumption ---

def test_self_consumption_uses_solar_share_and_explains():
    s = self_consumption_score(_flows(solar_self_consumption_pct=91.0, grid_export_kwh=3.2))
    assert s.value == 91.0 and s.unit == "%"
    assert "Kept 91%" in s.explanation and "3.2 kWh" in s.explanation


def test_self_consumption_falls_back_to_self_sufficiency_without_solar():
    s = self_consumption_score(_flows(solar_self_consumption_pct=None, self_sufficiency_pct=78.0))
    assert s.value == 78.0 and "No solar" in s.explanation


def test_self_consumption_none_without_any_data():
    assert self_consumption_score(_flows()).value is None


# --- CO₂ ---

def test_co2_percent_avoided_vs_reference():
    # Load 15 kWh, imported 6 kWh at 0.27 kg/kWh → your 1.62 kg vs baseline 4.05 kg → 60% avoided.
    s = co2_score(_flows(home_kwh=10.0, car_kwh=5.0, grid_import_kwh=6.0), grid_factor=0.27)
    assert s.value == 60.0 and s.raw == 1.6 and s.unit == "kg"
    assert "Avoided 60%" in s.explanation


def test_co2_steps_down_and_flags_gas_when_gas_included():
    # Same electricity, plus 50 m³ gas (89 kg) — the score collapses; gas is flagged as the big cut.
    s = co2_score(_flows(home_kwh=10.0, car_kwh=5.0, grid_import_kwh=6.0),
                  grid_factor=0.27, gas_factor=1.78, gas_m3=50.0)
    assert s.value == 2.6 and s.raw == 90.6
    assert "Gas heating is 98%" in s.explanation


def test_co2_none_without_load():
    assert co2_score(_flows()).value is None


def test_co2_clamps_to_zero_when_worse_than_reference():
    # Heavy grid-charging (arbitrage) can import MORE than the load — % avoided goes negative and
    # must clamp to 0 (never negative), not distort the tile.
    s = co2_score(_flows(home_kwh=5.0, car_kwh=0.0, grid_import_kwh=10.0), grid_factor=0.27)
    assert s.value == 0.0


# --- best price ---

def test_best_price_ignores_unpriced_slots():
    # Slots with no price are skipped; the score uses only the priced imports.
    s = best_price_score([(2.0, 0.10), (1.0, None), (1.0, 0.30)])
    assert s.value == 66.7 and s.raw == round((2 * 0.10 + 1 * 0.30) / 3, 4)


# --- best price ---

def test_best_price_maps_vwap_onto_the_price_range():
    # Imported 3 kWh @ €0.08 and 1 kWh @ €0.30 → VWAP €0.135 in a €0.08–€0.30 range → 75/100.
    s = best_price_score([(3.0, 0.08), (1.0, 0.30)])
    assert s.value == 75.0 and s.raw == 0.135 and s.unit == "€/kWh"
    assert "€0.08" in s.explanation and "saved" in s.explanation


def test_best_price_100_when_no_import_needed():
    s = best_price_score([(0.0, 0.10), (0.0, 0.25)])
    assert s.value == 100.0 and "didn't need to import" in s.explanation


def test_best_price_100_when_prices_flat():
    s = best_price_score([(1.0, 0.20), (2.0, 0.20)])
    assert s.value == 100.0 and "flat" in s.explanation


def test_best_price_none_without_prices():
    assert best_price_score([(1.0, None), (2.0, None)]).value is None
