"""Daily grid-charge cap (#317): clamp, local-midnight reset, DST, restart-from-history."""
import logging
import sqlite3
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from ems.domain import BatteryIntent
from ems.planner.budgets import (
    cap_reason,
    clamp_grid_charge,
    grid_charge_kwh_from_samples,
    local_day_bounds,
    planned_grid_charge_kwh,
    read_already_bought_today,
)
from ems.planner.buy_pool import BuyPoolInfo
from ems.planner.schedule import SLOT, Plan, PlanSlot

AMS = ZoneInfo("Europe/Amsterdam")
# 2026-03-29: clocks jump 02:00 → 03:00 (23 h). 2026-10-25: 03:00 → 02:00 (25 h).
SPRING = datetime(2026, 3, 29, 12, 0, tzinfo=AMS)
AUTUMN = datetime(2026, 10, 25, 12, 0, tzinfo=AMS)


def _charge(start: datetime, kwh: float = 1.0) -> PlanSlot:
    return PlanSlot(
        start, BatteryIntent.GRID_CHARGE_TO_TARGET, "charge: cheap",
        target_soc=80.0, target_kwh=kwh, power_w=4000.0,
    )


def _plan(n: int, *, kwh: float = 1.0, start: datetime | None = None) -> Plan:
    t0 = start or datetime(2026, 6, 15, 0, 0, tzinfo=AMS)
    slots = tuple(_charge(t0 + i * SLOT, kwh) for i in range(n))
    return Plan(created_at=t0, slots=slots, strategy="winter")


def _kwh(plan: Plan) -> float:
    return planned_grid_charge_kwh(plan, 1.0, tz=AMS)


def test_clamp_never_exceeds_cap_and_names_the_reason():
    plan = clamp_grid_charge(_plan(20), 0.0, 12.0, 1.0, tz=AMS)
    assert _kwh(plan) <= 12.0 + 1e-9
    assert _kwh(plan) == 12.0
    kept = [s for s in plan.slots if s.intent is BatteryIntent.GRID_CHARGE_TO_TARGET]
    dropped = [s for s in plan.slots if s.intent is BatteryIntent.ALLOW_SELF_CONSUMPTION]
    assert len(kept) == 12 and len(dropped) == 8
    assert all(s.reason == cap_reason(12) for s in dropped)
    assert "Dagplafond 12 kWh bereikt" in dropped[0].reason
    assert all(s.reason == "charge: cheap" for s in kept)


def test_negative_price_soak_slots_count_toward_the_cap():
    """A cap is a cap: sub-zero GRID_CHARGE slots spend the same budget (Besluit)."""
    t0 = datetime(2026, 6, 15, 0, 0, tzinfo=AMS)
    slots = tuple(
        PlanSlot(
            t0 + i * SLOT, BatteryIntent.GRID_CHARGE_TO_TARGET,
            "charge: price €-0.05/kWh below €0 — you are paid to charge",
            target_kwh=1.0, power_w=4000.0,
        )
        for i in range(16)
    )
    plan = clamp_grid_charge(Plan(created_at=t0, slots=slots), 0.0, 12.0, 1.0, tz=AMS)
    assert _kwh(plan) == 12.0
    assert sum(s.intent is BatteryIntent.ALLOW_SELF_CONSUMPTION for s in plan.slots) == 4


def test_already_bought_8_leaves_at_most_4():
    plan = clamp_grid_charge(_plan(20), 8.0, 12.0, 1.0, tz=AMS)
    assert _kwh(plan) <= 4.0 + 1e-9
    assert _kwh(plan) == 4.0


