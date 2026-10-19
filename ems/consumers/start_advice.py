"""Advisory-only cheapest contiguous start window for appliances (#227 / epic #222, #333).

Pure — no clock reads, no I/O, never starts a device and never touches the battery.
Window score = Σ (effective €/kWh) × kWh/slot, with solar surplus (P50 − load) valued at
``economics.export_value``. Penalties apply for GRID_CHARGE plan overlap and fuse pressure.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol
from zoneinfo import ZoneInfo

from ems.economics import EconomicSnapshot
from ems.tariffs import TariffPolicy

SLOT = timedelta(minutes=15)
SLOT_HOURS = 0.25

# €/kWh-equivalent added to a slot's effective price (#333 / audit F-33, F-42).
GRID_CHARGE_SLOT_PENALTY_EUR_PER_KWH = 0.08
FUSE_PRESSURE_PENALTY_EUR_PER_KWH = 0.08
# House fuse check when appliance draw is unknown (typical washer ~2 kW).
DEFAULT_APPLIANCE_POWER_W = 2000.0

_CLOCK_RANGE_RE = re.compile(
    r"^\s*(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})\s*$",
)

# Rough cycle energy for cost display only (homeowner estimate, not metered).
_ASSUMED_KWH: dict[str, float] = {
    "washer": 1.0,
    "dryer": 2.0,
    "dishwasher": 1.2,
    "other": 1.0,
    "unknown": 1.0,
}


class _PriceLike(Protocol):
    start: datetime
    eur_per_kwh: float


@dataclass(frozen=True)
class StartAdviceScoring:
    """Optional inputs for #333 scoring. Omit fields → price-only path (same as #227)."""

    p50_w_by_slot: dict[datetime, float] | None = None
    load_w_by_slot: dict[datetime, float] | None = None
    grid_charge_starts: frozenset[datetime] | None = None
    grid_limit_w: float | None = None
    appliance_power_w: float = DEFAULT_APPLIANCE_POWER_W
    export_model: str = "spot_minus_tax"
    energy_tax_eur_per_kwh: float = 0.13
    fixed_feed_in_eur_per_kwh: float = 0.01
    export_fee_eur_per_kwh: float = 0.0
    site_tz: ZoneInfo | None = None
    quiet_hours: str = ""
    preferred_window: str = ""


def _fmt_duration(slots: int) -> str:
    total_minutes = round(slots * SLOT_HOURS * 60)
    h, m = divmod(total_minutes, 60)
    if h and m:
        return f"{h}h{m}m"
    if h:
        return f"{h}h"
    return f"{m}m"


def _minutes_since_midnight(h: int, m: int) -> int:
    return h * 60 + m


def parse_clock_range(spec: str) -> tuple[int, int] | None:
    """Parse ``HH:MM-HH:MM``. Returns (start_min, end_min) from local midnight, or None."""
    if not str(spec or "").strip():
        return None
    m = _CLOCK_RANGE_RE.match(str(spec).strip())
    if not m:
        return None
    h1, m1, h2, m2 = (int(m.group(i)) for i in range(1, 5))
    if not (0 <= h1 < 24 and 0 <= m1 < 60 and 0 <= h2 < 24 and 0 <= m2 < 60):
        return None
    return _minutes_since_midnight(h1, m1), _minutes_since_midnight(h2, m2)


def _local_minutes(dt: datetime, tz: ZoneInfo) -> int:
    local = dt.astimezone(tz)
    return _minutes_since_midnight(local.hour, local.minute)


def _in_wrapped_range(minute: int, start: int, end: int) -> bool:
    """True when ``minute`` lies in [start, end) with optional midnight wrap (start > end)."""
    if start == end:
        return False
    if start < end:
        return start <= minute < end
    return minute >= start or minute < end


def instant_in_quiet_hours(instant: datetime, quiet_hours: str, tz: ZoneInfo) -> bool:
    parsed = parse_clock_range(quiet_hours)
    if parsed is None:
        return False
    start, end = parsed
    return _in_wrapped_range(_local_minutes(instant, tz), start, end)


def window_touches_quiet_hours(
    start: datetime,
    end: datetime,
    quiet_hours: str,
    tz: ZoneInfo,
) -> bool:
    if not str(quiet_hours or "").strip():
        return False
    return (
        instant_in_quiet_hours(start, quiet_hours, tz)
        or instant_in_quiet_hours(end, quiet_hours, tz)
    )


