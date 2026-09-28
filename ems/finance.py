"""Daily finance: what the grid cost, what the battery cost in wear, and what the EMS saved —
measured from recorded history, not from the plan (backlog B-03; spec 2026-07-03).

Pure — the caller supplies one local day's raw rows and stored price slots. The baseline is the
"no battery, same solar + loads" counterfactual: removing the battery from the meter balance gives
`grid'_w = grid_w + battery_w` per slot (battery + grid + solar = load). Export is credited via
`economics.export_value` under the configured feed-in model — default `net_metering` (full price,
today's saldering); `spot_minus_tax` / `fixed` model the post-2027 world (B-05) — applied to BOTH
the actual and the baseline cost so the comparison stays honest (under a low feed-in the baseline
household, which exports more, is penalised more, so the battery's measured benefit honestly grows).
Fixed fees and taxes are the same in both worlds and cancel out of `saved_eur`.

Battery wear is charged per **kWh discharged** (`degradation_eur_per_kwh`), which prices a
charge→discharge cycle once on the energy delivered — the same basis the planner spends in its
arbitrage break-even.

Every € figure is computed over the SAME priced slots (cost, baseline, and the wear inside
`saved`), so a partial-price day yields a correct partial-window saving — it can't mix partial
revenue with a full day of wear. A day with no priced slots at all reports energy only (€ = None);
otherwise `price_coverage` (0..1) signals how much of the day the money figures cover.

**Savings breakdown (B-36 / #80).** When € figures exist, `saved_eur` is also split into three
plain-language parts that sum to it (wear included in each discharged kWh):

- `solar_self_use_eur` — own solar stored in the battery and used later (vs exporting it)
- `avoided_expensive_eur` — grid energy shifted off expensive hours (cheap charge → dear discharge)
- `battery_contribution_eur` — residual (unmatched discharge / rounding) so the three sum to
  `saved_eur`

Attribution uses a same-day FIFO of priced charge packets (solar-first vs grid), matching the
energy-flow solar-first intuition. Comparison baseline stays "without a battery".

**EMS vs battery AUTO (#131).** `day_vs_auto` answers "is EMS worth it vs the vendor default?" by
simulating Indevolt self-consumption (`auto_selfuse`) on the same reconstructed load + solar, then
pricing BOTH the measured meter and the simulation on the same quarter-hour basis via
`EconomicSnapshot.from_tariff_policy` (import fee / `tibber_total_includes_all` included) — not via
replay's simpler spot×kWh cost. The simulation is a model (fixed η=0.90, no 50 W floor, no dead
zone or standby loss); EV charging sits inside reconstructed load while EMS would hold the battery.
Computed per request only — never written into `daily_finance`.
"""
from __future__ import annotations

import math
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from ems.economics import EconomicSnapshot
from ems.retrospect import _floor, _mean, _parse
from ems.tariffs import TariffPolicy
from ems.timeseries import observed_segments

# Ephemeral vs-AUTO fields attached to a finance day dict for the API response only. MUST be
# stripped before any `daily_finance` upsert so a simulation never lands in the long-horizon store
# and never bumps `_FINANCE_CALC_VERSION`.
VS_AUTO_EPHEMERAL_KEYS = (
    "saved_vs_auto_eur",
    "auto_cost_eur",
    "auto_grid_cost_eur",
    "auto_battery_cost_eur",
    "vs_auto_has_sim",
)

VS_AUTO_MODEL_NOTE = (
    "Simulated model of the battery's own default (self-consumption): fixed round-trip "
    "efficiency 0.90, no 50 W floor, no dead zone or standby loss. The EV charger is inside "
    "house load — this model lets AUTO discharge into it, while EMS holds the battery during "
    "car charging."
)

_SLOT_H = 0.25  # hours per 15-min quarter
_AUTO_ETA = 0.90  # fixed model efficiency (AC #131) — not the live settings knob


