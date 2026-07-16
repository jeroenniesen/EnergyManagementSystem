"""The three Insights scores — pure functions, 0..100 where 100 = best, each self-explaining.

Design: docs/superpowers/specs/2026-07-01-insights-reporting-design.md (Part 1 — the three scores).
No I/O: the reporting layer hands in an EnergyFlows window, the per-slot (import_kWh, price) pairs,
and the carbon/gas factors; these functions return Score values the API serialises directly. Every
score carries a human-readable `explanation` (the "why", including what the system did) —
explainability is a core product principle, not decoration.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, dataclass

from ems.energy_flow import EnergyFlows

# Default emission factors (NL). grid: ~0.27 kg CO₂/kWh (2025 grid-mix, editable in settings); gas:
# ~1.78 kg CO₂/m³ (combustion). The reporting layer passes the effective values from the settings.
DEFAULT_GRID_CO2 = 0.27
DEFAULT_GAS_CO2 = 1.78


@dataclass(frozen=True)
class Score:
    """One Insights tile. `value` is 0..100 (100 = best) or None when not computable; `raw` is the
    underlying figure (e.g. % kept, kg CO₂, €/kWh) shown beneath the score."""
    key: str        # "self_consumption" | "co2" | "best_price"
    label: str
    value: float | None
    raw: float | None
    unit: str       # unit of `raw`
    explanation: str

    def to_dict(self) -> dict:
        return asdict(self)


def _clamp(pct: float) -> float:
    return round(max(0.0, min(100.0, pct)), 1)


def _self_consumption_score(
    ssc: float | None, ss: float | None, grid_export_kwh: float
) -> Score:
    """Shared core behind both `self_consumption_score` (per-slot flows) and
    `self_consumption_score_from_totals` (aggregate rollup) — the ONE place the tile's copy/rules
    live, so the two entry points can never drift. `ssc` (solar self-consumption %) is preferred;
    it falls back to `ss` (self-sufficiency %) over a window with no solar."""
    if ssc is not None:
        exported = grid_export_kwh
        expl = f"Kept {ssc:.0f}% of your solar on-site"
        expl += (f"; exported {exported:.1f} kWh you couldn't use or store."
                 if exported > 0.05 else " — nothing wasted to the grid.")
        return Score("self_consumption", "Self-consumption", _clamp(ssc), round(ssc, 1), "%", expl)
    if ss is not None:
        return Score("self_consumption", "Self-consumption", _clamp(ss), round(ss, 1), "%",
                     f"No solar this period — you ran {ss:.0f}% on your own battery/solar.")
    return Score("self_consumption", "Self-consumption", None, None, "%", "No energy recorded yet.")


def self_consumption_score(flows: EnergyFlows) -> Score:
    """Share of produced solar used on-site (100 = exported nothing). Falls back to self-sufficiency
    over a window with no solar (winter/night), so the tile is always meaningful."""
    return _self_consumption_score(
        flows.solar_self_consumption_pct, flows.self_sufficiency_pct, flows.grid_export_kwh)


def self_consumption_score_from_totals(
    *, solar_kwh: float, load_kwh: float, grid_import_kwh: float, grid_export_kwh: float
) -> Score:
    """The self-consumption tile from AGGREGATE kWh totals (F2 — the year view scores the never-
    purged daily_energy rollup rather than the partially-retained raw window). No per-slot Sankey is
    available, so the two headline shares are derived from totals (documented approximations,
    energy-conserving over a long window): solar self-consumption ≈ (solar − export) / solar
    treating all export as solar-origin (battery→grid is negligible); self-sufficiency ≈
    (load − import) / load treating imports as load-serving (battery round-trips net out over a
    year). Both mirror `energy_flow.build_flows`' clamp/round exactly, then reuse the SAME scoring
    core so the copy and thresholds are identical to the per-slot path."""
    ssc = (round(min(100.0, max(0.0, solar_kwh - grid_export_kwh) / solar_kwh * 100.0), 1)
           if solar_kwh > 1e-9 else None)
    ss = (round(min(100.0, max(0.0, load_kwh - grid_import_kwh) / load_kwh * 100.0), 1)
          if load_kwh > 1e-9 else None)
    return _self_consumption_score(ssc, ss, grid_export_kwh)


def _co2_score(
    elec_load_kwh: float, grid_import_kwh: float, grid_factor: float, gas_factor: float,
    gas_m3: float,
) -> Score:
    """Shared core behind both `co2_score` (flows) and `co2_score_from_totals` (aggregate) — the ONE
    place the CO₂ math + copy live so the two entry points can't diverge."""
    gas_kg = gas_m3 * gas_factor
    your_kg = grid_import_kwh * grid_factor + gas_kg
    baseline_kg = elec_load_kwh * grid_factor + gas_kg
    if baseline_kg <= 1e-9:
        return Score("co2", "CO₂", None, None, "kg", "No energy recorded yet.")
    avoided = _clamp((baseline_kg - your_kg) / baseline_kg * 100.0)
    expl = (f"Avoided {avoided:.0f}% of a no-solar home's CO₂ "
            f"({your_kg:.0f} kg vs {baseline_kg:.0f} kg).")
    if gas_m3 > 0.0 and your_kg > 1e-9:
        gas_pct = gas_kg / your_kg * 100
        expl += f" Gas heating is {gas_pct:.0f}% of your footprint — the biggest cut left."
    return Score("co2", "CO₂", avoided, round(your_kg, 1), "kg", expl)