def window_outside_preferred(
    start: datetime,
    end: datetime,
    preferred: str,
    tz: ZoneInfo,
) -> bool:
    """True when the window is NOT fully inside the preferred local-time band (non-wrapping)."""
    parsed = parse_clock_range(preferred)
    if parsed is None:
        return False
    pref_start, pref_end = parsed
    if pref_start >= pref_end:
        # Wrapping preferred bands are not used for appliances — treat as invalid / no constraint.
        return False
    sm, em = _local_minutes(start, tz), _local_minutes(end, tz)
    return sm < pref_start or em > pref_end


def parse_preferred_windows_json(raw: str | None) -> dict[str, str]:
    if not raw or not str(raw).strip():
        return {}
    try:
        data = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    out: dict[str, str] = {}
    for k, v in data.items():
        if isinstance(k, str) and isinstance(v, str) and parse_clock_range(v):
            out[k] = v.strip()
    return out


def _economic_snapshot(scoring: StartAdviceScoring | None) -> EconomicSnapshot | None:
    if scoring is None:
        return None
    if scoring.p50_w_by_slot is None and scoring.load_w_by_slot is None:
        return None
    return EconomicSnapshot.from_tariff_policy(
        TariffPolicy(export_fee_eur_per_kwh=max(0.0, float(scoring.export_fee_eur_per_kwh))),
        export_model=scoring.export_model,
        energy_tax_eur_per_kwh=scoring.energy_tax_eur_per_kwh,
        fixed_feed_in_eur_per_kwh=scoring.fixed_feed_in_eur_per_kwh,
    )


def _load_w(
    slot_start: datetime,
    scoring: StartAdviceScoring | None,
) -> float | None:
    if scoring is None or scoring.load_w_by_slot is None:
        return None
    if slot_start in scoring.load_w_by_slot:
        return float(scoring.load_w_by_slot[slot_start])
    return None


def _slot_effective_eur_per_kwh(
    slot: _PriceLike,
    kwh_per_slot: float,
    scoring: StartAdviceScoring | None,
    snapshot: EconomicSnapshot | None,
) -> tuple[float, float]:
    """Return (effective €/kWh, solar_value €/kWh component used)."""
    price = float(slot.eur_per_kwh)
    solar_value = 0.0
    if snapshot is not None and scoring is not None and scoring.p50_w_by_slot is not None:
        p50 = float(scoring.p50_w_by_slot.get(slot.start, 0.0))
        load_w = _load_w(slot.start, scoring)
        if load_w is not None:
            surplus_w = max(0.0, p50 - load_w)
            surplus_kwh = surplus_w * SLOT_HOURS / 1000.0
            if kwh_per_slot > 0 and surplus_kwh > 0:
                export_credit = snapshot.export_credit(price)
                solar_value = export_credit * min(1.0, surplus_kwh / kwh_per_slot)
    effective = price - solar_value
    if scoring is not None and scoring.grid_charge_starts is not None:
        if slot.start in scoring.grid_charge_starts:
            effective += GRID_CHARGE_SLOT_PENALTY_EUR_PER_KWH
    if scoring is not None and scoring.grid_limit_w is not None and scoring.grid_limit_w > 0:
        load_w = _load_w(slot.start, scoring)
        if load_w is not None:
            demand_w = max(0.0, load_w) + max(0.0, scoring.appliance_power_w)
            if demand_w > scoring.grid_limit_w + 1e-6:
                effective += FUSE_PRESSURE_PENALTY_EUR_PER_KWH
    return effective, solar_value


def _window_overlaps_grid_charge(
    window: list[_PriceLike],
    grid_charge_starts: frozenset[datetime] | None,
) -> bool:
    if not grid_charge_starts:
        return False
    return any(s.start in grid_charge_starts for s in window)