@dataclass(frozen=True)
class DayFinance:
    day: str  # local YYYY-MM-DD
    has_data: bool
    price_coverage: float  # 0..1 — share of sampled slots with a stored price
    sample_coverage: float  # 0..1 — share of the requested window with observed samples
    grid_cost_eur: float | None
    battery_cost_eur: float | None
    baseline_cost_eur: float | None
    saved_eur: float | None
    grid_import_kwh: float
    grid_export_kwh: float
    battery_charge_kwh: float
    battery_discharge_kwh: float
    # B-36 breakdown — None when saved_eur is None (no priced window).
    solar_self_use_eur: float | None = None
    avoided_expensive_eur: float | None = None
    battery_contribution_eur: float | None = None

    def to_dict(self) -> dict:
        def r2(x: float | None) -> float | None:
            return None if x is None else round(x, 2)

        return {
            "day": self.day, "has_data": self.has_data,
            "price_coverage": round(self.price_coverage, 3),
            "sample_coverage": round(self.sample_coverage, 3),
            "grid_cost_eur": r2(self.grid_cost_eur),
            "battery_cost_eur": r2(self.battery_cost_eur),
            "baseline_cost_eur": r2(self.baseline_cost_eur),
            "saved_eur": r2(self.saved_eur),
            "grid_import_kwh": round(self.grid_import_kwh, 2),
            "grid_export_kwh": round(self.grid_export_kwh, 2),
            "battery_charge_kwh": round(self.battery_charge_kwh, 2),
            "battery_discharge_kwh": round(self.battery_discharge_kwh, 2),
            "solar_self_use_eur": r2(self.solar_self_use_eur),
            "avoided_expensive_eur": r2(self.avoided_expensive_eur),
            "battery_contribution_eur": r2(self.battery_contribution_eur),
        }


@dataclass
class _ChargePacket:
    kwh: float
    unit_cost: float  # €/kWh paid (grid import) or forgone (export credit) to store
    source: str  # "solar" | "grid"


