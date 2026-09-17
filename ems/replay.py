"""Read-only historical simulation of no battery, AUTO, planner and a solar-foresight oracle.

Plans roll every quarter hour using trailing load and forecasts issued before the decision.
Scenario-specific stored energy continues across complete contiguous days; gaps are skipped and
restart from observed storage. Grid bill, estimated wear and an explicitly valued change in
stored energy are reported separately. These are model comparisons, never measured savings.
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from ems.domain import BatteryIntent
from ems.economics import EconomicSnapshot
from ems.perf import timed
from ems.planner.adaptive import AdaptiveConfig
from ems.planner.load_profile import build_load_profile
from ems.planner.rule_based import PlannerConfig
from ems.planner.strategy import HysteresisState, build_plan, resolve_strategy_hysteretic
from ems.planner.summer import SummerConfig
from ems.retrospect import _floor, _mean, _parse
from ems.settings import SETTINGS_BY_KEY, effective_settings, validate_settings
from ems.sources.forecast import ForecastSlot
from ems.sources.prices import PriceSlot
from ems.tariff_history import finance_tariff_kwargs
from ems.tariffs import TariffPeriod
from ems.timeutil import day_slot_count

_DH = 0.25  # hours per 15-min slot (energy = power × this)
_COVERAGE_MIN = 1.0  # incomplete days cannot be simulated without inventing battery state
SCENARIOS = ("no_battery", "auto_selfuse", "planner", "oracle")
# Matches every writer connection's own busy_timeout (storage/history.py, storage/settings.py):
# without it, a `mode=ro` connection has SQLite's default (fail-instantly) timeout, so a replay
# started from a LIVE app (B-69/B-73's web routes) can race the daily maintenance loop's
# `PRAGMA wal_checkpoint(TRUNCATE)` right after boot and get an immediate "database is locked" —
# which `_query` below then silently reads as "no data" rather than retrying. Waiting it out here
# is the fix, not loosening that (deliberately narrow) except clause.
_RO_BUSY_TIMEOUT_MS = 3000


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


# --------------------------------------------------------------------------------------------------
# Config: the full knob set, built from settings defaults + an overridable dict (A/B comparison).
# --------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class ReplayConfig:
    """The complete set of knobs a replay depends on, so two `ReplayConfig`s can be compared
    apples-to-apples. Build it with `from_settings({...})`; every field maps to a `settings.py`
    key so `--set planner.solar_confidence=70` (or a test override dict) changes exactly what the
    live app would change."""

    # Battery model
    usable_kwh: float
    max_charge_w: float
    max_discharge_w: float
    min_reserve_soc: float
    round_trip_efficiency: float
    # Planner economics
    degradation_eur_per_kwh: float
    risk_margin_eur_per_kwh: float
    solar_confidence: float  # 0..1 (settings stores 0..100)
    negative_price_soak: bool
    charge_slots: int
    discharge_slots: int
    # Strategy selection
    strategy: str  # auto | summer | winter
    summer_grid_topup: bool
    summer_max_topup_price: float
    hysteresis_days: int  # §8.4 seasonal-transition damping (0 = instant switch); B-15
    overnight_load_kwh: float
    # Export (feed-in) valuation — mirrors finance.py
    export_price_model: str
    energy_tax_eur_per_kwh: float
    fixed_feed_in_eur_per_kwh: float
    export_fee_eur_per_kwh: float = 0.0
    import_fee_eur_per_kwh: float = 0.0
    tibber_total_includes_all: bool = False
    tariff_periods: tuple[TariffPeriod, ...] = ()
    legacy_before: str | None = None
    bill_optimization_enabled: bool = False
    # Locale (season choice + local-day bucketing). Default = the site tz.
    tz: ZoneInfo = ZoneInfo("Europe/Amsterdam")

    @classmethod
    def from_settings(
        cls, overrides: dict | None = None, *, tz: ZoneInfo | None = None
    ) -> ReplayConfig:
        """Effective settings (defaults overlaid by `overrides`, validated) mapped to typed knobs.
        `overrides` uses the same dotted `settings.py` keys as the UI/CLI."""
        s = effective_settings(overrides or {})
        tariffs = finance_tariff_kwargs({**s, **(overrides or {})})
        return cls(
            usable_kwh=s["battery.usable_kwh"],
            max_charge_w=s["battery.max_charge_w"],
            max_discharge_w=s["battery.max_discharge_w"],
            min_reserve_soc=s["battery.min_reserve_soc"],
            round_trip_efficiency=s["planner.round_trip_efficiency"],
            degradation_eur_per_kwh=s["planner.degradation_eur_per_kwh"],
            risk_margin_eur_per_kwh=s["planner.risk_margin_eur_per_kwh"],
            solar_confidence=s["planner.solar_confidence"] / 100.0,
            negative_price_soak=s["planner.negative_price_soak"],
            charge_slots=int(s["planner.charge_slots"]),
            discharge_slots=int(s["planner.discharge_slots"]),
            strategy=s["strategy.mode"],
            summer_grid_topup=s["strategy.summer_grid_topup"],
            summer_max_topup_price=s["strategy.summer_max_topup_price"],
            hysteresis_days=int(s["strategy.hysteresis_days"]),
            overnight_load_kwh=s["battery.overnight_load_kwh"],
            export_price_model=tariffs["export_price_model"],
            energy_tax_eur_per_kwh=tariffs["energy_tax_eur_per_kwh"],
            fixed_feed_in_eur_per_kwh=tariffs["fixed_feed_in_eur_per_kwh"],
            export_fee_eur_per_kwh=tariffs["export_fee_eur_per_kwh"],
            import_fee_eur_per_kwh=tariffs["import_fee_eur_per_kwh"],
            tibber_total_includes_all=tariffs["tibber_total_includes_all"],
            tariff_periods=tuple(tariffs["tariff_periods"]),
            legacy_before=tariffs["legacy_before"],
            bill_optimization_enabled=s.get("planner.bill_optimization_enabled", False),
            tz=tz or ZoneInfo("Europe/Amsterdam"),
        )


def _winter_cfg(cfg: ReplayConfig) -> PlannerConfig:
    return PlannerConfig(
        round_trip_efficiency=cfg.round_trip_efficiency,
        degradation_eur_per_kwh=cfg.degradation_eur_per_kwh,
        risk_margin_eur_per_kwh=cfg.risk_margin_eur_per_kwh,
        charge_slots=cfg.charge_slots,
        discharge_slots=cfg.discharge_slots,
        negative_price_soak=cfg.negative_price_soak,
        bill_optimization_enabled=cfg.bill_optimization_enabled,
        max_discharge_w=cfg.max_discharge_w,
    )


def _adaptive_cfg(cfg: ReplayConfig) -> AdaptiveConfig:
    return AdaptiveConfig(
        usable_kwh=cfg.usable_kwh,
        reserve_soc_pct=cfg.min_reserve_soc,
        round_trip_efficiency=cfg.round_trip_efficiency,
        max_charge_w=cfg.max_charge_w,
        degradation_eur_per_kwh=cfg.degradation_eur_per_kwh,
        risk_margin_eur_per_kwh=cfg.risk_margin_eur_per_kwh,
        solar_confidence=cfg.solar_confidence,
        negative_price_soak=cfg.negative_price_soak,
        bill_optimization_enabled=cfg.bill_optimization_enabled,
        max_discharge_w=cfg.max_discharge_w,
    )


def _summer_cfg(cfg: ReplayConfig) -> SummerConfig:
    # Only used by build_plan when adaptive_cfg is absent; we always pass adaptive_cfg (so the
    # adaptive charger runs, matching the live app), but a valid SummerConfig is still required.
    usable = max(1e-6, cfg.usable_kwh)
    reserve_kwh = cfg.min_reserve_soc / 100.0 * usable
    target = min(100.0, (reserve_kwh + cfg.overnight_load_kwh) / usable * 100.0)
    return SummerConfig(
        usable_kwh=cfg.usable_kwh,
        target_soc_pct=target,
        round_trip_efficiency=cfg.round_trip_efficiency,
        max_charge_w=cfg.max_charge_w,
        expected_load_w=cfg.overnight_load_kwh * 1000.0 / 12.0,
        solar_confidence=cfg.solar_confidence,
        allow_grid_topup=cfg.summer_grid_topup,
        max_topup_price_eur_per_kwh=cfg.summer_max_topup_price,
        negative_price_soak=cfg.negative_price_soak,
    )


# --------------------------------------------------------------------------------------------------
# Results
# --------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class ScenarioResult:
    """One scenario's outcome over a day. `cost_eur` is None only if no slot had a price (a
    replayable day always has complete price coverage, so in practice it's a number)."""

    cost_eur: float | None
    import_kwh: float
    export_kwh: float
    cycles_kwh: float  # kWh the battery DISCHARGED (throughput on the wear basis; 0 for no_battery)
    reserve_breaches: int  # slots whose end-of-slot SoC is below the reserve floor
    switches: int  # planner intent changes across the day (0 for no_battery / auto)
    initial_stored_kwh: float = 0.0
    final_stored_kwh: float = 0.0
    estimated_wear_eur: float = 0.0
    inventory_adjustment_eur: float = 0.0

    @property
    def net_cost_eur(self) -> float | None:
        if self.cost_eur is None:
            return None
        return self.cost_eur + self.estimated_wear_eur + self.inventory_adjustment_eur

    def to_dict(self) -> dict:
        def r(x: float | None, n: int) -> float | None:
            return None if x is None else round(x, n)

        return {
            "cost_eur": r(self.cost_eur, 4),
            "import_kwh": r(self.import_kwh, 3),
            "export_kwh": r(self.export_kwh, 3),
            "cycles_kwh": r(self.cycles_kwh, 3),
            "reserve_breaches": self.reserve_breaches,
            "switches": self.switches,
            "grid_cost_eur": r(self.cost_eur, 4),
            "estimated_wear_eur": r(self.estimated_wear_eur, 4),
            "inventory_adjustment_eur": r(self.inventory_adjustment_eur, 4),
            "net_cost_eur": r(self.net_cost_eur, 4),
            "initial_stored_kwh": r(self.initial_stored_kwh, 4),
            "final_stored_kwh": r(self.final_stored_kwh, 4),
        }


