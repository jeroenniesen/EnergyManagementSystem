"""Historical replay optimization suite (backlog B-77).

Every scenario's cost/energy is HAND-COMPUTED in the comments so a math drift fails loudly. The
canned days are pure functions of grid/solar/price, so the identities are exact (no floats fuzz
beyond 1e-9). Two synthetic days are used:

  * SUMMER-ish day (`_solar_day`): flat 500 W load, a midday 2 kW solar block (10:00–14:00 =
    16 slots), cheap night / expensive evening prices. Exercises no_battery + auto self-use.
  * WINTER arbitrage day (`_winter_day`): flat 1 kW load, NO solar, cheap night + a 0.50 €/kWh
    evening peak. Auto can't help (nothing to store), so the planner strictly beats it.

Battery model for the exact tests: usable 10 kWh, ±4 kW, reserve 0%, round-trip η = 1.0 (lossless
→ clean arithmetic), start SoC 0%. All timestamps UTC so a local day = a UTC day = 96 slots.
"""
import asyncio
import json
import os
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from ems.domain import RawSample
from ems.load_model import reconstruct
from ems.planner.strategy import HysteresisState
from ems.replay import ReplayConfig, _resolve_strategy, main, replay_day, replay_range
from ems.sources.forecast import ForecastSlot
from ems.sources.prices import PriceSlot
from ems.storage.history import HistoryStore

UTZ = ZoneInfo("UTC")
DAY = datetime(2026, 1, 15, 0, 0, tzinfo=UTC)  # a winter day (Jan) — season resolves to winter

# Lossless, reserve-free 10 kWh / ±4 kW pack, so the kWh/€ arithmetic below is exact.
_BASE = {
    "battery.usable_kwh": 10.0,
    "battery.max_charge_w": 4000.0,
    "battery.max_discharge_w": 4000.0,
    "battery.min_reserve_soc": 0.0,
    "planner.round_trip_efficiency": 1.0,
}


def _cfg(**overrides) -> ReplayConfig:
    return ReplayConfig.from_settings({**_BASE, **overrides}, tz=UTZ)


def _hour(i: int) -> int:
    return i // 4  # slot index (0..95) → hour of day


def _raw_day(load_w, solar_of_slot, *, soc=0.0, day=DAY):
    """96 raw rows on a 15-min grid. Setting battery=0 makes the reconstructed load = grid+solar =
    `load_w` exactly (grid = load − solar), and solar is carried straight through."""
    rows = []
    for i in range(96):
        ts = (day + timedelta(minutes=15 * i)).isoformat()
        s = solar_of_slot(i)
        rows.append({
            "ts": ts, "grid_power_w": load_w - s, "solar_power_w": s,
            "battery_power_w": 0.0, "ev_power_w": 0.0, "soc_pct": soc,
        })
    return rows


def _price_day(price_of_slot, *, day=DAY):
    return [
        {"start_ts": (day + timedelta(minutes=15 * i)).isoformat(),
         "eur_per_kwh": price_of_slot(i)}
        for i in range(96)
    ]


# --- day builders ---------------------------------------------------------------------------------
def _solar_of_slot(i):  # 2 kW block over hours 10–13 (10:00–14:00), else dark
    return 2000.0 if 10 <= _hour(i) < 14 else 0.0


def _price_of_slot(i):  # cheap night, day, expensive evening, cheap late
    h = _hour(i)
    if h < 6:
        return 0.10
    if 6 <= h < 18:
        return 0.20
    if 18 <= h < 22:
        return 0.40
    return 0.15


def _solar_day(soc=0.0):
    return _raw_day(500.0, _solar_of_slot, soc=soc), _price_day(_price_of_slot)


def _winter_price(i):  # 0.10 night(0-5) · 0.20 midday(6-16) · 0.50 peak(17-20) · 0.15 late(21-23)
    h = _hour(i)
    if h < 6:
        return 0.10
    if 17 <= h < 21:
        return 0.50
    if 21 <= h:
        return 0.15
    return 0.20


def _winter_day(soc=0.0):
    return _raw_day(1000.0, lambda i: 0.0, soc=soc), _price_day(_winter_price)