def day_finance(
    raw_rows: list[dict],
    price_rows: list[dict],
    *,
    day: str,
    degradation_eur_per_kwh: float = 0.05,
    export_price_model: str = "net_metering",
    energy_tax_eur_per_kwh: float = 0.13,
    fixed_feed_in_eur_per_kwh: float = 0.01,
    tibber_total_includes_all: bool = False,
    import_fee_eur_per_kwh: float = 0.0,
    export_fee_eur_per_kwh: float = 0.0,
    window_start: datetime | None = None,
    window_end: datetime | None = None,
    sample_interval_seconds: float = 900.0,
    max_hold_seconds: float | None = None,
) -> DayFinance:
    """One day's finance from raw samples (`ts`, `grid_power_w`, `battery_power_w`, optional
    `solar_power_w`; the caller windows the rows to the local day) and stored price slots
    (`start_ts`, `eur_per_kwh`).

    `export_price_model` (+ `energy_tax_eur_per_kwh` / `fixed_feed_in_eur_per_kwh`) picks how
    exported energy is valued (see module docstring / `economics.export_value`); the default
    `net_metering` credits export at the full spot price — today's saldering behaviour."""
    # Ensure solar is present so observed_segments can require the field without dropping rows
    # from older callers that only passed grid + battery.
    normalized = [
        {**r, "solar_power_w": r.get("solar_power_w", 0.0)} for r in raw_rows
    ]
    timestamps = [dt for row in normalized if (dt := _parse(row.get("ts"))) is not None]
    if window_start is None and timestamps:
        window_start = _floor(min(timestamps))
    if window_end is None and timestamps:
        window_end = _floor(max(timestamps)) + timedelta(minutes=15)
    if window_start is None or window_end is None:
        return DayFinance(day, False, 0.0, 0.0, None, None, None, None,
                          0.0, 0.0, 0.0, 0.0)

    segments = observed_segments(
        normalized, start=window_start, end=window_end,
        fields=("grid_power_w", "battery_power_w", "solar_power_w"),
        nominal_interval_seconds=sample_interval_seconds,
        max_hold_seconds=max_hold_seconds,
    )

    price_by: dict[datetime, float] = {}
    for p in price_rows:
        dt = _parse(p.get("start_ts"))
        if dt is not None:
            price_by[_floor(dt)] = float(p.get("eur_per_kwh", 0.0))

    imp = exp = chg = dis = 0.0  # full-day physical energy (always reported)
    dis_priced = 0.0             # discharge over PRICED slots only → wear inside `saved`
    cost = base_cost = 0.0
    priced_seconds = 0.0
    observed_seconds = 0.0
    solar_self_use = 0.0
    avoided_expensive = 0.0
    residual_benefit = 0.0
    charge_q: deque[_ChargePacket] = deque()
    # Keep all measured cost calculations on the same normalized economic boundary used by
    # planning and savings.  Tibber totals already include the import fee, so suppress it here.
    tariff_policy = TariffPolicy(
        import_fee_eur_per_kwh=0.0 if tibber_total_includes_all else import_fee_eur_per_kwh,
        export_fee_eur_per_kwh=export_fee_eur_per_kwh,
    )
    for segment in segments:
        grid_w = segment.values["grid_power_w"]
        batt_w = segment.values["battery_power_w"]  # + discharge / − charge
        solar_w = max(0.0, segment.values["solar_power_w"])
        hours = segment.duration_seconds / 3600.0
        observed_seconds += segment.duration_seconds
        imp += max(0.0, grid_w) * hours / 1000.0
        exp += max(0.0, -grid_w) * hours / 1000.0
        dis += max(0.0, batt_w) * hours / 1000.0
        chg += max(0.0, -batt_w) * hours / 1000.0
        price = price_by.get(_floor(segment.start))
        if price is None:
            continue
        priced_seconds += segment.duration_seconds
        snapshot = EconomicSnapshot.from_tariff_policy(
            tariff_policy,
            raw_price_eur_per_kwh=price,
            degradation_eur_per_kwh=degradation_eur_per_kwh,
            export_model=export_price_model,
            energy_tax_eur_per_kwh=energy_tax_eur_per_kwh,
            fixed_feed_in_eur_per_kwh=fixed_feed_in_eur_per_kwh,
        )
        import_price = snapshot.import_price_eur_per_kwh
        # Import costs the full price; export earns the feed-in VALUE (full price under saldering,
        # less post-2027 — may even be negative). Same credit in both worlds so `saved` stays fair.
        credit = snapshot.export_credit(price)
        cost += (max(0.0, grid_w) * import_price - max(0.0, -grid_w) * credit) * hours / 1000.0
        baseline_w = grid_w + batt_w  # the meter with the battery removed
        base_cost += (max(0.0, baseline_w) * import_price
                      - max(0.0, -baseline_w) * credit) * hours / 1000.0
        dis_kwh = max(0.0, batt_w) * hours / 1000.0
        chg_kwh = max(0.0, -batt_w) * hours / 1000.0
        dis_priced += dis_kwh

        # FIFO charge packets for the B-36 breakdown (priced slots only).
        if chg_kwh > 0.0:
            # Solar-first: load = grid + solar + battery; solar left after serving load can charge.
            load_w = grid_w + solar_w + batt_w
            solar_to_load = min(solar_w, max(0.0, load_w))
            solar_left = solar_w - solar_to_load
            solar_chg = min(chg_kwh, solar_left * hours / 1000.0)
            grid_chg = chg_kwh - solar_chg
            # Storing solar forgoes the export credit; storing grid energy pays the import price.
            if solar_chg > 0.0:
                charge_q.append(_ChargePacket(solar_chg, credit, "solar"))
            if grid_chg > 0.0:
                charge_q.append(_ChargePacket(grid_chg, import_price, "grid"))
        if dis_kwh > 0.0:
            remaining = dis_kwh
            while remaining > 1e-12 and charge_q:
                pkt = charge_q[0]
                take = min(remaining, pkt.kwh)
                # Benefit of discharging this packet now (vs having no battery): avoid import_price,
                # minus what it cost to store, minus wear on the delivered kWh.
                benefit = take * (import_price - pkt.unit_cost - degradation_eur_per_kwh)
                if pkt.source == "solar":
                    solar_self_use += benefit
                else:
                    avoided_expensive += benefit
                pkt.kwh -= take
                remaining -= take
                if pkt.kwh <= 1e-12:
                    charge_q.popleft()
            if remaining > 1e-12:
                # Discharge with no same-day priced charge to match (overnight carry / gap) —
                # count as residual battery contribution at avoided import minus wear.
                residual_benefit += remaining * (import_price - degradation_eur_per_kwh)

    coverage = priced_seconds / observed_seconds if observed_seconds else 0.0
    # DST-safe window length: subtract in UTC so a spring-forward / fall-back local day is 23 h /
    # 25 h (92 / 100 quarters), not a naive 24 h wall-clock difference.
    window_seconds = max(
        0.0,
        (window_end.astimezone(UTC) - window_start.astimezone(UTC)).total_seconds(),
    )
    sample_coverage = observed_seconds / window_seconds if window_seconds else 0.0
    # Give € figures whenever ANY slot is priced. Charging wear only over PRICED-slot discharge
    # (`dis_priced`) keeps cost, baseline and wear on the SAME window, so a partial-price day yields
    # a correct partial-window saving (never full-day wear against priced-only revenue). Confidence
    # is signalled by `price_coverage`, not by blanking the numbers.
    if priced_seconds:
        battery_cost = dis_priced * degradation_eur_per_kwh
        saved = base_cost - cost - battery_cost
        # Reconcile FIFO attribution to the exact measured `saved` (floating residuals + unmatched
        # charge left in the queue that never discharged today).
        attributed = solar_self_use + avoided_expensive + residual_benefit
        battery_contribution = residual_benefit + (saved - attributed)
        return DayFinance(
            day, True, coverage, sample_coverage, cost, battery_cost, base_cost, saved,
            imp, exp, chg, dis,
            solar_self_use_eur=solar_self_use,
            avoided_expensive_eur=avoided_expensive,
            battery_contribution_eur=battery_contribution,
        )
    # No priced slots at all → can't compute money figures; report energy only.
    return DayFinance(day, bool(segments), coverage, sample_coverage,
                      None, None, None, None, imp, exp, chg, dis)


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _slot_series(
    raw_rows: list[dict],
) -> tuple[
    dict[datetime, float], dict[datetime, float], dict[datetime, float], dict[datetime, float]
]:
    """Mean grid/solar/battery/SoC per 15-min slot (SPEC §4 load reconstruction)."""
    grid_by: dict[datetime, list[float]] = defaultdict(list)
    solar_l: dict[datetime, list[float]] = defaultdict(list)
    batt_by: dict[datetime, list[float]] = defaultdict(list)
    soc_by: dict[datetime, list[float]] = defaultdict(list)
    for r in raw_rows:
        dt = _parse(r.get("ts"))
        if dt is None:
            continue
        s = _floor(dt)
        grid_by[s].append(float(r.get("grid_power_w", 0.0)))
        solar_l[s].append(float(r.get("solar_power_w", 0.0) or 0.0))
        batt_by[s].append(float(r.get("battery_power_w", 0.0)))
        if r.get("soc_pct") is not None:
            soc_by[s].append(float(r["soc_pct"]))
    load_by: dict[datetime, float] = {}
    solar_by: dict[datetime, float] = {}
    batt_meas: dict[datetime, float] = {}
    for s in grid_by:
        solar = _mean(solar_l[s])
        # house_load = grid + solar + battery (SPEC §4 / load_model) — includes EV on P1.
        load_by[s] = _mean(grid_by[s]) + solar + _mean(batt_by[s])
        solar_by[s] = solar
        batt_meas[s] = _mean(batt_by[s])
    start_soc_by: dict[datetime, float] = {s: _mean(v) for s, v in soc_by.items()}
    return load_by, solar_by, batt_meas, start_soc_by


