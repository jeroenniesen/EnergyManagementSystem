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

from ems.planner.economics import export_value

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
    return instant_in_quiet_hours(start, quiet_hours, tz) or instant_in_quiet_hours(
        end, quiet_hours, tz
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
        # Wrapping preferred bands are rejected at save time. Stored leftovers do not constrain.
        return False
    sm, em = _local_minutes(start, tz), _local_minutes(end, tz)
    # A window that crosses local midnight cannot sit inside a same-day band.
    if em <= sm:
        return True
    return sm < pref_start or em > pref_end


def parse_preferred_windows_json(raw: str | None) -> dict[str, str]:
    """Best-effort map for advice. Invalid entries are dropped; save-time validation is strict."""
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
        if not isinstance(k, str) or not k.strip() or not isinstance(v, str):
            continue
        parsed = parse_clock_range(v)
        if parsed is None or parsed[0] >= parsed[1]:
            continue
        out[k.strip()] = v.strip()
    return out


def preferred_windows_error(raw: str) -> str | None:
    """None when ``raw`` is empty/{} or a same-day HH:MM-HH:MM map. Otherwise an error string."""
    text = str(raw or "").strip() or "{}"
    try:
        data = json.loads(text)
    except (TypeError, ValueError, json.JSONDecodeError):
        return 'expected JSON like {"device-id": "09:00-17:00"}'
    if not isinstance(data, dict):
        return 'expected JSON like {"device-id": "09:00-17:00"}'
    for k, v in data.items():
        if not isinstance(k, str) or not k.strip() or not isinstance(v, str):
            return 'expected JSON like {"device-id": "09:00-17:00"}'
        parsed = parse_clock_range(v)
        if parsed is None or parsed[0] >= parsed[1]:
            return (
                f"{k.strip() or 'device'}: use a same-day HH:MM-HH:MM window "
                "(start before end), e.g. 09:00-17:00"
            )
    return None


def quiet_hours_error(raw: str) -> str | None:
    """None when empty or a real HH:MM-HH:MM range (midnight wrap allowed)."""
    text = str(raw or "").strip()
    if not text:
        return None
    parsed = parse_clock_range(text)
    if parsed is None or parsed[0] == parsed[1]:
        return "use HH:MM-HH:MM (e.g. 22:00-07:00) or leave empty"
    return None


def _load_w(
    slot_start: datetime,
    scoring: StartAdviceScoring | None,
) -> float | None:
    if scoring is None or scoring.load_w_by_slot is None:
        return None
    if slot_start in scoring.load_w_by_slot:
        return float(scoring.load_w_by_slot[slot_start])
    return None


def _export_eur_per_kwh(price: float, scoring: StartAdviceScoring) -> float | None:
    """€/kWh forgone by using surplus on site. None when the price cannot be valued."""
    try:
        credit = export_value(
            price,
            model=scoring.export_model,
            energy_tax_eur_per_kwh=float(scoring.energy_tax_eur_per_kwh),
            fixed_feed_in_eur_per_kwh=float(scoring.fixed_feed_in_eur_per_kwh),
        )
        fee = max(0.0, float(scoring.export_fee_eur_per_kwh))
    except (TypeError, ValueError):
        return None
    return float(credit) - fee


def _slot_economics(
    slot: _PriceLike,
    kwh_per_slot: float,
    scoring: StartAdviceScoring | None,
    *,
    use_solar: bool,
) -> tuple[float, float, bool, bool]:
    """Return (price − solar_value €/kWh, solar_value €/kWh, grid-charge hit, fuse hit).

    Penalties are flags only — they rank windows and never enter ``est_cost_eur``.
    Missing P50 or load for this slot contributes no solar credit (surplus stays 0).
    """
    price = float(slot.eur_per_kwh)
    solar_value = 0.0
    if use_solar and scoring is not None and scoring.p50_w_by_slot is not None:
        load_w = _load_w(slot.start, scoring)
        if load_w is not None and kwh_per_slot > 0:
            p50 = max(0.0, float(scoring.p50_w_by_slot.get(slot.start, 0.0)))
            surplus_w = max(0.0, p50 - max(0.0, load_w))
            surplus_kwh = surplus_w * SLOT_HOURS / 1000.0
            if surplus_kwh > 0:
                credit = _export_eur_per_kwh(price, scoring)
                if credit is not None:
                    solar_value = credit * min(1.0, surplus_kwh / kwh_per_slot)
    grid_hit = bool(
        scoring is not None
        and scoring.grid_charge_starts is not None
        and slot.start in scoring.grid_charge_starts
    )
    fuse_hit = False
    if scoring is not None and scoring.grid_limit_w is not None and scoring.grid_limit_w > 0:
        load_w = _load_w(slot.start, scoring)
        if load_w is not None:
            p50 = 0.0
            if scoring.p50_w_by_slot is not None:
                p50 = max(0.0, float(scoring.p50_w_by_slot.get(slot.start, 0.0)))
            # Grid draw ≈ house load + appliance − PV. Missing PV counts as 0 (no invented sun).
            demand_w = max(0.0, load_w) + max(0.0, scoring.appliance_power_w) - p50
            if demand_w > scoring.grid_limit_w + 1e-6:
                fuse_hit = True
    return price - solar_value, solar_value, grid_hit, fuse_hit


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
        (s for s in price_slots if s.start >= now and (end is None or s.start < end)),
        key=lambda s: s.start,
    )
    if len(candidates) < duration_slots:
        return None

    kwh_per_slot = (
        (assumed_kwh / duration_slots) if assumed_kwh is not None and assumed_kwh > 0 else 0.0
    )
    use_solar = (
        scoring is not None
        and scoring.p50_w_by_slot is not None
        and scoring.load_w_by_slot is not None
        and len(scoring.load_w_by_slot) > 0
    )
    price_only = not use_solar

    tz = (scoring.site_tz if scoring is not None else None) or ZoneInfo("UTC")
    quiet = str(scoring.quiet_hours if scoring else "").strip()
    preferred = str(scoring.preferred_window if scoring else "").strip()

    # score ranks; econ_sum is Σ(price − solar_value) and is the only euro figure we publish.
    ranked: list[tuple[float, int, list[_PriceLike], float, int, int]] = []
    n = len(candidates)
    for i in range(n - duration_slots + 1):
        window = candidates[i : i + duration_slots]
        if any(window[k + 1].start - window[k].start != SLOT for k in range(len(window) - 1)):
            continue
        start, finish = window[0].start, window[-1].start + SLOT
        if quiet and window_touches_quiet_hours(start, finish, quiet, tz):
            continue
        if preferred and window_outside_preferred(start, finish, preferred, tz):
            continue
        econ_sum = 0.0
        score = 0.0
        solar_slots = 0
        fuse_hits = 0
        for s in window:
            econ, solar_v, grid_hit, fuse_hit = _slot_economics(
                s,
                kwh_per_slot,
                scoring,
                use_solar=use_solar,
            )
            econ_sum += econ
            slot_score = econ
            if grid_hit:
                slot_score += GRID_CHARGE_SLOT_PENALTY_EUR_PER_KWH
            if fuse_hit:
                slot_score += FUSE_PRESSURE_PENALTY_EUR_PER_KWH
                fuse_hits += 1
            score += slot_score
            if solar_v > 1e-9:
                solar_slots += 1
        ranked.append((score, i, window, econ_sum, solar_slots, fuse_hits))

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
    econ_sum = chosen[3]
    solar_slots = chosen[4]
    fuse_hits = chosen[5]
    avg = econ_sum / duration_slots
    start, finish = window[0].start, window[-1].start + SLOT
    duration_label = _fmt_duration(duration_slots)
    avoided_fuse = any(entry[5] > fuse_hits and entry[3] + 1e-9 < econ_sum for entry in ranked)

    reason_parts = [
        f"Best {duration_label} window from the day-ahead prices (avg €{avg:.2f}/kWh",
    ]
    if price_only and scoring is not None and scoring.p50_w_by_slot is not None:
        reason_parts.append("; solar/load forecast unavailable — prices only")
    elif not price_only and solar_slots > 0:
        if solar_slots == 1:
            reason_parts.append("; 1 slot uses solar export value")
        else:
            reason_parts.append(f"; {solar_slots} slots use solar export value")
    if grid_overlap:
        reason_parts.append("; overlaps planned grid-charge — consider another time")
    elif skipped_for_grid:
        reason_parts.append("; skipped a cheaper window that overlapped grid-charge")
    if fuse_hits or avoided_fuse:
        kw = max(0.0, scoring.appliance_power_w) / 1000.0 if scoring is not None else 2.0
        reason_parts.append(
            f"; house fuse is tight at an assumed {kw:g} kW draw"
            if fuse_hits
            else "; skipped a cheaper window that would press the house fuse"
        )
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
        # Σ(price − solar_value) × kWh/slot. Penalties are not euros.
        out["est_cost_eur"] = round(econ_sum * kwh_per_slot, 2)
        out["assumed_kwh"] = assumed_kwh
    return out


