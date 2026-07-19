"""Energy model: reconstruct house load from raw meters (SPEC §4). P1 is NET GRID, not load."""
from __future__ import annotations

import dataclasses
import math
from dataclasses import dataclass

from .domain import RawSample

# Generous per-channel ceilings for the plausibility guard below: chosen so a LEGIT reading is
# never clipped (battery inverter maxes ~4-5 kW here, solar is ~3 kWp) — only gross garbage from
# a future sensor/comms glitch. Grid and EV are NOT clamped here; both can legitimately be large
# on 3-phase.
MAX_BATTERY_W = 20000.0
MAX_SOLAR_W = 20000.0
MAX_LEARNABLE_LOAD_W = 50000.0
NEGATIVE_NOISE_TOLERANCE_W = 50.0


@dataclass(frozen=True)
class DerivedSample:
    house_load_w: float  # total house demand (SPEC §4.2)
    non_ev_load_w: float  # house load excluding EV charging (what the planner learns)


@dataclass(frozen=True)
class ReconstructionAssessment:
    derived: DerivedSample
    valid_for_learning: bool
    flags: tuple[str, ...]


def reconstruct(raw: RawSample, ev_charging_threshold_w: float = 200.0) -> DerivedSample:
    """house_load = grid + solar + battery; subtract EV only while it is charging (§4.5)."""
    house_load = raw.grid_power_w + raw.solar_power_w + raw.battery_power_w
    ev = raw.ev_power_w if raw.ev_power_w > ev_charging_threshold_w else 0.0
    return DerivedSample(house_load_w=house_load, non_ev_load_w=house_load - ev)


def assess_reconstruction(
    raw: RawSample,
    ev_charging_threshold_w: float = 200.0,
    *,
    negative_noise_tolerance_w: float = NEGATIVE_NOISE_TOLERANCE_W,
    max_learnable_load_w: float = MAX_LEARNABLE_LOAD_W,
) -> ReconstructionAssessment:
    """Classify reconstructed load without mutating its raw evidence.

    Small negative meter noise is safe to clamp to zero. Material negative, non-finite, or
    implausibly large loads stay visible in raw/derived history but are quarantined from learning.
    """
    derived = reconstruct(raw, ev_charging_threshold_w)
    flags: list[str] = []
    house, non_ev = derived.house_load_w, derived.non_ev_load_w
    if not math.isfinite(house) or not math.isfinite(non_ev):
        return ReconstructionAssessment(derived, False, ("non_finite_load",))
    if house < -negative_noise_tolerance_w:
        flags.append("negative_house_load")
    if non_ev < -negative_noise_tolerance_w:
        flags.append("negative_non_ev_load")
    if house > max_learnable_load_w or non_ev > max_learnable_load_w:
        flags.append("implausible_load")
    if flags:
        return ReconstructionAssessment(derived, False, tuple(flags))
    if house < 0.0 or non_ev < 0.0:
        derived = DerivedSample(max(0.0, house), max(0.0, non_ev))
        flags.append("clamped_noise")
    return ReconstructionAssessment(derived, True, tuple(flags))


def normalise_solar(raw_solar_w: float) -> float:
    """Production is >= 0; clamp negatives to 0 rather than taking magnitude (§4.7)."""
    return max(0.0, raw_solar_w)


def sanitize_sample(raw: RawSample) -> tuple[RawSample, tuple[str, ...]]:
    """Defensive plausibility guard at ingestion (defense-in-depth against a future sensor/comms
    glitch producing a wildly out-of-range value): clamp battery/solar to the generous ceilings
    above. Returns the (possibly corrected) sample plus the names of any channels that were
    clamped; if nothing exceeded, returns `raw` UNCHANGED and an empty tuple."""
    clamped: list[str] = []

    battery = raw.battery_power_w
    if battery > MAX_BATTERY_W:
        battery = MAX_BATTERY_W
        clamped.append("battery")
    elif battery < -MAX_BATTERY_W:
        battery = -MAX_BATTERY_W
        clamped.append("battery")

    solar = raw.solar_power_w
    if solar > MAX_SOLAR_W:
        solar = MAX_SOLAR_W
        clamped.append("solar")
    elif solar < 0.0:
        solar = 0.0
        clamped.append("solar")

    if not clamped:
        return raw, ()
    return dataclasses.replace(raw, battery_power_w=battery, solar_power_w=solar), tuple(clamped)


def is_soc_jump_implausible(
    prev_soc: float | None,
    new_soc: float,
    minutes_elapsed: float,
    max_jump_pct_per_5min: float = 20.0,
) -> bool:
    """Reject SoC jumps larger than the configured rate (SPEC §4.7)."""
    if prev_soc is None:
        return False
    allowed = max_jump_pct_per_5min * (minutes_elapsed / 5.0)
    return abs(new_soc - prev_soc) > allowed