def _price_grid_cost(
    grid_w_by: dict[datetime, float],
    price_by: dict[datetime, float],
    *,
    degradation_eur_per_kwh: float,
    discharge_kwh: float,
    export_price_model: str,
    energy_tax_eur_per_kwh: float,
    fixed_feed_in_eur_per_kwh: float,
    tibber_total_includes_all: bool,
    import_fee_eur_per_kwh: float,
    export_fee_eur_per_kwh: float,
) -> tuple[float | None, float]:
    """Price a quarter-hour grid series via the same EconomicSnapshot path as `day_finance`.

    Returns `(grid_cost_eur or None if no priced slots, battery_wear_eur)`. Wear uses the caller's
    discharge total (priced-window discharge for honesty with day_finance's `dis_priced` basis when
    the caller passes only priced-slot discharge)."""
    tariff_policy = TariffPolicy(
        import_fee_eur_per_kwh=0.0 if tibber_total_includes_all else import_fee_eur_per_kwh,
        export_fee_eur_per_kwh=export_fee_eur_per_kwh,
    )
    cost = 0.0
    priced = 0
    for slot, grid_w in grid_w_by.items():
        price = price_by.get(slot)
        if price is None:
            continue
        priced += 1
        snapshot = EconomicSnapshot.from_tariff_policy(
            tariff_policy,
            raw_price_eur_per_kwh=price,
            degradation_eur_per_kwh=degradation_eur_per_kwh,
            export_model=export_price_model,
            energy_tax_eur_per_kwh=energy_tax_eur_per_kwh,
            fixed_feed_in_eur_per_kwh=fixed_feed_in_eur_per_kwh,
        )
        import_price = snapshot.import_price_eur_per_kwh
        credit = snapshot.export_credit(price)
        cost += (max(0.0, grid_w) * import_price - max(0.0, -grid_w) * credit) * _SLOT_H / 1000.0
    wear = discharge_kwh * degradation_eur_per_kwh
    return (cost if priced else None), wear


