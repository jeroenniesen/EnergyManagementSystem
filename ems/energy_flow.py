"""Energy-distribution flows for the Sankey + Insights report (SPEC §9.1; design in
docs/superpowers/specs/2026-07-01-insights-reporting-design.md).

The meters record only NET flows per slot (grid ±import/−export, solar, battery +discharge/−charge)
plus the reconstructed home load (non-EV) and total load. We attribute each 15-min slot's energy
with a **solar-first, home-before-car** priority model — the intuition the Home Assistant Energy
dashboard uses, extended with the car as its own sink:

    solar   → home → car → battery → export
    battery (discharge) → home → car        (battery→car is the car-guard LEAK — should be ~0)
    grid    → whatever solar/battery didn't cover (home, car, battery-charge)

Summed over any window this yields the Sankey bands + node totals. It is an attribution *estimate*
(hardware can't say which electron went where) but energy-conserving per slot — sources
(solar + battery_discharge + grid_import) equal sinks (home + car + battery_charge + export) — and
honest over a window. Pure + unit-tested — the API passes in stored rows; no I/O here.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import NamedTuple

from ems.retrospect import _parse
from ems.timeseries import observed_segments

_SLOT_MIN = 15
_DH = _SLOT_MIN / 60.0  # hours per slot, for energy = power × time
_EPS = 1e-9


class SlotBands(NamedTuple):
    """One slot's energy attribution (kWh), source→sink. `batt_car` is the car-guard leak."""
    solar_home: float
    solar_car: float
    solar_batt: float
    solar_grid: float
    grid_home: float
    grid_car: float
    grid_batt: float
    batt_home: float
    batt_car: float
    batt_grid: float


@dataclass(frozen=True)
class EnergyFlows:
    """A window's energy distribution in kWh. The *_to_* fields are the Sankey bands; the rest are
    node totals + headline metrics. `has_data` is False when the window has no samples."""
    date: str  # YYYY-MM-DD (or window label) this covers
    has_data: bool
    partial: bool  # True if the window is still in progress (e.g. today)
    # Sankey bands (source → sink), kWh.
    solar_to_home: float
    solar_to_car: float
    solar_to_battery: float
    solar_to_grid: float
    grid_to_home: float
    grid_to_car: float
    grid_to_battery: float
    battery_to_home: float
    battery_to_car: float
    battery_to_grid: float
    # Node totals, kWh.
    solar_kwh: float
    grid_import_kwh: float
    grid_export_kwh: float
    battery_charge_kwh: float
    battery_discharge_kwh: float
    home_kwh: float
    car_kwh: float
    # Headline metrics.
    self_sufficiency_pct: float | None        # share of total load NOT bought from the grid
    solar_self_consumption_pct: float | None  # share of solar produced used on-site (not exported)
    car_guard_leak_kwh: float                 # battery energy that fed the car (should be ~0)

    def to_dict(self) -> dict:
        return asdict(self)


def _allocate_slot(
    solar_w: float, grid_w: float, battery_w: float, home_w: float, car_w: float = 0.0,
    *, duration_hours: float = _DH,
) -> SlotBands:
    """Attribute ONE slot's energy (kWh) solar-first, home-before-car. `home_w` is the non-EV house
    load; `car_w` is the EV load. `grid_w` (± net) is not read directly — the grid is whatever solar
    and the battery did not cover, which is energy-equivalent."""
    solar = max(0.0, solar_w) * duration_hours / 1000.0
    charge = max(0.0, -battery_w) * duration_hours / 1000.0  # energy INTO the battery
    discharge = max(0.0, battery_w) * duration_hours / 1000.0  # energy OUT of the battery
    home = max(0.0, home_w) * duration_hours / 1000.0
    car = max(0.0, car_w) * duration_hours / 1000.0

    # Solar: home → car → battery → export.
    solar_home = min(solar, home)
    rem_solar = solar - solar_home
    rem_home = home - solar_home
    solar_car = min(rem_solar, car)
    rem_solar -= solar_car
    rem_car = car - solar_car
    solar_batt = min(rem_solar, charge)
    rem_solar -= solar_batt
    solar_grid = rem_solar  # solar left over is exported
    rem_charge = charge - solar_batt  # battery charge solar didn't cover → from the grid

    # Battery discharge: home first, then car (the leak), then grid (leftover export, rare).
    batt_home = min(discharge, rem_home)
    rem_disch = discharge - batt_home
    rem_home -= batt_home
    batt_car = min(rem_disch, rem_car)  # >0 ⇒ battery fed the car ⇒ car-guard leak
    rem_disch -= batt_car
    rem_car -= batt_car
    batt_grid = rem_disch

    # The grid covers whatever is still left.
    grid_home = rem_home
    grid_car = rem_car
    grid_batt = rem_charge
    return SlotBands(solar_home, solar_car, solar_batt, solar_grid,
                     grid_home, grid_car, grid_batt, batt_home, batt_car, batt_grid)