@dataclass(frozen=True)
class DayResult:
    date: str  # local YYYY-MM-DD
    slots: int  # simulated 15-min slots (the physical day's load coverage)
    data_ok: bool
    skip_reason: str | None
    strategy: str | None  # resolved summer/winter used for the planner scenario (None if skipped)
    scenarios: dict[str, ScenarioResult]  # {} when skipped
    continuity: str = "initialized"

    def to_dict(self) -> dict:
        return {
            "date": self.date,
            "slots": self.slots,
            "data_ok": self.data_ok,
            "skip_reason": self.skip_reason,
            "strategy": self.strategy,
            "continuity": self.continuity,
            "scenarios": {k: v.to_dict() for k, v in self.scenarios.items()},
        }


@dataclass(frozen=True)
class RangeResult:
    days: list[DayResult]
    aggregate: dict
    days_b: list[DayResult] | None = None  # cfg_b per-day results, when an A/B config is given

    def to_dict(self) -> dict:
        out: dict = {
            "days": [d.to_dict() for d in self.days],
            "aggregate": self.aggregate,
        }
        if self.days_b is not None:
            out["days_b"] = [d.to_dict() for d in self.days_b]
        return out


# --------------------------------------------------------------------------------------------------
# Pure core
# --------------------------------------------------------------------------------------------------
def _slot_tariff(cfg: ReplayConfig, slot: datetime, raw_price: float) -> tuple[float, float] | None:
    for period in cfg.tariff_periods:
        if period.contains(slot, timezone=cfg.tz.key):
            value = period.normalize(raw_price)
            return value.import_eur_per_kwh, value.export_eur_per_kwh
    if cfg.tariff_periods and not (
        cfg.legacy_before and slot.astimezone(cfg.tz).date().isoformat() < cfg.legacy_before
    ):
        return None
    price = raw_price if cfg.tibber_total_includes_all else raw_price + cfg.import_fee_eur_per_kwh
    return price, EconomicSnapshot.from_replay_config(
        cfg, raw_price_eur_per_kwh=raw_price
    ).export_credit()


