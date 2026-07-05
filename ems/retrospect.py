"""Retrospective: reconstruct the last-24h energy story from recorded history (SPEC §9.1).

The forward view (plan + projection) shows what the strategy WILL do; this shows what it DID — the
evidence that earns trust. Recorded samples (irregular cadence) are resampled onto the same 15-min
grid as the forecast, integrated into kWh, split into import/export and charge/discharge, and costed
against the day's prices. Pure + unit-tested — the API passes in stored rows.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from ems.sources.prices import PriceSlot

_SLOT_MIN = 15
_DH = _SLOT_MIN / 60.0  # hours per slot, for energy = power × time


@dataclass(frozen=True)
class PastSlot:
    start: datetime  # 15-min slot start (UTC)
    soc_pct: float | None
    grid_w: float  # + import / − export (mean over the slot)
    solar_w: float
    battery_w: float  # + discharge / − charge
    load_w: float  # total house load (incl. EV) — the plotted demand
    non_ev_load_w: float  # house load excluding EV charging — the charge-kind split input (§4.5)
    eur_per_kwh: float | None


@dataclass(frozen=True)
class PastStory:
    slots: list[PastSlot]
    import_kwh: float
    export_kwh: float
    solar_kwh: float
    charge_kwh: float
    discharge_kwh: float
    load_kwh: float
    grid_cost_eur: float | None  # net of any export credit; None if no prices aligned
    self_sufficiency_pct: float | None  # share of house load NOT taken from the grid
    soc_start_pct: float | None
    soc_end_pct: float | None


def past_headline(story: PastStory) -> str:
    """A plain-language recap of the last 24h — the evidence that the strategy worked."""
    if not story.slots:
        return "No history yet — the story of the last 24h fills in as data is recorded."
    parts: list[str] = []
    if story.solar_kwh > 0.05:
        parts.append(f"{story.solar_kwh:.1f} kWh from the sun")
    if story.discharge_kwh > 0.05:
        parts.append(f"{story.discharge_kwh:.1f} kWh out of the battery")
    parts.append(
        f"{story.import_kwh:.1f} kWh imported"
        + (f", {story.export_kwh:.1f} kWh exported" if story.export_kwh > 0.05 else "")
    )
    head = "Last 24h — " + ", ".join(parts) + "."
    if story.self_sufficiency_pct is not None:
        head += f" {story.self_sufficiency_pct:.0f}% self-sufficient."
    if story.grid_cost_eur is not None:
        head += f" Net grid cost €{story.grid_cost_eur:.2f}."
    return head


def _parse(ts: object) -> datetime | None:
    if not isinstance(ts, str):
        return None
    try:
        dt = datetime.fromisoformat(ts)
    except ValueError:
        return None
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def _floor(dt: datetime) -> datetime:
    return dt.replace(minute=(dt.minute // _SLOT_MIN) * _SLOT_MIN, second=0, microsecond=0)


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def build_past_story(
    raw_rows: list[dict],
    derived_rows: list[dict],
    prices: list[PriceSlot],
    now: datetime,
    *,
    hours: int = 24,
) -> PastStory:
    """Resample recorded history into 15-min slots over the last `hours` and summarise it.

    Energy uses a zero-order hold (each slot's mean power held across the slot), the standard
    interval-metering approximation; sparse slots (a missed cycle) are weighted like full ones."""
    if now.tzinfo is None:
        raise ValueError("build_past_story: 'now' must be timezone-aware")
    now_utc = now.astimezone(UTC)
    cutoff = now_utc - timedelta(hours=hours)

    raw_by: dict[datetime, dict[str, list[float]]] = defaultdict(
        lambda: {"grid": [], "solar": [], "batt": [], "soc": []}
    )
    for r in raw_rows:
        dt = _parse(r.get("ts"))
        if dt is None or dt < cutoff or dt > now_utc:
            continue
        slot = _floor(dt)
        raw_by[slot]["grid"].append(float(r.get("grid_power_w", 0.0)))
        raw_by[slot]["solar"].append(float(r.get("solar_power_w", 0.0)))
        raw_by[slot]["batt"].append(float(r.get("battery_power_w", 0.0)))
        if r.get("soc_pct") is not None:
            raw_by[slot]["soc"].append(float(r["soc_pct"]))

    load_by: dict[datetime, list[float]] = defaultdict(list)
    non_ev_by: dict[datetime, list[float]] = defaultdict(list)
    for r in derived_rows:
        dt = _parse(r.get("ts"))
        if dt is None or dt < cutoff or dt > now_utc:
            continue
        if r.get("house_load_w") is not None:
            load_by[_floor(dt)].append(float(r["house_load_w"]))
        # Non-EV (house-only) load: the correct input to the solar-vs-grid charge split, so a
        # solar-fed battery charge during a car session isn't mislabelled a grid charge (§4.5).
        # Fall back to total load when the column is absent (older rows) — no worse than before.
        nev = r.get("non_ev_load_w")
        if nev is None:
            nev = r.get("house_load_w")
        if nev is not None:
            non_ev_by[_floor(dt)].append(float(nev))

    price_by = {p.start.astimezone(UTC): p.eur_per_kwh for p in prices}

    slots: list[PastSlot] = []
    for slot in sorted(raw_by):
        b = raw_by[slot]
        slots.append(PastSlot(
            start=slot,
            soc_pct=_mean(b["soc"]) if b["soc"] else None,
            grid_w=_mean(b["grid"]),
            solar_w=_mean(b["solar"]),
            battery_w=_mean(b["batt"]),
            load_w=_mean(load_by[slot]) if load_by.get(slot) else 0.0,
            non_ev_load_w=_mean(non_ev_by[slot]) if non_ev_by.get(slot) else 0.0,
            eur_per_kwh=price_by.get(slot),
        ))

    def kwh(power_w: float) -> float:
        return power_w * _DH / 1000.0

    import_kwh = sum(kwh(max(0.0, s.grid_w)) for s in slots)
    export_kwh = sum(kwh(max(0.0, -s.grid_w)) for s in slots)
    solar_kwh = sum(kwh(s.solar_w) for s in slots)
    charge_kwh = sum(kwh(max(0.0, -s.battery_w)) for s in slots)
    discharge_kwh = sum(kwh(max(0.0, s.battery_w)) for s in slots)
    load_kwh = sum(kwh(s.load_w) for s in slots)

    has_price = any(s.eur_per_kwh is not None for s in slots)
    cost = sum(
        (kwh(max(0.0, s.grid_w)) - kwh(max(0.0, -s.grid_w))) * s.eur_per_kwh
        for s in slots if s.eur_per_kwh is not None
    )
    # Only report self-sufficiency when the load actually covers the import — if import > load,
    # the raw/derived rows are out of sync (a missing derived row), so the number isn't trustworthy.
    self_suff = (
        min(100.0, (load_kwh - import_kwh) / load_kwh * 100.0)
        if load_kwh > 0 and load_kwh >= import_kwh else None
    )
    soc_slots = [s.soc_pct for s in slots if s.soc_pct is not None]

    return PastStory(
        slots=slots,
        import_kwh=round(import_kwh, 2),
        export_kwh=round(export_kwh, 2),
        solar_kwh=round(solar_kwh, 2),
        charge_kwh=round(charge_kwh, 2),
        discharge_kwh=round(discharge_kwh, 2),
        load_kwh=round(load_kwh, 2),
        grid_cost_eur=(round(cost, 2) + 0.0) if has_price else None,  # +0.0 avoids -0.0
        self_sufficiency_pct=round(self_suff, 1) if self_suff is not None else None,
        soc_start_pct=round(soc_slots[0], 1) if soc_slots else None,
        soc_end_pct=round(soc_slots[-1], 1) if soc_slots else None,
    )