def assumed_kwh_for_kind(kind: str) -> float:
    return _ASSUMED_KWH.get(str(kind), 1.0)


def _setting_float(settings: dict, key: str, default: float) -> float:
    try:
        raw = settings.get(key, default)
        if raw is None or raw == "":
            return float(default)
        return float(raw)
    except (TypeError, ValueError):
        return float(default)


def scoring_from_settings(
    settings: dict,
    *,
    p50_w_by_slot: dict[datetime, float] | None = None,
    load_w_by_slot: dict[datetime, float] | None = None,
    grid_charge_starts: frozenset[datetime] | None = None,
    site_tz: ZoneInfo | None = None,
    preferred_window: str = "",
) -> StartAdviceScoring:
    # Default watts exist while the fuse is still "unset" (#197). Do not invent a limit then.
    fuse = str(settings.get("control.grid_fuse", "unset") or "unset").strip().lower()
    watts = _setting_float(settings, "control.grid_limit_w", 0.0)
    grid_limit = watts if fuse != "unset" and watts > 0 else None
    return StartAdviceScoring(
        p50_w_by_slot=p50_w_by_slot,
        load_w_by_slot=load_w_by_slot,
        grid_charge_starts=grid_charge_starts,
        grid_limit_w=grid_limit,
        export_model=str(
            settings.get("prices.export_price_model", "spot_minus_tax") or "spot_minus_tax"
        ),
        energy_tax_eur_per_kwh=_setting_float(settings, "prices.energy_tax_eur_per_kwh", 0.13),
        fixed_feed_in_eur_per_kwh=_setting_float(
            settings,
            "prices.fixed_feed_in_eur_per_kwh",
            0.01,
        ),
        export_fee_eur_per_kwh=_setting_float(settings, "grid_fees.export_fee_eur_per_kwh", 0.0),
        site_tz=site_tz,
        quiet_hours=str(settings.get("consumers.quiet_hours") or ""),
        preferred_window=preferred_window,
    )