def _simulate(
    slots: list[datetime],
    load_by: dict[datetime, float],
    solar_by: dict[datetime, float],
    price_by: dict[datetime, float],
    *,
    cfg: ReplayConfig,
    start_soc: float,
    battery_enabled: bool,
    intents: dict[datetime, BatteryIntent] | None,
    target_soc: float | None,
    inventory_value_eur_per_kwh: float = 0.0,
) -> ScenarioResult:
    """Forward-simulate one scenario over the ordered `slots`.

    Battery math mirrors `planner.projection.project_energy`: η = √round_trip split per side,
    charge/discharge bounded by inverter power AND SoC head/available room, reserve floor never
    crossed by discharge. Per-slot intent (planner scenario) or plain self-consumption (auto):
      - GRID_CHARGE_TO_TARGET → charge at max power toward `target_soc` (draws extra grid import)
      - HOLD_RESERVE          → idle (neither charge nor discharge)
      - otherwise             → self-consumption: discharge the deficit / soak the surplus

    Cost mirrors `finance.py` exactly: Σ import×price − export×export_value(price) over priced
    slots, under the configured feed-in model."""
    eta = math.sqrt(_clamp(cfg.round_trip_efficiency, 1e-6, 1.0))
    usable = cfg.usable_kwh
    reserve_kwh = _clamp(cfg.min_reserve_soc, 0.0, 100.0) / 100.0 * usable
    soc_kwh = _clamp(start_soc, 0.0, 100.0) / 100.0 * usable
    target_kwh = usable if target_soc is None else _clamp(target_soc, 0.0, 100.0) / 100.0 * usable

    imp = exp = discharge = 0.0
    cost = 0.0
    priced = 0
    breaches = 0

    for slot in slots:
        solar = solar_by.get(slot, 0.0)
        load = load_by.get(slot, 0.0)
        net = load - solar  # + deficit (need power) / − surplus (excess solar)

        if not battery_enabled:
            battery_w = 0.0
        else:
            headroom_kwh = max(0.0, usable - soc_kwh)
            avail_kwh = max(0.0, soc_kwh - reserve_kwh)
            max_charge_ac = min(cfg.max_charge_w, headroom_kwh / eta / _DH * 1000.0)
            max_discharge_ac = min(cfg.max_discharge_w, avail_kwh * eta / _DH * 1000.0)
            intent = intents.get(slot) if intents else BatteryIntent.ALLOW_SELF_CONSUMPTION

            if intent is BatteryIntent.GRID_CHARGE_TO_TARGET:
                room_to_target_ac = max(0.0, target_kwh - soc_kwh) / eta / _DH * 1000.0
                battery_w = -min(max_charge_ac, room_to_target_ac)
            elif intent is BatteryIntent.HOLD_RESERVE:
                battery_w = 0.0
            else:  # ALLOW_SELF_CONSUMPTION / DISCHARGE_FOR_LOAD — track the house
                if net > 0:
                    battery_w = min(net, max_discharge_ac)
                elif net < 0:
                    battery_w = -min(-net, max_charge_ac)
                else:
                    battery_w = 0.0

            if battery_w < 0:  # charging: the pack gains less than the AC drawn
                soc_kwh += (-battery_w) * eta * _DH / 1000.0
            elif battery_w > 0:  # discharging: the pack loses more than the AC delivered
                soc_kwh -= battery_w / eta * _DH / 1000.0
            soc_kwh = _clamp(soc_kwh, 0.0, usable)
            discharge += max(0.0, battery_w) * _DH / 1000.0
            if soc_kwh < reserve_kwh - 1e-9:
                breaches += 1

        grid_w = load - solar - battery_w  # + import / − export
        imp += max(0.0, grid_w) * _DH / 1000.0
        exp += max(0.0, -grid_w) * _DH / 1000.0
        price = price_by.get(slot)
        if price is not None:
            tariff = _slot_tariff(cfg, slot, price)
            if tariff is not None:
                imported, credit = tariff
                cost += (max(0.0, grid_w) * imported - max(0.0, -grid_w) * credit) * _DH / 1000.0
                priced += 1

    return ScenarioResult(
        cost_eur=(cost if priced else None),
        import_kwh=imp,
        export_kwh=exp,
        cycles_kwh=discharge,
        reserve_breaches=breaches,
        switches=0,
        initial_stored_kwh=start_soc / 100.0 * usable if battery_enabled else 0.0,
        final_stored_kwh=soc_kwh if battery_enabled else 0.0,
        estimated_wear_eur=discharge * cfg.degradation_eur_per_kwh,
        inventory_adjustment_eur=(start_soc / 100.0 * usable - soc_kwh)
        * inventory_value_eur_per_kwh
        if battery_enabled
        else 0.0,
    )