def build_flows(
    raw_rows: list[dict],
    derived_rows: list[dict],
    start: datetime,
    end: datetime,
    *,
    label: str,
    partial: bool,
    sample_interval_seconds: float = 900.0,
    max_hold_seconds: float | None = None,
) -> EnergyFlows:
    """Resample recorded rows into 15-min slots within [start, end) and sum the solar-first
    allocation into a window's flows. Home load = derived `non_ev_load_w`; car load =
    `house_load_w − non_ev_load_w` (0 with no EV / old rows). Zero-order hold per slot."""
    start_utc, end_utc = start.astimezone(UTC), end.astimezone(UTC)

    normalized_raw = [
        {**r, "grid_power_w": r.get("grid_power_w", 0.0),
         "solar_power_w": r.get("solar_power_w", 0.0),
         "battery_power_w": r.get("battery_power_w", 0.0),
         "ev_power_w": r.get("ev_power_w", 0.0)}
        for r in raw_rows
    ]
    raw_segments = observed_segments(
        normalized_raw, start=start_utc, end=end_utc,
        fields=("grid_power_w", "solar_power_w", "battery_power_w", "ev_power_w"),
        nominal_interval_seconds=sample_interval_seconds,
        max_hold_seconds=max_hold_seconds,
    )

    derived_by: dict[datetime, tuple[float, float, float]] = {}
    for r in derived_rows:
        dt = _parse(r.get("ts"))
        if dt is None or dt < start_utc or dt >= end_utc:
            continue
        total = r.get("house_load_w")
        if total is None:
            continue
        total = float(total)
        non_ev = r.get("non_ev_load_w")
        home = float(non_ev) if non_ev is not None else total
        derived_by[dt] = (total, home, max(0.0, total - home))

    tot = [0.0] * 10  # the ten SlotBands, accumulated
    for segment in raw_segments:
        values = segment.values
        measured_total = max(
            0.0,
            values["grid_power_w"] + values["solar_power_w"] + values["battery_power_w"],
        )
        derived = derived_by.get(segment.observed_at)
        tolerance_w = max(100.0, measured_total * 0.05)
        if derived is None or abs(derived[0] - measured_total) > tolerance_w:
            car = min(measured_total, max(0.0, values["ev_power_w"]))
            loads = (measured_total - car, car)
        else:
            loads = (derived[1], derived[2])
        bands = _allocate_slot(
            values["solar_power_w"], values["grid_power_w"], values["battery_power_w"],
            loads[0], loads[1], duration_hours=segment.duration_seconds / 3600.0,
        )
        tot = [acc + band for acc, band in zip(tot, bands, strict=True)]
    (s_home, s_car, s_batt, s_grid, g_home, g_car, g_batt, b_home, b_car, b_grid) = tot

    home = s_home + b_home + g_home
    car = s_car + b_car + g_car
    load = home + car
    served_self = s_home + s_car + b_home + b_car  # load met from own solar + battery
    ss = round(min(100.0, served_self / load * 100.0), 1) if load > _EPS else None
    solar_kwh = s_home + s_car + s_batt + s_grid
    solar_used = s_home + s_car + s_batt  # solar used on-site (not exported)
    ssc = round(min(100.0, solar_used / solar_kwh * 100.0), 1) if solar_kwh > _EPS else None

    def r2(x: float) -> float:
        return round(x, 2) + 0.0  # +0.0 collapses -0.0

    return EnergyFlows(
        date=label,
        has_data=bool(raw_segments),
        partial=partial,
        solar_to_home=r2(s_home), solar_to_car=r2(s_car), solar_to_battery=r2(s_batt),
        solar_to_grid=r2(s_grid),
        grid_to_home=r2(g_home), grid_to_car=r2(g_car), grid_to_battery=r2(g_batt),
        battery_to_home=r2(b_home), battery_to_car=r2(b_car), battery_to_grid=r2(b_grid),
        solar_kwh=r2(solar_kwh),
        grid_import_kwh=r2(g_home + g_car + g_batt),
        grid_export_kwh=r2(s_grid + b_grid),
        battery_charge_kwh=r2(s_batt + g_batt),
        battery_discharge_kwh=r2(b_home + b_car + b_grid),
        home_kwh=r2(home), car_kwh=r2(car),
        self_sufficiency_pct=ss, solar_self_consumption_pct=ssc,
        car_guard_leak_kwh=r2(b_car),
    )


def build_daily_flows(
    raw_rows: list[dict],
    derived_rows: list[dict],
    day_start: datetime,
    day_end: datetime,
    *,
    label: str,
    partial: bool,
    sample_interval_seconds: float = 900.0,
    max_hold_seconds: float | None = None,
) -> EnergyFlows:
    """Backward-compatible single-day wrapper around build_flows (for /api/energy-distribution)."""
    return build_flows(
        raw_rows, derived_rows, day_start, day_end, label=label, partial=partial,
        sample_interval_seconds=sample_interval_seconds, max_hold_seconds=max_hold_seconds,
    )