# --- 1. no_battery exact cost ---------------------------------------------------------------------
def test_no_battery_exact_cost():
    raw, prices = _solar_day()
    day = replay_day(raw, prices, [], cfg=_cfg())
    assert day.data_ok and day.slots == 96
    nb = day.scenarios["no_battery"]
    # 80 non-solar slots import 500 W (0.125 kWh each) = 10.0 kWh; 16 solar slots export 1500 W
    # (0.375 kWh each) = 6.0 kWh.
    assert abs(nb.import_kwh - 10.0) < 1e-9
    assert abs(nb.export_kwh - 6.0) < 1e-9
    assert nb.cycles_kwh == 0.0 and nb.reserve_breaches == 0 and nb.switches == 0
    # Import cost: 24 night@0.10 + 32 day@0.20 + 16 eve@0.40 + 8 late@0.15, each 0.125 kWh
    #   = 0.30 + 0.80 + 0.80 + 0.15 = 2.05
    # Export credit (net_metering = full price): 6.0 kWh @0.20 = 1.20  →  cost 2.05 − 1.20 = 0.85
    assert abs(nb.cost_eur - 0.85) < 1e-9


# --- 2. auto self-use stores surplus + cuts evening import ----------------------------------------
def test_auto_selfuse_stores_surplus_and_cuts_evening_import():
    raw, prices = _solar_day()
    auto = replay_day(raw, prices, [], cfg=_cfg()).scenarios["auto_selfuse"]
    # Night (slots 0–39) can't discharge (SoC starts 0) → 40×0.125 = 5.0 kWh imported.
    # Solar (slots 40–55): 1500 W surplus stored → SoC 0 → 6.0 kWh, grid 0 (no export).
    # After solar (slots 56–95): 500 W deficit served from the 6.0 kWh pack → grid 0 (no import).
    assert abs(auto.import_kwh - 5.0) < 1e-9
    assert abs(auto.export_kwh - 0.0) < 1e-9
    assert abs(auto.cycles_kwh - 5.0) < 1e-9  # 40 evening slots × 0.125 kWh discharged
    # Import cost only over slots 0–39: 24@0.10 + 16@0.20, each 0.125 → 0.30 + 0.40 = 0.70
    assert abs(auto.cost_eur - 0.70) < 1e-9
    # It strictly beats no-battery (0.85) by soaking free solar into the evening.
    assert auto.cost_eur < 0.85


# --- 3. planner beats-or-ties auto on an arbitrage-friendly day -----------------------------------
def test_planner_beats_auto_on_winter_arbitrage_day():
    raw, prices = _winter_day()
    day = replay_day(raw, prices, [], cfg=_cfg(**{"strategy.mode": "winter"}))
    assert day.data_ok and day.strategy == "winter"
    auto = day.scenarios["auto_selfuse"]
    planner = day.scenarios["planner"]
    # No solar + SoC 0 → auto never charges, so it ties the no-battery meter (24 kWh × its price).
    assert abs(auto.cost_eur - day.scenarios["no_battery"].cost_eur) < 1e-9
    # The planner grid-charges the cheap night and discharges the 0.50 €/kWh peak → cheaper.
    assert planner.cost_eur <= auto.cost_eur + 1e-9
    assert planner.cost_eur < auto.cost_eur  # genuinely arbitraged, not just tied
    assert planner.switches > 0  # it changed intent across the day (charge / hold / discharge)


# --- 4. reserve-breach detection ------------------------------------------------------------------
def test_reserve_breach_counted_when_soc_forced_below_reserve():
    # Start SoC 20 %, reserve 50 %, no solar: the pack can never rise (no surplus) and can't
    # discharge below reserve (it's already under) → every slot's end-of-slot SoC is a breach.
    raw, prices = _winter_day(soc=20.0)
    day = replay_day(raw, prices, [], cfg=_cfg(**{"battery.min_reserve_soc": 50.0}))
    auto = day.scenarios["auto_selfuse"]
    assert auto.reserve_breaches == day.slots == 96
    # no_battery has no pack → no reserve concept → zero breaches.
    assert day.scenarios["no_battery"].reserve_breaches == 0