def propose_start_window(
    price_slots: list[_PriceLike],
    *,
    duration_slots: int,
    now: datetime,
    horizon_end: datetime | None = None,
    assumed_kwh: float | None = 1.0,
    scoring: StartAdviceScoring | None = None,
) -> dict | None:
    """Cheapest contiguous ``duration_slots`` window in ``[now, horizon_end)``.

    Returns None when inputs cannot form a real window. Ties keep the earliest window.
    Never raises.
    """
    if duration_slots <= 0:
        return None
    end = horizon_end
    candidates = sorted(
        (
            s for s in price_slots
            if s.start >= now and (end is None or s.start < end)
        ),
        key=lambda s: s.start,
    )
    if len(candidates) < duration_slots:
        return None

    kwh_per_slot = (
        (assumed_kwh / duration_slots) if assumed_kwh is not None and assumed_kwh > 0 else 0.0
    )
    snapshot = _economic_snapshot(scoring)
    use_solar = (
        snapshot is not None
        and scoring is not None
        and scoring.p50_w_by_slot is not None
        and scoring.load_w_by_slot is not None
        and len(scoring.load_w_by_slot) > 0
    )
    price_only = not use_solar

    tz = (scoring.site_tz if scoring is not None else None) or ZoneInfo("UTC")
    quiet = str(scoring.quiet_hours if scoring else "").strip()
    preferred = str(scoring.preferred_window if scoring else "").strip()

    ranked: list[tuple[float, int, list[_PriceLike], float, int]] = []
    n = len(candidates)
    for i in range(n - duration_slots + 1):
        window = candidates[i:i + duration_slots]
        if any(window[k + 1].start - window[k].start != SLOT for k in range(len(window) - 1)):
            continue
        start, finish = window[0].start, window[-1].start + SLOT
        if quiet and window_touches_quiet_hours(start, finish, quiet, tz):
            continue
        if preferred and window_outside_preferred(start, finish, preferred, tz):
            continue
        total = 0.0
        solar_slots = 0
        for s in window:
            eff, solar_v = _slot_effective_eur_per_kwh(
                s,
                kwh_per_slot,
                scoring,
                snapshot if use_solar else None,
            )
            total += eff
            if solar_v > 1e-9:
                solar_slots += 1
        ranked.append((total, i, window, total / duration_slots, solar_slots))

    if not ranked:
        return None

    ranked.sort(key=lambda r: (r[0], r[1]))

    grid_starts = scoring.grid_charge_starts if scoring is not None else None
    chosen = ranked[0]
    grid_overlap = _window_overlaps_grid_charge(chosen[2], grid_starts)
    skipped_for_grid = False
    if grid_overlap and len(ranked) > 1:
        for entry in ranked[1:]:
            if not _window_overlaps_grid_charge(entry[2], grid_starts):
                chosen = entry
                grid_overlap = False
                skipped_for_grid = True
                break

    window = chosen[2]
    best_sum = chosen[0]
    avg = chosen[3]
    solar_slots = chosen[4]
    start, finish = window[0].start, window[-1].start + SLOT
    duration_label = _fmt_duration(duration_slots)

    reason_parts = [
        f"Best {duration_label} window from the day-ahead prices (avg €{avg:.2f}/kWh",
    ]
    if price_only and scoring is not None and scoring.p50_w_by_slot is not None:
        reason_parts.append("; solar/load forecast unavailable — prices only")
    elif not price_only and solar_slots > 0:
        reason_parts.append(f"; {solar_slots} slot(s) use solar export value")
    if grid_overlap:
        reason_parts.append("; overlaps planned grid-charge — consider another time")
    elif skipped_for_grid:
        reason_parts.append("; skipped a cheaper window that overlapped grid-charge")
    if assumed_kwh is not None and assumed_kwh > 0:
        reason_parts.append(f", assuming {assumed_kwh:g} kWh")
    reason_parts.append(").")
    reason = "".join(reason_parts).replace("(;", "(")

    out: dict = {
        "start": start.isoformat(),
        "end": finish.isoformat(),
        "slots": duration_slots,
        "avg_eur_per_kwh": round(avg, 4),
        "reason": reason,
    }
    if assumed_kwh is not None and assumed_kwh > 0:
        out["est_cost_eur"] = round(best_sum * kwh_per_slot, 2)
        out["assumed_kwh"] = assumed_kwh
    return out


def assumed_kwh_for_kind(kind: str) -> float:
    return _ASSUMED_KWH.get(str(kind), 1.0)


def scoring_from_settings(
    settings: dict,
    *,
    p50_w_by_slot: dict[datetime, float] | None = None,
    load_w_by_slot: dict[datetime, float] | None = None,
    grid_charge_starts: frozenset[datetime] | None = None,
    site_tz: ZoneInfo | None = None,
    preferred_window: str = "",
) -> StartAdviceScoring:
    try:
        grid_limit = float(settings.get("control.grid_limit_w", 0) or 0)
    except (TypeError, ValueError):
        grid_limit = 0.0
    if grid_limit <= 0:
        grid_limit = None  # type: ignore[assignment]
    return StartAdviceScoring(
        p50_w_by_slot=p50_w_by_slot,
        load_w_by_slot=load_w_by_slot,
        grid_charge_starts=grid_charge_starts,
        grid_limit_w=grid_limit,
        export_model=str(settings.get("prices.export_price_model", "spot_minus_tax")),
        energy_tax_eur_per_kwh=float(settings.get("prices.energy_tax_eur_per_kwh", 0.13)),
        fixed_feed_in_eur_per_kwh=float(settings.get("prices.fixed_feed_in_eur_per_kwh", 0.01)),
        export_fee_eur_per_kwh=float(settings.get("grid_fees.export_fee_eur_per_kwh", 0.0)),
        site_tz=site_tz,
        quiet_hours=str(settings.get("consumers.quiet_hours") or ""),
        preferred_window=preferred_window,
    )