def test_buy_pool_state_cap_when_buying_is_blocked():
    t0 = datetime(2026, 6, 15, 0, 0, tzinfo=AMS)
    pool = BuyPoolInfo(
        state="open", max_buy_eur_per_kwh=0.2, cheapest_slot=None,
        shortfall_kwh=20.0, next_chance_start=t0, pool_size=20,
    )
    plan = Plan(created_at=t0, slots=tuple(_charge(t0 + i * SLOT) for i in range(20)),
                buy_pool=pool)
    capped = clamp_grid_charge(plan, 0.0, 12.0, 1.0, tz=AMS)
    assert capped.buy_pool is not None and capped.buy_pool.state == "cap"
    untouched = clamp_grid_charge(plan, 0.0, 40.0, 1.0, tz=AMS)
    assert untouched is plan
    assert untouched.buy_pool is not None and untouched.buy_pool.state == "open"


def test_unknown_history_clamps_in_plan_only_and_warns(caplog):
    with caplog.at_level(logging.WARNING, logger="ems.planner.budgets"):
        plan = clamp_grid_charge(_plan(20), None, 12.0, 1.0, tz=AMS)
    assert _kwh(plan) == 12.0  # still charges, up to the in-plan cap
    assert any("unknown" in r.message and "not blocking" in r.message for r in caplog.records)


def test_under_cap_plan_is_unchanged():
    original = _plan(3)
    assert clamp_grid_charge(original, 0.0, 12.0, 1.0, tz=AMS) is original


def test_tomorrow_gets_a_fresh_cap():
    """Slots after local midnight do not spend today's already-bought counter."""
    t0 = datetime(2026, 6, 15, 23, 0, tzinfo=AMS)
    today = tuple(_charge(t0 + i * SLOT) for i in range(4))  # 23:00–23:45, 4 kWh
    tomorrow = tuple(_charge(t0 + (4 + i) * SLOT) for i in range(6))  # from 00:00
    plan = Plan(created_at=t0, slots=today + tomorrow)
    capped = clamp_grid_charge(plan, 10.0, 12.0, 1.0, tz=AMS)
    today_kwh = planned_grid_charge_kwh(capped, 1.0, tz=AMS, day=t0.date())
    next_day = (t0 + timedelta(hours=2)).date()
    tomorrow_kwh = planned_grid_charge_kwh(capped, 1.0, tz=AMS, day=next_day)
    assert today_kwh <= 2.0 + 1e-9  # 12 − 10
    assert tomorrow_kwh == 6.0


def _span_hours(moment: datetime) -> float:
    start, end = local_day_bounds(moment, AMS)
    utc = ZoneInfo("UTC")
    return (end.astimezone(utc) - start.astimezone(utc)).total_seconds() / 3600


def test_local_midnight_resets_the_counter():
    assert _span_hours(datetime(2026, 6, 15, 12, tzinfo=AMS)) == 24
    start, end = local_day_bounds(datetime(2026, 6, 15, 0, 10, tzinfo=AMS), AMS)
    before = datetime(2026, 6, 14, 23, 50, tzinfo=AMS)
    after = datetime(2026, 6, 15, 0, 5, tzinfo=AMS)
    samples = [
        {"ts": before, "grid_power_w": 4000.0, "battery_power_w": -4000.0},
        {"ts": after, "grid_power_w": 4000.0, "battery_power_w": -4000.0},
    ]
    kwh = grid_charge_kwh_from_samples(samples, day_start=start, day_end=end, tz=AMS)
    assert kwh == 1.0  # only the 00:05 bucket; 23:50 belongs to yesterday


def test_spring_dst_day_is_23_hours_and_counts_only_that_day():
    assert _span_hours(SPRING) == 23
    start, end = local_day_bounds(SPRING, AMS)
    inside = datetime(2026, 3, 29, 1, 30, tzinfo=AMS)
    after_jump = datetime(2026, 3, 29, 3, 30, tzinfo=AMS)
    yesterday = datetime(2026, 3, 28, 23, 30, tzinfo=AMS)
    samples = [
        {"ts": yesterday, "grid_power_w": 4000.0, "battery_power_w": -4000.0},
        {"ts": inside, "grid_power_w": 4000.0, "battery_power_w": -4000.0},
        {"ts": after_jump, "grid_power_w": 4000.0, "battery_power_w": -4000.0},
    ]
    kwh = grid_charge_kwh_from_samples(samples, day_start=start, day_end=end, tz=AMS)
    assert kwh == 2.0