def _simulate_auto_battery(
    slots: list[datetime],
    load_by: dict[datetime, float],
    solar_by: dict[datetime, float],
    *,
    start_soc: float,
    usable_kwh: float,
    max_charge_w: float,
    max_discharge_w: float,
    min_reserve_soc: float,
) -> tuple[dict[datetime, float], dict[datetime, float]]:
    """Vendor AUTO / self-consumption trajectory (mirrors replay `auto_selfuse` / projection).

    Fixed η = `_AUTO_ETA` (0.90). No 50 W dead-band, no standby loss — deliberate model limits
    called out in `VS_AUTO_MODEL_NOTE`. Returns `(grid_w_by_slot, battery_w_by_slot)`."""
    eta = math.sqrt(_clamp(_AUTO_ETA, 1e-6, 1.0))
    usable = max(1e-6, usable_kwh)
    reserve_kwh = _clamp(min_reserve_soc, 0.0, 100.0) / 100.0 * usable
    soc_kwh = _clamp(start_soc, 0.0, 100.0) / 100.0 * usable
    grid_by: dict[datetime, float] = {}
    batt_by: dict[datetime, float] = {}
    for slot in slots:
        solar = solar_by.get(slot, 0.0)
        load = load_by.get(slot, 0.0)
        net = load - solar  # + deficit / − surplus
        headroom_kwh = max(0.0, usable - soc_kwh)
        avail_kwh = max(0.0, soc_kwh - reserve_kwh)
        max_charge_ac = min(max_charge_w, headroom_kwh / eta / _SLOT_H * 1000.0)
        max_discharge_ac = min(max_discharge_w, avail_kwh * eta / _SLOT_H * 1000.0)
        if net > 0:
            battery_w = min(net, max_discharge_ac)
        elif net < 0:
            battery_w = -min(-net, max_charge_ac)
        else:
            battery_w = 0.0
        if battery_w < 0:
            soc_kwh += (-battery_w) * eta * _SLOT_H / 1000.0
        elif battery_w > 0:
            soc_kwh -= battery_w / eta * _SLOT_H / 1000.0
        soc_kwh = _clamp(soc_kwh, 0.0, usable)
        batt_by[slot] = battery_w
        grid_by[slot] = load - solar - battery_w
    return grid_by, batt_by