# --- 5. low-coverage day is skipped with a reason -------------------------------------------------
def test_low_coverage_day_skipped_with_reason():
    raw, prices = _solar_day()
    # Keep only 10 of 96 slots of load → well below the 80 % floor.
    day = replay_day(raw[:10], prices, [], cfg=_cfg())
    assert day.data_ok is False
    assert day.skip_reason and "coverage" in day.skip_reason
    assert day.scenarios == {}
    assert day.strategy is None


def test_low_price_coverage_day_skipped():
    raw, prices = _solar_day()
    day = replay_day(raw, prices[:10], [], cfg=_cfg())  # full load, sparse prices
    assert day.data_ok is False
    assert "price coverage" in day.skip_reason


# --- 6. export credited via export_value under spot_minus_tax (numeric identity) ------------------
def test_export_credited_under_spot_minus_tax():
    raw, prices = _solar_day()
    cfg = _cfg(**{"prices.export_price_model": "spot_minus_tax",
                  "prices.energy_tax_eur_per_kwh": 0.13})
    nb = replay_day(raw, prices, [], cfg=cfg).scenarios["no_battery"]
    # Same 2.05 import cost. Export credit now (price − tax) = 0.20 − 0.13 = 0.07 per kWh:
    #   6.0 kWh × 0.07 = 0.42  →  cost 2.05 − 0.42 = 1.63  (vs 0.85 under net_metering).
    assert abs(nb.cost_eur - 1.63) < 1e-9
    assert abs(nb.export_kwh - 6.0) < 1e-9
    # It differs deterministically from the default net_metering credit.
    nb_net = replay_day(raw, prices, [], cfg=_cfg()).scenarios["no_battery"]
    assert nb.cost_eur > nb_net.cost_eur


# --- 7. A/B override changes the result deterministically -----------------------------------------
def test_ab_override_changes_planner_cost(tmp_path):
    db = str(tmp_path / "ems.sqlite")

    async def seed():
        store = HistoryStore(db)
        await store.init()
        raw, prices = _winter_day()
        for r in raw:
            sample = RawSample(
                grid_power_w=r["grid_power_w"], solar_power_w=r["solar_power_w"],
                battery_power_w=r["battery_power_w"], ev_power_w=r["ev_power_w"],
                soc_pct=r["soc_pct"])
            await store.record(r["ts"], sample, reconstruct(sample))
        await store.upsert_price_slots([(p["start_ts"], p["eur_per_kwh"]) for p in prices])

    asyncio.run(seed())

    cfg_a = _cfg(**{"strategy.mode": "winter"})
    # Config B: enormous wear cost → break-even exceeds every price → the planner won't arbitrage,
    # so it ties the auto/no-battery floor. Deterministically dearer than A.
    cfg_b = _cfg(**{"strategy.mode": "winter", "planner.degradation_eur_per_kwh": 0.50})

    result = replay_range(db, 1, cfg_a, cfg_b=cfg_b)
    assert len(result.days) == 1 and result.days[0].data_ok
    a_cost = result.days[0].scenarios["planner"].cost_eur
    b_cost = result.days_b[0].scenarios["planner"].cost_eur
    assert a_cost < b_cost  # A arbitrages, B is throttled off → A is cheaper
    agg = result.aggregate
    assert "cfg_b" in agg
    assert abs(agg["planner_vs_auto_eur"] - (agg["auto_cost_eur"] - agg["planner_cost_eur"])) < 1e-9
    # B ties the vendor-auto floor (no arbitrage).
    assert abs(b_cost - result.days[0].scenarios["auto_selfuse"].cost_eur) < 1e-9
    assert agg["cfg_b"]["delta_vs_a_eur"] < 0  # A cheaper ⇒ B − A > 0 ⇒ (A − B) < 0