def test_autumn_dst_day_is_25_hours_and_counts_both_folds():
    assert _span_hours(AUTUMN) == 25
    start, end = local_day_bounds(AUTUMN, AMS)
    first = datetime(2026, 10, 25, 2, 30, tzinfo=AMS, fold=0)
    second = datetime(2026, 10, 25, 2, 30, tzinfo=AMS, fold=1)
    assert first.astimezone(ZoneInfo("UTC")) != second.astimezone(ZoneInfo("UTC"))
    assert start <= first < end and start <= second < end
    samples = [
        {"ts": first, "grid_power_w": 4000.0, "battery_power_w": -4000.0},
        {"ts": second, "grid_power_w": 4000.0, "battery_power_w": -4000.0},
    ]
    kwh = grid_charge_kwh_from_samples(samples, day_start=start, day_end=end, tz=AMS)
    assert kwh == 2.0


def _write_history(path, rows: list[tuple[str, float, float]]) -> None:
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE raw_samples (ts TEXT, grid_power_w REAL, solar_power_w REAL, "
        "battery_power_w REAL, ev_power_w REAL, soc_pct REAL)"
    )
    conn.executemany(
        "INSERT INTO raw_samples (ts, grid_power_w, battery_power_w) VALUES (?, ?, ?)",
        rows,
    )
    conn.commit()
    conn.close()


def test_restart_recomputes_the_same_total_from_history(tmp_path):
    """No in-memory counter: a second call (a fresh process) reads the same kWh."""
    day = datetime(2026, 6, 15, 0, 0, tzinfo=AMS)
    rows = []
    for i in range(8):
        ts = (day + timedelta(minutes=15 * i)).astimezone(ZoneInfo("UTC")).isoformat()
        rows.append((ts, 4000.0, -4000.0))  # 1 kWh grid-charge per quarter
    db = tmp_path / "ems.sqlite"
    _write_history(db, rows)
    now = datetime(2026, 6, 15, 18, 0, tzinfo=AMS)
    first = read_already_bought_today(str(db), now, AMS)
    second = read_already_bought_today(str(db), now, AMS)  # restart: no retained total
    assert first == 8.0
    assert second == first
    # Next local day does not keep yesterday's 8 kWh.
    assert read_already_bought_today(
        str(db), datetime(2026, 6, 16, 1, 0, tzinfo=AMS), AMS,
    ) == 0.0


def test_missing_database_is_unknown_not_a_full_cap(tmp_path):
    missing = tmp_path / "nope.sqlite"
    now = datetime(2026, 6, 15, 12, tzinfo=AMS)
    assert read_already_bought_today(str(missing), now, AMS) is None
    assert read_already_bought_today(None, now, AMS) is None


def test_injected_history_of_8_kwh_limits_new_charge_to_4():
    """AC2: history says 8 kWh bought, cap 12 → at most 4 kWh of new charge slots."""
    day = datetime(2026, 1, 15, 0, 0, tzinfo=AMS)
    rows = []
    for i in range(8):
        ts = (day + timedelta(minutes=15 * i)).astimezone(ZoneInfo("UTC")).isoformat()
        rows.append({"ts": ts, "grid_power_w": 4000.0, "battery_power_w": -4000.0})
    start, end = local_day_bounds(datetime(2026, 1, 15, 12, tzinfo=AMS), AMS)
    bought = grid_charge_kwh_from_samples(rows, day_start=start, day_end=end, tz=AMS)
    assert bought == 8.0
    plan = clamp_grid_charge(_plan(20, start=day), bought, 12.0, 1.0, tz=AMS)
    assert _kwh(plan) <= 4.0 + 1e-9