@dataclass(frozen=True)
class DayVsAuto:
    """Per-request EMS-vs-AUTO comparison for one local day (never persisted)."""

    day: str
    has_sim: bool
    actual_cost_eur: float | None  # measured grid + wear, finance-priced
    auto_cost_eur: float | None  # simulated AUTO grid + wear, finance-priced
    auto_grid_cost_eur: float | None
    auto_battery_cost_eur: float | None
    saved_vs_auto_eur: float | None  # auto_cost − actual_cost (negative stays visible)

    def to_ephemeral_dict(self) -> dict:
        def r2(x: float | None) -> float | None:
            return None if x is None else round(x, 2)

        return {
            "vs_auto_has_sim": self.has_sim,
            "auto_cost_eur": r2(self.auto_cost_eur),
            "auto_grid_cost_eur": r2(self.auto_grid_cost_eur),
            "auto_battery_cost_eur": r2(self.auto_battery_cost_eur),
            "saved_vs_auto_eur": r2(self.saved_vs_auto_eur),
        }


def day_vs_auto(
    raw_rows: list[dict],
    price_rows: list[dict],
    *,
    day: str,
    usable_kwh: float = 10.8,
    max_charge_w: float = 4000.0,
    max_discharge_w: float = 4000.0,
    min_reserve_soc: float = 10.0,
    degradation_eur_per_kwh: float = 0.05,
    export_price_model: str = "net_metering",
    energy_tax_eur_per_kwh: float = 0.13,
    fixed_feed_in_eur_per_kwh: float = 0.01,
    tibber_total_includes_all: bool = False,
    import_fee_eur_per_kwh: float = 0.0,
    export_fee_eur_per_kwh: float = 0.0,
) -> DayVsAuto:
    """Simulate battery AUTO on this day's reconstructed load and compare to measured cost.

    Both legs are priced on the same 15-min slots via `EconomicSnapshot.from_tariff_policy` (same
    import-fee / feed-in boundary as `day_finance`). Returns `has_sim=False` when there are no
    usable samples — never invents a € figure. Pure; the caller must NOT persist the result."""
    load_by, solar_by, batt_meas, soc_by = _slot_series(raw_rows)
    if not load_by:
        return DayVsAuto(day, False, None, None, None, None, None)

    price_by: dict[datetime, float] = {}
    for p in price_rows:
        dt = _parse(p.get("start_ts"))
        if dt is not None:
            price_by[_floor(dt)] = float(p.get("eur_per_kwh", 0.0))

    slots = sorted(load_by)
    start_soc = soc_by[slots[0]] if slots[0] in soc_by else (
        next(iter(soc_by.values())) if soc_by else 50.0
    )

    # Measured meter on the same quarter-hour grid (grid_w already recorded).
    actual_grid = {
        s: load_by[s] - solar_by.get(s, 0.0) - batt_meas.get(s, 0.0) for s in slots
    }
    # Discharge only over priced slots — same wear window as day_finance's `dis_priced`.
    actual_dis = sum(
        max(0.0, batt_meas.get(s, 0.0)) * _SLOT_H / 1000.0
        for s in slots if s in price_by
    )
    actual_grid_cost, actual_wear = _price_grid_cost(
        actual_grid, price_by,
        degradation_eur_per_kwh=degradation_eur_per_kwh,
        discharge_kwh=actual_dis,
        export_price_model=export_price_model,
        energy_tax_eur_per_kwh=energy_tax_eur_per_kwh,
        fixed_feed_in_eur_per_kwh=fixed_feed_in_eur_per_kwh,
        tibber_total_includes_all=tibber_total_includes_all,
        import_fee_eur_per_kwh=import_fee_eur_per_kwh,
        export_fee_eur_per_kwh=export_fee_eur_per_kwh,
    )

    auto_grid, auto_batt = _simulate_auto_battery(
        slots, load_by, solar_by,
        start_soc=start_soc,
        usable_kwh=usable_kwh,
        max_charge_w=max_charge_w,
        max_discharge_w=max_discharge_w,
        min_reserve_soc=min_reserve_soc,
    )
    auto_dis_priced = sum(
        max(0.0, auto_batt.get(s, 0.0)) * _SLOT_H / 1000.0
        for s in slots if s in price_by
    )
    auto_grid_cost, auto_wear = _price_grid_cost(
        auto_grid, price_by,
        degradation_eur_per_kwh=degradation_eur_per_kwh,
        discharge_kwh=auto_dis_priced,
        export_price_model=export_price_model,
        energy_tax_eur_per_kwh=energy_tax_eur_per_kwh,
        fixed_feed_in_eur_per_kwh=fixed_feed_in_eur_per_kwh,
        tibber_total_includes_all=tibber_total_includes_all,
        import_fee_eur_per_kwh=import_fee_eur_per_kwh,
        export_fee_eur_per_kwh=export_fee_eur_per_kwh,
    )

    if actual_grid_cost is None or auto_grid_cost is None:
        return DayVsAuto(day, bool(slots), None, None, None, None, None)

    actual_total = actual_grid_cost + actual_wear
    auto_total = auto_grid_cost + auto_wear
    return DayVsAuto(
        day, True, actual_total, auto_total, auto_grid_cost, auto_wear,
        auto_total - actual_total,
    )