def _resolve_strategy(
    cfg: ReplayConfig,
    now: datetime,
    prices: list[PriceSlot],
    forecast: list[ForecastSlot],
    load_by: dict[datetime, float],
    state: HysteresisState | None = None,
) -> tuple[str, HysteresisState]:
    """Resolve cfg.strategy → ('summer'|'winter', new_hysteresis_state), exactly as the app's
    `_resolve_strategy`: an explicit mode is honoured; `auto` decides by forecast surplus + price
    spread (energy review P1.1), then dampened by the seasonal-transition hysteresis (§8.4 / B-15)
    so a replayed range doesn't flap in the shoulder months. `state` carries the pending-switch
    counter across days; None ⇒ a fresh state (instantaneous pick, today's per-day behaviour)."""
    if state is None:
        state = HysteresisState()
    mode = (cfg.strategy or "auto").lower()
    if mode in ("summer", "winter"):
        strat, _why, new_state = resolve_strategy_hysteretic(
            now, mode, cfg.tz, state, hysteresis_days=cfg.hysteresis_days
        )
        return strat, new_state
    surplus = (
        sum(max(0.0, f.p50_w - load_by.get(f.start, 0.0)) * _DH / 1000.0 for f in forecast[:96])
        if forecast
        else None
    )
    ps = [p.eur_per_kwh for p in prices[:96]]
    spread = (max(ps) - min(ps)) if ps else None
    strat, _why, new_state = resolve_strategy_hysteretic(
        now,
        mode,
        cfg.tz,
        state,
        surplus_kwh=surplus,
        price_spread_eur=spread,
        hysteresis_days=cfg.hysteresis_days,
    )
    return strat, new_state


def _eligible_forecast(rows: list[dict], now: datetime) -> list[ForecastSlot]:
    """Use issue-eligible forecasts; legacy date-only issues become available the next day."""
    latest: dict[datetime, tuple[datetime, dict]] = {}
    for row in rows:
        target = _parse(row.get("start", row.get("target_start")))
        issued = _parse(row.get("issued_at"))
        if row.get("source") == "legacy_snapshot":
            issued = issued + timedelta(days=1) if issued else None
        if issued is None:
            day = _parse(row.get("issued_date"))
            issued = day + timedelta(days=1) if day is not None else None
        if target is None or issued is None or issued > now or target < now:
            continue
        values = [
            row.get(new, row.get(old))
            for new, old in (("p10_w", "low_w"), ("p50_w", "expected_w"), ("p90_w", "high_w"))
        ]
        if any(not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0 for v in values):
            continue
        if target not in latest or issued > latest[target][0]:
            latest[target] = (issued, row)
    return [
        ForecastSlot(
            start=target,
            p10_w=float(row.get("p10_w", row.get("low_w", 0))),
            p50_w=float(row.get("p50_w", row.get("expected_w", 0))),
            p90_w=float(row.get("p90_w", row.get("high_w", 0))),
        )
        for target, (_, row) in sorted(latest.items())
    ]


def _load_history(rows: list[dict], before: datetime) -> list[dict]:
    out = []
    for row in rows:
        ts = _parse(row.get("ts"))
        if ts is None or not before - timedelta(days=14) <= ts < before:
            continue
        if row.get("non_ev_load_w") is not None:
            load = row["non_ev_load_w"]
        else:
            try:
                load = (
                    float(row["grid_power_w"])
                    + float(row["solar_power_w"])
                    + float(row["battery_power_w"])
                    - max(0.0, float(row.get("ev_power_w") or 0.0))
                )
            except (KeyError, ValueError, TypeError):
                continue
        out.append({"ts": ts.isoformat(), "non_ev_load_w": load})
    return out


