"""Daily energy-distribution flows for the Sankey view (SPEC §9.1 companion to retrospect.py).

The meters record only NET flows per slot (grid +import/−export, solar, battery +discharge/−charge,
reconstructed house load). To draw "where the day's energy came from and went" we attribute each
15-min slot's energy with a **solar-first priority** model — the same intuition the Home Assistant
Energy dashboard uses:

    solar  → home first, then into the battery, then exported to the grid
    battery (discharge) → home
    grid (import) → whatever home + battery charge solar didn't cover

Summed over a calendar day this yields the six Sankey bands. It's an attribution *estimate* (the
hardware can't tell you which electron went where), but it's physically consistent per slot and
honest over a day. Pure + unit-tested — the API passes in stored rows; no I/O here.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import UTC, datetime

from ems.retrospect import _floor, _mean, _parse

_SLOT_MIN = 15
_DH = _SLOT_MIN / 60.0  # hours per slot, for energy = power × time
_EPS = 1e-9


@dataclass(frozen=True)
class EnergyFlows:
    """A day's energy distribution in kWh. The six *_to_* fields are the Sankey bands; the rest are
    node totals + a self-sufficiency headline. `has_data` is False when the day has no samples."""
    date: str  # YYYY-MM-DD (local calendar day this covers)
    has_data: bool
    partial: bool  # True if the day is today (still in progress)
    # Sankey bands (source → sink), kWh.
    solar_to_home: float
    solar_to_battery: float
    solar_to_grid: float
    grid_to_home: float
    grid_to_battery: float
    battery_to_home: float
    # Node totals, kWh.
    solar_kwh: float
    grid_import_kwh: float
    grid_export_kwh: float
    battery_charge_kwh: float
    battery_discharge_kwh: float
    home_kwh: float
    # Headline: share of the home served WITHOUT buying from the grid (solar + battery).
    self_sufficiency_pct: float | None

    def to_dict(self) -> dict:
        return asdict(self)


def _allocate_slot(
    solar_w: float, grid_w: float, battery_w: float, load_w: float
) -> tuple[float, float, float, float, float, float]:
    """Attribute ONE slot's energy (kWh) solar-first. Returns
    (solar_home, solar_batt, solar_grid, grid_home, grid_batt, batt_home)."""
    solar = max(0.0, solar_w) * _DH / 1000.0
    charge = max(0.0, -battery_w) * _DH / 1000.0  # energy INTO the battery
    discharge = max(0.0, battery_w) * _DH / 1000.0  # energy OUT of the battery
    load = max(0.0, load_w) * _DH / 1000.0

    # Solar serves the home first, then tops up the battery, then exports the remainder.
    solar_home = min(solar, load)
    rem_solar = solar - solar_home
    rem_home = load - solar_home
    solar_batt = min(rem_solar, charge)
    rem_solar -= solar_batt
    solar_grid = rem_solar  # whatever solar is left is exported
    rem_charge = charge - solar_batt  # battery charge solar didn't cover → came from the grid

    # The battery's own output covers the rest of the home; the grid covers what's still left
    # plus any grid-fed charging.
    batt_home = min(discharge, rem_home)
    grid_home = rem_home - batt_home
    grid_batt = rem_charge
    return solar_home, solar_batt, solar_grid, grid_home, grid_batt, batt_home


def build_daily_flows(
    raw_rows: list[dict],
    derived_rows: list[dict],
    day_start: datetime,
    day_end: datetime,
    *,
    label: str,
    partial: bool,
) -> EnergyFlows:
    """Resample recorded rows into 15-min slots within [day_start, day_end) and sum the solar-first
    allocation into a day's flows. `day_start`/`day_end` bound the local day; `label` is the local
    YYYY-MM-DD it represents. Zero-order hold per slot (mean power × slot length)."""
    start_utc, end_utc = day_start.astimezone(UTC), day_end.astimezone(UTC)

    raw_by: dict[datetime, dict[str, list[float]]] = defaultdict(
        lambda: {"grid": [], "solar": [], "batt": []}
    )
    for r in raw_rows:
        dt = _parse(r.get("ts"))
        if dt is None or dt < start_utc or dt >= end_utc:
            continue
        slot = _floor(dt)
        raw_by[slot]["grid"].append(float(r.get("grid_power_w", 0.0)))
        raw_by[slot]["solar"].append(float(r.get("solar_power_w", 0.0)))
        raw_by[slot]["batt"].append(float(r.get("battery_power_w", 0.0)))

    load_by: dict[datetime, list[float]] = defaultdict(list)
    for r in derived_rows:
        dt = _parse(r.get("ts"))
        if dt is None or dt < start_utc or dt >= end_utc:
            continue
        if r.get("house_load_w") is not None:
            load_by[_floor(dt)].append(float(r["house_load_w"]))

    tot = [0.0] * 6  # solar_home, solar_batt, solar_grid, grid_home, grid_batt, batt_home
    for slot in sorted(raw_by):
        b = raw_by[slot]
        bands = _allocate_slot(
            _mean(b["solar"]), _mean(b["grid"]), _mean(b["batt"]),
            _mean(load_by[slot]) if load_by.get(slot) else 0.0,
        )
        tot = [acc + band for acc, band in zip(tot, bands, strict=True)]
    s_home, s_batt, s_grid, g_home, g_batt, b_home = tot

    home = s_home + b_home + g_home
    served_self = s_home + b_home  # home energy NOT bought from the grid
    ss = round(min(100.0, served_self / home * 100.0), 1) if home > _EPS else None

    def r2(x: float) -> float:
        return round(x, 2) + 0.0  # +0.0 collapses -0.0

    return EnergyFlows(
        date=label,
        has_data=bool(raw_by),
        partial=partial,
        solar_to_home=r2(s_home), solar_to_battery=r2(s_batt), solar_to_grid=r2(s_grid),
        grid_to_home=r2(g_home), grid_to_battery=r2(g_batt), battery_to_home=r2(b_home),
        solar_kwh=r2(s_home + s_batt + s_grid),
        grid_import_kwh=r2(g_home + g_batt),
        grid_export_kwh=r2(s_grid),
        battery_charge_kwh=r2(s_batt + g_batt),
        battery_discharge_kwh=r2(b_home),
        home_kwh=r2(home),
        self_sufficiency_pct=ss,
    )