def strip_vs_auto_ephemeral(data: dict) -> dict:
    """Return a shallow copy of a finance day dict without per-request vs-AUTO fields."""
    return {k: v for k, v in data.items() if k not in VS_AUTO_EPHEMERAL_KEYS}


def _rows_by_local_day(
    rows: list[dict], ts_field: str, start: datetime, end: datetime, tz: ZoneInfo,
) -> dict[str, list[dict]]:
    """Group already-fetched rows (spanning [start, end) in `tz`) by their LOCAL calendar day
    (YYYY-MM-DD) — the shared grouping behind `raw_rows_by_local_day`/`price_rows_by_local_day`.

    BACKLOG B-49: lets `/api/finance` fetch a WHOLE window's rows in ONE round trip instead of one
    round trip PER local day (up to 365 for a year view), then slice them back into day_finance()'s
    per-day inputs in memory. Every local day in [start, end) is present as a key (possibly an
    empty list) so a day with zero samples still gets a data-less day_finance() row — matching the
    unbatched per-day fetch's behaviour exactly."""
    by_day: dict[str, list[dict]] = {}
    cur = start
    while cur < end:
        by_day[cur.date().isoformat()] = []
        cur += timedelta(days=1)
    for r in rows:
        dt = _parse(r.get(ts_field))
        if dt is None:
            continue
        local = dt.astimezone(tz)
        if local < start or local >= end:
            continue
        key = local.date().isoformat()
        bucket = by_day.get(key)
        if bucket is not None:
            bucket.append(r)
    return by_day


def raw_rows_by_local_day(
    raw_rows: list[dict], start: datetime, end: datetime, tz: ZoneInfo,
) -> dict[str, list[dict]]:
    """Group already-fetched raw rows (`ts`) by LOCAL calendar day — see `_rows_by_local_day`."""
    return _rows_by_local_day(raw_rows, "ts", start, end, tz)


def price_rows_by_local_day(
    price_rows: list[dict], start: datetime, end: datetime, tz: ZoneInfo,
) -> dict[str, list[dict]]:
    """Group already-fetched price-slot rows (`start_ts`) by LOCAL calendar day — see
    `_rows_by_local_day`."""
    return _rows_by_local_day(price_rows, "start_ts", start, end, tz)