def co2_score(
    flows: EnergyFlows, *, grid_factor: float = DEFAULT_GRID_CO2,
    gas_factor: float = DEFAULT_GAS_CO2, gas_m3: float = 0.0,
) -> Score:
    """% of CO₂ avoided vs. a reference home with no solar/battery/EMS (which imports its whole load
    at grid intensity and burns the same gas). 100 = zero footprint vs. that home. `raw` is your
    actual footprint in kg (electricity + gas). Gas sits in the denominator without any avoided —
    intended: it surfaces heating as the biggest cut left and steps the score down when added."""
    return _co2_score(flows.home_kwh + flows.car_kwh, flows.grid_import_kwh,
                      grid_factor, gas_factor, gas_m3)


def co2_score_from_totals(
    *, home_kwh: float, car_kwh: float, grid_import_kwh: float,
    grid_factor: float = DEFAULT_GRID_CO2, gas_factor: float = DEFAULT_GAS_CO2,
    gas_m3: float = 0.0,
) -> Score:
    """The CO₂ tile from AGGREGATE kWh totals (F2 — full-year daily_energy rollup). Same math as
    `co2_score` via the shared core: import×grid_factor vs (home+car)×grid_factor. Gas has no
    full-year rollup (gas_readings purge with the raw window), so the year score is electricity-only
    (`gas_m3=0` at the call site) rather than mixing a full-year electricity figure with 90 days of
    gas."""
    return _co2_score(home_kwh + car_kwh, grid_import_kwh, grid_factor, gas_factor, gas_m3)


def best_price_score(import_slots: Sequence[tuple[float, float | None]]) -> Score:
    """How well grid imports were timed against the period's price curve. `import_slots` is a list
    of (import_kWh, €/kWh) per slot. 100 = imported entirely at the cheapest price, 0 = at the
    priciest. `raw` is your import volume-weighted average price."""
    priced = [(kwh, p) for kwh, p in import_slots if p is not None]
    if not priced:
        return Score("best_price", "Best price", None, None, "€/kWh", "No price data this period.")
    prices = [p for _, p in priced]
    p_min, p_max = min(prices), max(prices)
    imported = sum(kwh for kwh, _ in priced)
    if imported <= 1e-9:
        return Score("best_price", "Best price", 100.0, None, "€/kWh",
                     "You didn't need to import from the grid.")
    vwap = sum(kwh * p for kwh, p in priced) / imported
    if p_max - p_min < 1e-9:
        return Score("best_price", "Best price", 100.0, round(vwap, 4), "€/kWh",
                     f"Prices were flat at €{vwap:.2f}/kWh — nothing to time.")
    value = _clamp((p_max - vwap) / (p_max - p_min) * 100.0)
    avg = sum(prices) / len(prices)
    saved = max(0.0, (avg - vwap) * imported)
    expl = f"Imported at €{vwap:.2f}/kWh vs the period's €{p_min:.2f}–€{p_max:.2f} range"
    expl += (f"; ≈ €{saved:.2f} saved vs buying at the average." if saved > 0.005 else ".")
    return Score("best_price", "Best price", value, round(vwap, 4), "€/kWh", expl)
