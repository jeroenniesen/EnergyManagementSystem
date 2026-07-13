"""Historical replay optimization suite (backlog B-77): the engine that makes every future
planner change measurable.

Replay recorded days through THREE scenarios on the SAME actual weather + prices and compares
what each would have cost:

  a. **no_battery**    — the counterfactual meter with no storage at all (grid = load − solar).
  b. **auto_selfuse**  — the vendor's self-consumption behaviour (soak solar surplus, discharge
                         to residual load), the "no EMS" floor the system must never be worse than.
  c. **planner**       — the app's OWN plan (`strategy.build_plan`, exactly as the control loop
                         builds it) applied slot by slot: grid-charge toward the target, hold idle,
                         self-consume otherwise.

The plan is built at day-start from that day's STORED prices + STORED (day-ahead) solar forecast —
what the planner actually knew — while the simulation runs against the day's ACTUAL reconstructed
load + ACTUAL solar. That asymmetry is deliberate: it's a faithful replay (plan against forecast,
reality happens), not a perfect-foresight fantasy.

The battery model is shared across scenarios and mirrors `planner.projection` exactly: round-trip
efficiency split as √η per side, a hard reserve floor discharge never crosses, and per-slot power
bounded by both the inverter limit and the SoC head/available room. Cost mirrors `finance.py`:
Σ import×price − export×export_value(price) over priced slots, under the configured feed-in model.

Everything here is PURE except the DB reader, which opens the SQLite file **read-only** (`mode=ro`
URI) — this module must never write history. Run it: `uv run python -m ems.replay --days 14`.
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
from ems.planner.adaptive import AdaptiveConfig
from ems.planner.economics import export_value
from ems.planner.rule_based import PlannerConfig
from ems.planner.strategy import build_plan, select_strategy_with_reason
from ems.planner.summer import SummerConfig
from ems.retrospect import _floor, _mean, _parse
from ems.settings import SETTINGS_BY_KEY, effective_settings, validate_settings
from ems.sources.forecast import ForecastSlot
from ems.sources.prices import PriceSlot
from ems.timeutil import day_slot_count

_DH = 0.25  # hours per 15-min slot (energy = power × this)
_COVERAGE_MIN = 0.80  # a day needs ≥80% slot coverage of BOTH load and prices to be replayable
SCENARIOS = ("no_battery", "auto_selfuse", "planner")
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
    overnight_load_kwh: float
    # Export (feed-in) valuation — mirrors finance.py
    export_price_model: str
    energy_tax_eur_per_kwh: float
    fixed_feed_in_eur_per_kwh: float
    # Locale (season choice + local-day bucketing). Default = the site tz.
    tz: ZoneInfo = ZoneInfo("Europe/Amsterdam")

    @classmethod
    def from_settings(
        cls, overrides: dict | None = None, *, tz: ZoneInfo | None = None
    ) -> ReplayConfig:
        """Effective settings (defaults overlaid by `overrides`, validated) mapped to typed knobs.
        `overrides` uses the same dotted `settings.py` keys as the UI/CLI."""
        s = effective_settings(overrides or {})
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
            overnight_load_kwh=s["battery.overnight_load_kwh"],
            export_price_model=s["prices.export_price_model"],
            energy_tax_eur_per_kwh=s["prices.energy_tax_eur_per_kwh"],
            fixed_feed_in_eur_per_kwh=s["prices.fixed_feed_in_eur_per_kwh"],
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
    replayable day always has ≥80% price coverage, so in practice it's a number)."""

    cost_eur: float | None
    import_kwh: float
    export_kwh: float
    cycles_kwh: float  # kWh the battery DISCHARGED (throughput on the wear basis; 0 for no_battery)
    reserve_breaches: int  # slots whose end-of-slot SoC is below the reserve floor
    switches: int  # planner intent changes across the day (0 for no_battery / auto)

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
        }