def replay_day(
    raw_rows: list[dict],
    price_rows: list[dict],
    forecast_rows: list[dict],
    *,
    cfg: ReplayConfig,
    hysteresis_box: dict[str, HysteresisState] | None = None,
    history_rows: list[dict] | None = None,
    state_box: dict | None = None,
) -> DayResult:
    """Roll plans forward using only preceding load and issue-eligible solar forecasts.

    Complete load, solar and price slots are required; unknown intervals cannot silently stop the
    battery clock. Each scenario carries its own energy across contiguous days via ``state_box``.
    Historical price publication timestamps are unavailable: today's prices are assumed known;
    next-day prices are admitted only after 15:00 local, explicitly a publication assumption.
    """
    samples: dict[datetime, list[tuple[float, float]]] = defaultdict(list)
    observed_soc_by: dict[datetime, float] = {}
    for row in raw_rows:
        dt = _parse(row.get("ts"))
        if dt is None:
            continue
        try:
            grid, solar, battery = (
                float(row[k]) for k in ("grid_power_w", "solar_power_w", "battery_power_w")
            )
        except (KeyError, TypeError, ValueError):
            continue
        if not all(math.isfinite(v) for v in (grid, solar, battery)):
            continue
        slot = _floor(dt)
        samples[slot].append((max(0.0, grid + solar + battery), max(0.0, solar)))
        soc = row.get("soc_pct")
        if isinstance(soc, (int, float)) and math.isfinite(soc) and 0 <= soc <= 100:
            observed_soc_by[dt] = float(soc)
    price_by = {}
    for row in price_rows:
        dt = _parse(row.get("start_ts"))
        price = row.get("eur_per_kwh")
        if dt is not None and isinstance(price, (float, int)) and math.isfinite(price):
            price_by[_floor(dt)] = float(price)
    keys = set(samples) or set(price_by)
    if not keys:
        return DayResult("unknown", 0, False, "no data", None, {})
    day = min(keys).astimezone(cfg.tz).date()
    date_str = day.isoformat()
    begin, end = (_parse(v) for v in _day_window(day, cfg.tz))
    slots = [begin + timedelta(minutes=15 * i) for i in range(day_slot_count(day, cfg.tz))]
    for name, values in (("load", samples), ("price", price_by)):
        coverage = sum(slot in values for slot in slots) / len(slots)
        if coverage < _COVERAGE_MIN:
            return DayResult(
                date_str,
                len(samples),
                False,
                f"{name} coverage {coverage:.0%} < 100%; incomplete day skipped",
                None,
                {},
                "gap",
            )
    if any(_slot_tariff(cfg, slot, price_by[slot]) is None for slot in slots):
        return DayResult(
            date_str, len(slots), False, "tariff period coverage incomplete", None, {}, "gap"
        )
    state = state_box if state_box is not None else {}
    continuous = state.get("end") == begin.isoformat()
    continuity = "continued" if continuous else ("reset_after_gap" if state else "initialized")
    observations = [ts for ts in observed_soc_by if ts < begin + timedelta(minutes=15)]
    if not continuous and not observations:
        return DayResult(
            date_str, len(slots), False, "initial battery state unavailable", None, {}, "gap"
        )
    observed_soc = observed_soc_by[min(observations)] if observations else 0.0
    load_by = {slot: _mean([pair[0] for pair in samples[slot]]) for slot in slots}
    solar_by = {slot: _mean([pair[1] for pair in samples[slot]]) for slot in slots}
    starts = state.get("soc", {}) if continuous else {}
    eta = math.sqrt(cfg.round_trip_efficiency)
    # One fixed DC-energy valuation across the comparison makes daily adjustments telescope.
    value = state.setdefault(
        "inventory_value",
        max(0.0, _mean([_slot_tariff(cfg, t, price_by[t])[0] for t in slots])) * eta,
    )
    result = {}
    for scenario in ("no_battery", "auto_selfuse"):
        result[scenario] = _simulate(
            slots,
            load_by,
            solar_by,
            price_by,
            cfg=cfg,
            start_soc=starts.get(scenario, observed_soc),
            battery_enabled=scenario != "no_battery",
            intents=None,
            target_soc=None,
            inventory_value_eur_per_kwh=value,
        )
    pieces = {name: [] for name in ("planner", "oracle")}
    scenario_soc = {name: starts.get(name, observed_soc) for name in pieces}
    previous_intent = {}
    switches = dict.fromkeys(pieces, 0)
    hyst = hysteresis_box.get("state") if hysteresis_box is not None else None
    strategy = None
    history = history_rows or []
    for now in slots:
        local = now.astimezone(cfg.tz)
        last_known_day = local.date() + timedelta(days=int(local.hour >= 15))
        prices = [
            PriceSlot(start=t, eur_per_kwh=_slot_tariff(cfg, t, price_by[t])[0])
            for t in sorted(price_by)
            if _slot_tariff(cfg, t, price_by[t]) is not None
            if now <= t < now + timedelta(hours=24)
            and t.astimezone(cfg.tz).date() <= last_known_day
        ]
        profile = build_load_profile(
            _load_history(history + raw_rows, now),
            cfg.tz,
            enhanced=cfg.bill_optimization_enabled,
            as_of=now,
        )
        predicted = {p.start: profile.expected_w(p.start) for p in prices}
        forecast = _eligible_forecast(forecast_rows, now)
        strategy, hyst = _resolve_strategy(cfg, now, prices, forecast, predicted, hyst)
        for name in pieces:
            solar_fc = (
                forecast
                if name == "planner"
                else [
                    ForecastSlot(start=t, p10_w=w, p50_w=w, p90_w=w)
                    for t, w in solar_by.items()
                    if t >= now
                ]
            )
            plan = build_plan(
                strategy,
                prices=prices,
                forecast=solar_fc,
                now=now,
                soc_pct=scenario_soc[name],
                winter_cfg=_winter_cfg(cfg),
                summer_cfg=_summer_cfg(cfg),
                load_w_by=predicted,
                adaptive_cfg=_adaptive_cfg(cfg),
                export_price_by={
                    p.start: _slot_tariff(cfg, p.start, price_by[p.start])[1] for p in prices
                },
            )
            current = plan.intent_at(now)
            intent = current.intent if current else BatteryIntent.ALLOW_SELF_CONSUMPTION
            if name in previous_intent and previous_intent[name] != intent:
                switches[name] += 1
            previous_intent[name] = intent
            step = _simulate(
                [now],
                load_by,
                solar_by,
                price_by,
                cfg=cfg,
                start_soc=scenario_soc[name],
                battery_enabled=True,
                intents={now: intent},
                target_soc=current.target_soc if current else None,
                inventory_value_eur_per_kwh=value,
            )
            pieces[name].append(step)
            scenario_soc[name] = step.final_stored_kwh / cfg.usable_kwh * 100
    for name, steps in pieces.items():
        result[name] = ScenarioResult(
            cost_eur=sum(s.cost_eur for s in steps),
            import_kwh=sum(s.import_kwh for s in steps),
            export_kwh=sum(s.export_kwh for s in steps),
            cycles_kwh=sum(s.cycles_kwh for s in steps),
            reserve_breaches=sum(s.reserve_breaches for s in steps),
            switches=switches[name],
            initial_stored_kwh=steps[0].initial_stored_kwh,
            final_stored_kwh=steps[-1].final_stored_kwh,
            estimated_wear_eur=sum(s.estimated_wear_eur for s in steps),
            inventory_adjustment_eur=sum(s.inventory_adjustment_eur for s in steps),
        )
    if hysteresis_box is not None:
        hysteresis_box["state"] = hyst
    state.update(
        end=end.isoformat(),
        soc={name: r.final_stored_kwh / cfg.usable_kwh * 100 for name, r in result.items()},
    )
    return DayResult(date_str, len(slots), True, None, strategy, result, continuity)