# --- 8. CLI end-to-end against a seeded read-only DB ----------------------------------------------
def test_cli_table_and_json_against_seeded_db(tmp_path, capsys):
    db = str(tmp_path / "ems.sqlite")

    async def seed():
        store = HistoryStore(db)
        await store.init()
        raw, prices = _winter_day()
        for r in raw:
            sample = RawSample(
                grid_power_w=r["grid_power_w"], solar_power_w=r["solar_power_w"],
                battery_power_w=r["battery_power_w"], ev_power_w=r["ev_power_w"],
                soc_pct=r["soc_pct"])
            await store.record(r["ts"], sample, reconstruct(sample))
        await store.upsert_price_slots([(p["start_ts"], p["eur_per_kwh"]) for p in prices])

    asyncio.run(seed())

    # Table form.
    rc = main(["--days", "1", "--db", db, "--tz", "UTC", "--set", "strategy.mode=winter"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "planner €" in out and "TOTAL" in out and "2026-01-15" in out

    # JSON form parses and carries the aggregate + a planner scenario.
    rc = main(["--days", "1", "--db", db, "--tz", "UTC", "--json", "--set", "strategy.mode=winter"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["aggregate"]["days_replayed"] == 1
    assert "planner_vs_auto_eur" in payload["aggregate"]
    assert payload["days"][0]["scenarios"]["planner"]["cost_eur"] is not None


# --- 9. read-only: a replay never writes the DB ---------------------------------------------------
def test_replay_never_writes_db(tmp_path):
    db = str(tmp_path / "ems.sqlite")

    async def seed():
        store = HistoryStore(db)
        await store.init()
        raw, prices = _winter_day()
        for r in raw:
            sample = RawSample(
                grid_power_w=r["grid_power_w"], solar_power_w=r["solar_power_w"],
                battery_power_w=r["battery_power_w"], ev_power_w=r["ev_power_w"],
                soc_pct=r["soc_pct"])
            await store.record(r["ts"], sample, reconstruct(sample))
        await store.upsert_price_slots([(p["start_ts"], p["eur_per_kwh"]) for p in prices])

    asyncio.run(seed())

    before = os.path.getmtime(db)
    replay_range(db, 3, _cfg(**{"strategy.mode": "winter"}))
    # The file is untouched (mode=ro would raise on any write anyway).
    assert os.path.getmtime(db) == before


def test_resolve_strategy_threads_hysteresis_across_days():
    # B-15: replay's _resolve_strategy dampens the season the same way the live app does. Start
    # committed to winter, then feed a strong-surplus (summer-leaning) forecast on three
    # consecutive days: the switch to summer must not land until the 3rd day (hysteresis_days=3).
    cfg = _cfg()  # strategy.mode defaults to auto, hysteresis_days=3
    assert cfg.strategy == "auto" and cfg.hysteresis_days == 3
    state = HysteresisState(committed="winter", last_day="2026-02-28")
    seen = []
    for day_offset in range(3):
        d = datetime(2026, 3, 1 + day_offset, 12, tzinfo=UTC)
        # Sunny forecast (high surplus) + a tiny price spread → the raw pick leans summer.
        fc = [ForecastSlot(d + i * timedelta(minutes=15), 3000.0, 3000.0, 3000.0)
              for i in range(96)]
        prices = [PriceSlot(d + i * timedelta(minutes=15), 0.20) for i in range(96)]
        strat, state = _resolve_strategy(cfg, d, prices, fc, {}, state)
        seen.append(strat)
    assert seen == ["winter", "winter", "summer"]  # held two days, switched on the third


def test_replay_hysteresis_matches_live_same_day_tick_semantics():
    """Replay callers may evaluate multiple snapshots in a day; transient agreement preserves run."""
    cfg = _cfg()
    d = datetime(2026, 3, 1, 12, tzinfo=UTC)
    state = HysteresisState(committed="winter", last_day="2026-02-28")
    sunny = [ForecastSlot(d + i * timedelta(minutes=15), 3000.0, 3000.0, 3000.0)
             for i in range(96)]
    flat = [ForecastSlot(d + i * timedelta(minutes=15), 0.0, 0.0, 0.0) for i in range(96)]
    prices = [PriceSlot(d + i * timedelta(minutes=15), 0.20) for i in range(96)]
    _, state = _resolve_strategy(cfg, d, prices, sunny, {}, state)
    assert state.count == 1
    _, state = _resolve_strategy(cfg, d, prices, flat, {}, state)  # same-day transient agreement
    assert state.pending == "summer" and state.count == 1