@dataclass(frozen=True)
class DayResult:
    date: str  # local YYYY-MM-DD
    slots: int  # simulated 15-min slots (the physical day's load coverage)
    data_ok: bool
    skip_reason: str | None
    strategy: str | None  # resolved summer/winter used for the planner scenario (None if skipped)
    scenarios: dict[str, ScenarioResult]  # {} when skipped

    def to_dict(self) -> dict:
        return {
            "date": self.date,
            "slots": self.slots,
            "data_ok": self.data_ok,
            "skip_reason": self.skip_reason,
            "strategy": self.strategy,
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
            credit = export_value(
                price,
                model=cfg.export_price_model,
                energy_tax_eur_per_kwh=cfg.energy_tax_eur_per_kwh,
                fixed_feed_in_eur_per_kwh=cfg.fixed_feed_in_eur_per_kwh,
            )
            cost += (max(0.0, grid_w) * price - max(0.0, -grid_w) * credit) * _DH / 1000.0
            priced += 1

    return ScenarioResult(
        cost_eur=(cost if priced else None),
        import_kwh=imp,
        export_kwh=exp,
        cycles_kwh=discharge,
        reserve_breaches=breaches,
        switches=0,
    )


def _resolve_strategy(
    cfg: ReplayConfig,
    now: datetime,
    prices: list[PriceSlot],
    forecast: list[ForecastSlot],
    load_by: dict[datetime, float],
) -> str:
    """Resolve cfg.strategy → 'summer' | 'winter', exactly as the app's `_resolve_strategy`: an
    explicit mode is honoured; `auto` decides by forecast surplus + price spread (energy review
    P1.1), falling back to the calendar season when those inputs are absent."""
    mode = (cfg.strategy or "auto").lower()
    if mode in ("summer", "winter"):
        return select_strategy_with_reason(now, mode, cfg.tz)[0]
    surplus = sum(
        max(0.0, f.p50_w - load_by.get(f.start, 0.0)) * _DH / 1000.0 for f in forecast[:96]
    ) if forecast else None
    ps = [p.eur_per_kwh for p in prices[:96]]
    spread = (max(ps) - min(ps)) if ps else None
    return select_strategy_with_reason(
        now, mode, cfg.tz, surplus_kwh=surplus, price_spread_eur=spread
    )[0]


def replay_day(
    raw_rows: list[dict],
    price_rows: list[dict],
    forecast_rows: list[dict],
    *,
    cfg: ReplayConfig,
) -> DayResult:
    """Replay ONE day (rows already windowed to the local day) through all three scenarios.

    Reconstructs load = grid + solar + battery per 15-min slot (SPEC §4 / `load_model`), reads the
    stored price + day-ahead forecast, then simulates no_battery / auto_selfuse / planner sharing
    one battery model. A day with <80% slot coverage of load OR prices is not replayed — it returns
    `data_ok=False` with a `skip_reason` and empty scenarios (never a fabricated number)."""
    tz = cfg.tz

    # --- per-slot series from raw samples (mean power per 15-min slot) ---
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
        solar_l[s].append(float(r.get("solar_power_w", 0.0)))
        batt_by[s].append(float(r.get("battery_power_w", 0.0)))
        if r.get("soc_pct") is not None:
            soc_by[s].append(float(r["soc_pct"]))

    load_by: dict[datetime, float] = {}
    solar_by: dict[datetime, float] = {}
    for s in grid_by:
        solar = _mean(solar_l[s])
        # house_load = grid + solar + battery (SPEC §4.2 / load_model.reconstruct). The RECORDED
        # battery only reconstructs the historical load; the replay then simulates a fresh battery.
        load_by[s] = _mean(grid_by[s]) + solar + _mean(batt_by[s])
        solar_by[s] = solar

    price_by: dict[datetime, float] = {}
    for p in price_rows:
        dt = _parse(p.get("start_ts"))
        if dt is not None:
            price_by[_floor(dt)] = float(p.get("eur_per_kwh", 0.0))

    # Day-ahead forecast: keep the FIRST snapshot per slot (forecast rows arrive issued_date-ASC,
    # so the earliest issue — the day-ahead — wins over a later same-day nowcast).
    fc_slots: list[ForecastSlot] = []
    seen: set[datetime] = set()
    for f in forecast_rows:
        dt = _parse(f.get("start"))
        if dt is None or dt in seen:
            continue
        seen.add(dt)
        fc_slots.append(ForecastSlot(
            start=dt, p10_w=float(f.get("p10_w", 0.0)),
            p50_w=float(f.get("p50_w", 0.0)), p90_w=float(f.get("p90_w", 0.0)),
        ))
    fc_slots.sort(key=lambda f: f.start)

    all_keys = set(load_by) | set(price_by) | {f.start for f in fc_slots}
    if not all_keys:
        return DayResult("unknown", 0, False, "no data", None, {})

    first_slot = min(all_keys)
    day_date = first_slot.astimezone(tz).date()
    date_str = day_date.isoformat()
    expected = day_slot_count(day_date, tz) or 96  # DST-aware slot count (96/92/100)
    load_cov = len(load_by) / expected
    price_cov = len(price_by) / expected
    if load_cov < _COVERAGE_MIN:
        return DayResult(
            date_str, len(load_by), False,
            f"load coverage {load_cov:.0%} < {_COVERAGE_MIN:.0%}", None, {})
    if price_cov < _COVERAGE_MIN:
        return DayResult(
            date_str, len(load_by), False,
            f"price coverage {price_cov:.0%} < {_COVERAGE_MIN:.0%}", None, {})

    slots = sorted(load_by)
    start_soc = _mean(soc_by[min(soc_by)]) if soc_by else 0.0  # first recorded SoC of the day

    # (a) no_battery: grid = load − solar every slot.
    no_battery = _simulate(
        slots, load_by, solar_by, price_by, cfg=cfg, start_soc=start_soc,
        battery_enabled=False, intents=None, target_soc=None)

    # (b) auto_selfuse: vendor self-consumption (soak surplus / discharge deficit).
    auto = _simulate(
        slots, load_by, solar_by, price_by, cfg=cfg, start_soc=start_soc,
        battery_enabled=True, intents=None, target_soc=None)

    # (c) planner: build the app's plan at day-start from stored prices + day-ahead forecast, then
    # simulate it against the ACTUAL load + solar. Load fed to the planner is the day's actual
    # reconstructed load (a faithful proxy for the learned profile it would have used).
    now = first_slot
    prices = [PriceSlot(start=s, eur_per_kwh=price_by[s]) for s in sorted(price_by)]
    strategy = _resolve_strategy(cfg, now, prices, fc_slots, load_by)
    plan = build_plan(
        strategy, prices=prices, forecast=fc_slots, now=now, soc_pct=start_soc,
        winter_cfg=_winter_cfg(cfg), summer_cfg=_summer_cfg(cfg),
        load_w_by=load_by, adaptive_cfg=_adaptive_cfg(cfg))
    intents: dict[datetime, BatteryIntent] = {}
    for s in slots:
        ps = plan.intent_at(s)
        intents[s] = ps.intent if ps is not None else BatteryIntent.ALLOW_SELF_CONSUMPTION
    seq = [intents[s] for s in slots]
    switches = sum(1 for a, b in zip(seq, seq[1:], strict=False) if a != b)
    planner = _simulate(
        slots, load_by, solar_by, price_by, cfg=cfg, start_soc=start_soc,
        battery_enabled=True, intents=intents, target_soc=plan.target_soc)
    planner = ScenarioResult(
        planner.cost_eur, planner.import_kwh, planner.export_kwh, planner.cycles_kwh,
        planner.reserve_breaches, switches)

    return DayResult(
        date_str, len(slots), True, None, strategy,
        {"no_battery": no_battery, "auto_selfuse": auto, "planner": planner})


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
    db_path = getattr(store, "db_path", None) or str(store)
    conn = _ro_conn(db_path)
    try:
        row = conn.execute("SELECT max(ts) FROM raw_samples").fetchone()
        latest = row[0] if row else None
        if latest is None:
            return RangeResult([], _aggregate([], None), None if cfg_b is None else [])
        last_dt = _parse(latest)
        last_date = last_dt.astimezone(cfg.tz).date() if last_dt else datetime.now(cfg.tz).date()
        dates = [last_date - timedelta(days=i) for i in range(max(1, days))][::-1]

        results: list[DayResult] = []
        results_b: list[DayResult] | None = [] if cfg_b is not None else None
        for d in dates:
            start_iso, end_iso = _day_window(d, cfg.tz)
            raw = _query(
                conn,
                "SELECT ts, grid_power_w, solar_power_w, battery_power_w, ev_power_w, soc_pct "
                "FROM raw_samples WHERE ts >= ? AND ts < ? ORDER BY rowid ASC",
                (start_iso, end_iso))
            prices = _query(
                conn,
                "SELECT start_ts, eur_per_kwh FROM price_slots "
                "WHERE start_ts >= ? AND start_ts < ? ORDER BY start_ts ASC",
                (start_iso, end_iso))
            forecast = _query(
                conn,
                "SELECT issued_date, start, p10_w, p50_w, p90_w FROM forecast_snapshots "
                "WHERE start >= ? AND start < ? ORDER BY issued_date ASC, start ASC",
                (start_iso, end_iso))
            results.append(replay_day(raw, prices, forecast, cfg=cfg))
            if results_b is not None and cfg_b is not None:
                results_b.append(replay_day(raw, prices, forecast, cfg=cfg_b))
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
    agg = {
        "days_replayed": len(ok),
        "days_skipped": len(days) - len(ok),
        "no_battery_cost_eur": round(nb, 4),
        "auto_cost_eur": round(auto, 4),
        "planner_cost_eur": round(planner, 4),
        # + = the planner is CHEAPER than the vendor-auto floor / than no battery at all.
        "planner_vs_auto_eur": round(auto - planner, 4),
        "planner_vs_no_battery_eur": round(nb - planner, 4),
        "reserve_breaches": sum(
            d.scenarios["planner"].reserve_breaches for d in ok if "planner" in d.scenarios),
        "switches": sum(
            d.scenarios["planner"].switches for d in ok if "planner" in d.scenarios),
    }
    if days_b is not None:
        planner_b = _sum_cost([d for d in days_b if d.data_ok], "planner")
        agg["cfg_b"] = {
            "planner_cost_eur": round(planner_b, 4),
            # + = config B's planner is CHEAPER than config A's.
            "delta_vs_a_eur": round(planner - planner_b, 4),
        }
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
    """A compact per-day table + an aggregate line (date | no-batt € | auto € | planner € | Δ |
    breaches | notes). Δ = auto − planner (what the planner saved over the vendor-auto floor)."""
    lines = [
        f"{'date':<12}{'no-batt €':>10}{'auto €':>9}{'planner €':>11}"
        f"{'Δ vs auto':>11}{'breach':>8}  notes",
        "-" * 78,
    ]
    for d in result.days:
        if not d.data_ok:
            lines.append(f"{d.date:<12}{'--':>10}{'--':>9}{'--':>11}{'--':>11}{'--':>8}  "
                         f"skipped: {d.skip_reason}")
            continue
        nb = d.scenarios["no_battery"].cost_eur
        auto = d.scenarios["auto_selfuse"].cost_eur
        pl = d.scenarios["planner"].cost_eur
        delta = (auto - pl) if (auto is not None and pl is not None) else None
        breaches = d.scenarios["planner"].reserve_breaches
        lines.append(
            f"{d.date:<12}{_fmt_eur(nb):>10}{_fmt_eur(auto):>9}{_fmt_eur(pl):>11}"
            f"{_fmt_eur(delta):>11}{breaches:>8}  {d.strategy}")
    a = result.aggregate
    lines.append("-" * 78)
    lines.append(
        f"{'TOTAL':<12}{_fmt_eur(a['no_battery_cost_eur']):>10}"
        f"{_fmt_eur(a['auto_cost_eur']):>9}{_fmt_eur(a['planner_cost_eur']):>11}"
        f"{_fmt_eur(a['planner_vs_auto_eur']):>11}{a['reserve_breaches']:>8}  "
        f"{a['days_replayed']} days, {a['switches']} switches")
    if "cfg_b" in a:
        b = a["cfg_b"]
        lines.append(
            f"config B planner € {b['planner_cost_eur']:.3f}  "
            f"(Δ vs A {b['delta_vs_a_eur']:+.3f}; + = B cheaper)")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m ems.replay",
        description="Replay recorded days through the planner (read-only) and compare "
                    "no-battery / vendor-auto / planner cost.")
    ap.add_argument("--days", type=int, default=14, help="number of recent days to replay")
    ap.add_argument("--db", default="ems/data/ems.sqlite", help="SQLite history DB (read-only)")
    ap.add_argument("--tz", default="Europe/Amsterdam", help="site timezone for local days")
    ap.add_argument("--set", action="append", default=[], metavar="key=value",
                    help="override a setting (repeatable), e.g. --set planner.solar_confidence=70")
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