# --------------------------------------------------------------------------------------------------
# Read-only DB access (this module NEVER writes history) + range replay
# --------------------------------------------------------------------------------------------------
def _ro_conn(db_path: str) -> sqlite3.Connection:
    """A strictly READ-ONLY connection (mode=ro URI): any write raises. Guards the promise that a
    replay can never mutate the live history DB. `busy_timeout` lets a transient writer lock
    (e.g. the maintenance loop's WAL checkpoint) be waited out instead of failing instantly."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.execute(f"PRAGMA busy_timeout={_RO_BUSY_TIMEOUT_MS}")
    return conn


def _query(conn: sqlite3.Connection, sql: str, params: tuple) -> list[dict]:
    try:
        cur = conn.execute(sql, params)
    except sqlite3.OperationalError:
        return []  # table absent (an older-schema DB has no price_slots/forecast_snapshots)
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, row, strict=False)) for row in cur.fetchall()]


def _day_window(d: date, tz: ZoneInfo) -> tuple[str, str]:
    """[local-midnight, next-local-midnight) as UTC-ISO bounds (stored ts are UTC-ISO)."""
    start = datetime(d.year, d.month, d.day, tzinfo=tz).astimezone(UTC).isoformat()
    nxt = d + timedelta(days=1)
    end = datetime(nxt.year, nxt.month, nxt.day, tzinfo=tz).astimezone(UTC).isoformat()
    return start, end


def replay_range(
    store: object,
    days: int,
    cfg: ReplayConfig,
    cfg_b: ReplayConfig | None = None,
) -> RangeResult:
    """Replay the most recent `days` complete local days ending at the latest recorded sample.

    `store` may be a `HistoryStore` (its `.db_path` is used) or a path string. The DB is opened
    READ-ONLY. Returns per-day results + an aggregate (scenario totals, planner-vs-auto delta, and
    — when `cfg_b` is given — the same days under the second config plus its cost delta)."""
    with timed("replay.run"):
        db_path = getattr(store, "db_path", None) or str(store)
        conn = _ro_conn(db_path)
        try:
            row = conn.execute("SELECT max(ts) FROM raw_samples").fetchone()
            latest = row[0] if row else None
            if latest is None:
                return RangeResult([], _aggregate([], None), None if cfg_b is None else [])
            last_dt = _parse(latest)
            last_date = (
                last_dt.astimezone(cfg.tz).date() if last_dt else datetime.now(cfg.tz).date()
            )
            dates = [last_date - timedelta(days=i) for i in range(max(1, days))][::-1]

            results: list[DayResult] = []
            results_b: list[DayResult] | None = [] if cfg_b is not None else None
            # One hysteresis memory per config, carried across the days in order (§8.4 / B-15) so
            # the replayed `auto` season is dampened just like the live app. A/B get independent
            # counters.
            hyst_box: dict[str, HysteresisState] = {"state": HysteresisState()}
            hyst_box_b: dict[str, HysteresisState] = {"state": HysteresisState()}
            state_box: dict = {}
            state_box_b: dict = {}
            for d in dates:
                start_iso, end_iso = _day_window(d, cfg.tz)
                _, horizon_end = _day_window(d + timedelta(days=1), cfg.tz)
                history_start, _ = _day_window(d - timedelta(days=14), cfg.tz)
                history = _query(
                    conn,
                    "SELECT ts, non_ev_load_w FROM derived_samples "
                    "WHERE ts >= ? AND ts < ? ORDER BY ts",
                    (history_start, start_iso),
                )
                raw = _query(
                    conn,
                    "SELECT ts, grid_power_w, solar_power_w, battery_power_w, ev_power_w, soc_pct "
                    "FROM raw_samples WHERE ts >= ? AND ts < ? ORDER BY rowid ASC",
                    (start_iso, end_iso),
                )
                prices = _query(
                    conn,
                    "SELECT start_ts, eur_per_kwh FROM price_slots "
                    "WHERE start_ts >= ? AND start_ts < ? ORDER BY start_ts ASC",
                    (start_iso, horizon_end),
                )
                forecast = _query(
                    conn,
                    "SELECT issued_date, start, p10_w, p50_w, p90_w FROM forecast_snapshots "
                    "WHERE start >= ? AND start < ? ORDER BY issued_date ASC, start ASC",
                    (start_iso, end_iso),
                )
                ledger = _query(
                    conn,
                    "SELECT issued_at, target_start, low_w, expected_w, high_w, quality, source "
                    "FROM forecast_ledger WHERE kind = 'solar' AND target_start >= ? "
                    "AND target_start < ? ORDER BY issued_at",
                    (start_iso, horizon_end),
                )
                forecast.extend(ledger)
                results.append(
                    replay_day(
                        raw,
                        prices,
                        forecast,
                        cfg=cfg,
                        hysteresis_box=hyst_box,
                        history_rows=history,
                        state_box=state_box,
                    )
                )
                if results_b is not None and cfg_b is not None:
                    results_b.append(
                        replay_day(
                            raw,
                            prices,
                            forecast,
                            cfg=cfg_b,
                            hysteresis_box=hyst_box_b,
                            history_rows=history,
                            state_box=state_box_b,
                        )
                    )
        finally:
            conn.close()

        return RangeResult(results, _aggregate(results, results_b), results_b)


def _sum_cost(days: list[DayResult], scenario: str) -> float:
    return sum(
        d.scenarios[scenario].cost_eur
        for d in days
        if d.data_ok and d.scenarios.get(scenario) and d.scenarios[scenario].cost_eur is not None
    )


def _aggregate(days: list[DayResult], days_b: list[DayResult] | None) -> dict:
    ok = [d for d in days if d.data_ok]
    nb = _sum_cost(ok, "no_battery")
    auto = _sum_cost(ok, "auto_selfuse")
    planner = _sum_cost(ok, "planner")
    oracle = _sum_cost(ok, "oracle")
    agg = {
        "days_replayed": len(ok),
        "days_skipped": len(days) - len(ok),
        "no_battery_cost_eur": round(nb, 4),
        "auto_cost_eur": round(auto, 4),
        "planner_cost_eur": round(planner, 4),
        "oracle_cost_eur": round(oracle, 4),
        # + = the planner is CHEAPER than the vendor-auto floor / than no battery at all.
        "planner_vs_auto_eur": round(auto - planner, 4),
        "planner_vs_no_battery_eur": round(nb - planner, 4),
        # + = the € a PERFECT SOLAR forecast would have saved beyond the planner (headroom /
        # ceiling for solar-forecast + ML work). ~0 means better solar forecasting can't help much.
        "oracle_headroom_eur": round(planner - oracle, 4),
        "reserve_breaches": sum(
            d.scenarios["planner"].reserve_breaches for d in ok if "planner" in d.scenarios
        ),
        "switches": sum(d.scenarios["planner"].switches for d in ok if "planner" in d.scenarios),
    }
    for name, prefix in (
        ("no_battery", "no_battery"),
        ("auto_selfuse", "auto"),
        ("planner", "planner"),
        ("oracle", "oracle"),
    ):
        outcomes = [d.scenarios[name] for d in ok if name in d.scenarios]
        for field in ("estimated_wear_eur", "inventory_adjustment_eur", "net_cost_eur"):
            agg[f"{prefix}_{field}"] = round(sum(getattr(r, field) or 0 for r in outcomes), 4)
    agg["planner_vs_auto_net_eur"] = round(
        agg["auto_net_cost_eur"] - agg["planner_net_cost_eur"], 4
    )
    agg["planner_vs_no_battery_net_eur"] = round(nb - agg["planner_net_cost_eur"], 4)
    agg["continuity_resets"] = sum(d.continuity == "reset_after_gap" for d in ok)
    agg["simulation"] = True
    agg["limitations"] = [
        "Simulated results, not measured savings; quarter-hour mode execution omits live dwell "
        "and write caps.",
        "Price issuance timestamps are unavailable: next-day prices assumed available "
        "after 15:00 local.",
        "Date-only legacy forecasts become eligible the following day; "
        "missing forecasts remain absent.",
        "Stored DC energy valued at the first replayed day's nonnegative mean import price "
        "times one-way efficiency.",
        "Oracle changes only solar foresight; its observed advantage is not a guaranteed "
        "upper bound.",
    ]
    if days_b is not None:
        planner_b = _sum_cost([d for d in days_b if d.data_ok], "planner")
        agg["cfg_b"] = {
            "planner_cost_eur": round(planner_b, 4),
            # + = config B's planner is CHEAPER than config A's.
            "delta_vs_a_eur": round(planner - planner_b, 4),
            "planner_net_cost_eur": round(
                sum(d.scenarios["planner"].net_cost_eur or 0 for d in days_b if d.data_ok), 4
            ),
            "planner_estimated_wear_eur": round(
                sum(d.scenarios["planner"].estimated_wear_eur for d in days_b if d.data_ok), 4
            ),
            "planner_inventory_adjustment_eur": round(
                sum(d.scenarios["planner"].inventory_adjustment_eur for d in days_b if d.data_ok), 4
            ),
        }
        agg["cfg_b"]["net_delta_vs_a_eur"] = round(
            agg["planner_net_cost_eur"] - agg["cfg_b"]["planner_net_cost_eur"], 4
        )
    return agg


# --------------------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------------------
def _coerce_override(key: str, raw: str) -> object:
    field = SETTINGS_BY_KEY[key]
    if field.type == "bool":
        return raw.strip().lower() in ("1", "true", "yes", "on")
    if field.type == "int":
        return int(raw)
    if field.type == "number":
        return float(raw)
    return raw


def _parse_overrides(pairs: list[str]) -> dict:
    """`key=value` strings → a validated settings-override dict. Exits on an unknown/invalid key."""
    raw: dict = {}
    for pair in pairs:
        if "=" not in pair:
            raise SystemExit(f"--set expects key=value, got: {pair!r}")
        key, value = pair.split("=", 1)
        key = key.strip()
        if key not in SETTINGS_BY_KEY:
            raise SystemExit(f"--set: unknown setting {key!r}")
        try:
            raw[key] = _coerce_override(key, value)
        except ValueError:
            raise SystemExit(f"--set: bad value for {key!r}: {value!r}") from None
    clean, errors = validate_settings(raw)
    if errors:
        msgs = "; ".join(f"{k}: {v}" for k, v in errors.items())
        raise SystemExit(f"--set validation failed: {msgs}")
    return clean


def _fmt_eur(x: float | None) -> str:
    return "   --" if x is None else f"{x:7.3f}"


def format_table(result: RangeResult) -> str:
    """A compact per-day table + an aggregate line and an annualized value-gap summary. Columns:
    date | no-batt € | auto € | planner € | oracle € | Δ vs auto | breaches | notes. Δ = auto −
    planner (planner's saving over the vendor-auto floor); oracle = planner + perfect solar."""
    lines = [
        f"{'date':<12}{'no-batt €':>10}{'auto €':>9}{'planner €':>11}{'oracle €':>10}"
        f"{'Δ vs auto':>11}{'breach':>8}  notes",
        "-" * 88,
    ]
    for d in result.days:
        if not d.data_ok:
            lines.append(
                f"{d.date:<12}{'--':>10}{'--':>9}{'--':>11}{'--':>10}{'--':>11}{'--':>8}  "
                f"skipped: {d.skip_reason}"
            )
            continue
        nb = d.scenarios["no_battery"].cost_eur
        auto = d.scenarios["auto_selfuse"].cost_eur
        pl = d.scenarios["planner"].cost_eur
        orc = d.scenarios["oracle"].cost_eur
        delta = (auto - pl) if (auto is not None and pl is not None) else None
        breaches = d.scenarios["planner"].reserve_breaches
        lines.append(
            f"{d.date:<12}{_fmt_eur(nb):>10}{_fmt_eur(auto):>9}{_fmt_eur(pl):>11}"
            f"{_fmt_eur(orc):>10}{_fmt_eur(delta):>11}{breaches:>8}  {d.strategy}"
        )
    a = result.aggregate
    lines.append("-" * 88)
    lines.append(
        f"{'TOTAL':<12}{_fmt_eur(a['no_battery_cost_eur']):>10}"
        f"{_fmt_eur(a['auto_cost_eur']):>9}{_fmt_eur(a['planner_cost_eur']):>11}"
        f"{_fmt_eur(a['oracle_cost_eur']):>10}{_fmt_eur(a['planner_vs_auto_eur']):>11}"
        f"{a['reserve_breaches']:>8}  {a['days_replayed']} days, {a['switches']} switches"
    )
    if "cfg_b" in a:
        b = a["cfg_b"]
        lines.append(
            f"config B planner € {b['planner_cost_eur']:.3f}  "
            f"(Δ vs A {b['delta_vs_a_eur']:+.3f}; + = B cheaper)"
        )
    if a["days_replayed"]:
        lines += [
            "",
            "Simulated comparison over recorded days (not measured savings):",
            f"  net benefit versus AUTO: €{a['planner_vs_auto_net_eur']:+.3f}",
            "  Includes estimated wear and the change in stored-energy value.",
            "  Solar oracle is a foresight sensitivity, not a guaranteed savings ceiling.",
        ]
    lines.extend(f"  Assumption: {note}" for note in a.get("limitations", []))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m ems.replay",
        description="Replay recorded days through the planner (read-only) and compare "
        "no-battery / vendor-auto / planner cost.",
    )
    ap.add_argument("--days", type=int, default=14, help="number of recent days to replay")
    ap.add_argument("--db", default="ems/data/ems.sqlite", help="SQLite history DB (read-only)")
    ap.add_argument("--tz", default="Europe/Amsterdam", help="site timezone for local days")
    ap.add_argument(
        "--set",
        action="append",
        default=[],
        metavar="key=value",
        help="override a setting (repeatable), e.g. --set planner.solar_confidence=70",
    )
    ap.add_argument("--json", action="store_true", help="dump full results as JSON")
    args = ap.parse_args(argv)

    overrides = _parse_overrides(args.set)
    cfg = ReplayConfig.from_settings(overrides, tz=ZoneInfo(args.tz))
    result = replay_range(args.db, args.days, cfg)
    if args.json:
        print(json.dumps(result.to_dict(), indent=2))
    else:
        print(format_table(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
