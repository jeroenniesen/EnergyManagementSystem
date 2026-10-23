"""Daily grid-charge cap (#317): clamp, local-midnight reset, DST, restart-from-history."""
import logging
import math
import sqlite3
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from ems.domain import BatteryIntent
from ems.planner.budgets import (
    UNKNOWN_COUNTER_REASON,
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


def test_unknown_counter_blocks_todays_grid_charge(caplog):
    """None (DB error) treats today's cap as reached. Discharge stays; tomorrow is a new day."""
    t0 = datetime(2026, 6, 15, 0, 0, tzinfo=AMS)
    discharge = PlanSlot(
        t0 + 20 * SLOT, BatteryIntent.DISCHARGE_FOR_LOAD, "serve the house",
    )
    tomorrow = _charge(datetime(2026, 6, 16, 1, 0, tzinfo=AMS))
    incoming = Plan(created_at=t0, slots=_plan(4).slots + (discharge, tomorrow))
    with caplog.at_level(logging.WARNING, logger="ems.planner.budgets"):
        plan = clamp_grid_charge(incoming, None, 12.0, 1.0, tz=AMS)
    today_charge = [
        s for s in plan.slots
        if s.intent is BatteryIntent.GRID_CHARGE_TO_TARGET
        and s.start.astimezone(AMS).date() == t0.date()
    ]
    assert today_charge == []
    assert planned_grid_charge_kwh(plan, 1.0, tz=AMS, day=t0.date()) == 0.0
    assert planned_grid_charge_kwh(plan, 1.0, tz=AMS, day=tomorrow.start.date()) == 1.0
    dropped = [s for s in plan.slots if s.reason == UNKNOWN_COUNTER_REASON]
    assert len(dropped) == 4
    assert dropped[0].reason == "Dagteller onbekend: niet laden van het net"
    assert any(s.intent is BatteryIntent.DISCHARGE_FOR_LOAD and s.reason == "serve the house"
               for s in plan.slots)
    assert any(s.start == tomorrow.start and s.intent is BatteryIntent.GRID_CHARGE_TO_TARGET
               for s in plan.slots)
    assert any("counter unknown" in r.message and "not charging" in r.message
               for r in caplog.records)


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


def test_clamp_honours_site_tz_for_local_today():
    """Already-bought applies to the plan day in site tz, not a hard-coded zone."""
    utc = ZoneInfo("UTC")
    created = datetime(2026, 6, 15, 23, 0, tzinfo=utc)  # still 15 Jun UTC; 16 Jun in Amsterdam
    slot = _charge(created, kwh=1.0)
    plan = Plan(created_at=created, slots=(slot,))
    utc_plan = clamp_grid_charge(plan, 10.0, 12.0, 1.0, tz=utc)
    ams_plan = clamp_grid_charge(plan, 10.0, 12.0, 1.0, tz=AMS)
    assert planned_grid_charge_kwh(utc_plan, 1.0, tz=utc) == 1.0  # 12 − 10 budget on 15 Jun
    assert planned_grid_charge_kwh(ams_plan, 1.0, tz=AMS) == 1.0  # fresh 16 Jun day, full cap


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


def test_clamp_counts_ac_grid_energy_not_dc_stored():
    """η≈0.95: counting target_kwh (DC) would allow 25×0.5 kWh AC = 12.5 > cap 12."""
    eta = math.sqrt(0.9)
    dc = 2000.0 * 0.25 / 1000.0 * eta
    assert 25 * dc <= 12.0  # the old DC sum still fits; the grid purchase does not
    t0 = datetime(2026, 6, 15, 0, 0, tzinfo=AMS)
    slots = tuple(
        PlanSlot(
            t0 + i * SLOT, BatteryIntent.GRID_CHARGE_TO_TARGET, "charge",
            target_kwh=dc, power_w=2000.0,
        )
        for i in range(30)
    )
    plan = clamp_grid_charge(Plan(created_at=t0, slots=slots), 0.0, 12.0, 0.5, tz=AMS)
    kept = [s for s in plan.slots if s.intent is BatteryIntent.GRID_CHARGE_TO_TARGET]
    assert len(kept) == 24
    assert planned_grid_charge_kwh(plan, 0.5, tz=AMS) == 12.0


def test_running_slot_is_pro_rata_not_double_counted():
    """10 min into a slot: history has the elapsed 2/3 kWh; the plan only adds the last 1/3."""
    start = datetime(2026, 6, 15, 12, 0, tzinfo=AMS)
    now = start + timedelta(minutes=10)
    samples = [{"ts": now, "grid_power_w": 4000.0, "battery_power_w": -4000.0}]
    day_start, day_end = local_day_bounds(now, AMS)
    bought = grid_charge_kwh_from_samples(
        samples, day_start=day_start, day_end=day_end, tz=AMS, now=now,
    )
    assert bought == pytest.approx(4000.0 * (10 / 60) / 1000.0)
    # Full-slot history (1.0) plus a full plan slot would leave no budget and drop this slot.
    later = start + SLOT
    plan = clamp_grid_charge(
        Plan(created_at=now, slots=(_charge(start), _charge(later))),
        bought, 1.0, 1.0, tz=AMS,
    )
    assert plan.slots[0].intent is BatteryIntent.GRID_CHARGE_TO_TARGET
    assert plan.slots[1].intent is BatteryIntent.ALLOW_SELF_CONSUMPTION
    assert planned_grid_charge_kwh(plan, 1.0, tz=AMS) == pytest.approx(1.0 / 3.0)


def test_measurement_gap_counts_planned_charge_and_zero_outside():
    """A missing bucket during GRID_CHARGE is a full slot; a gap elsewhere is 0."""
    day = datetime(2026, 6, 15, 0, 0, tzinfo=AMS)
    now = datetime(2026, 6, 15, 2, 0, tzinfo=AMS)
    start, end = local_day_bounds(now, AMS)
    measured = day + timedelta(minutes=5)  # 00:00 bucket, complete
    gap_charge = (day + SLOT).astimezone(ZoneInfo("UTC"))  # 00:15, no samples
    # 00:30 has neither samples nor a charge plan.
    samples = [{"ts": measured, "grid_power_w": 4000.0, "battery_power_w": -4000.0}]
    planned = {gap_charge: 4000.0}
    kwh = grid_charge_kwh_from_samples(
        samples, day_start=start, day_end=end, tz=AMS, now=now,
        planned_charge_w_by_slot=planned,
    )
    assert kwh == 2.0

    # The running slot's gap is only the elapsed fraction, not another full slot.
    running = datetime(2026, 6, 15, 12, 0, tzinfo=AMS)
    now_run = running + timedelta(minutes=10)
    running_key = running.astimezone(ZoneInfo("UTC"))
    gap_now = grid_charge_kwh_from_samples(
        [], day_start=start, day_end=end, tz=AMS, now=now_run,
        planned_charge_w_by_slot={running_key: 4000.0},
    )
    assert gap_now == pytest.approx(4000.0 * (10 / 60) / 1000.0)


def test_sql_includes_exact_midnight_and_excludes_the_next(tmp_path):
    """SQL window is [local 00:00:00, next 00:00:00). Exactly 00:00:00 starts the new day at 0."""
    db = tmp_path / "ems.sqlite"
    this_midnight = datetime(2026, 6, 15, 0, 0, 0, tzinfo=AMS)
    next_midnight = datetime(2026, 6, 16, 0, 0, 0, tzinfo=AMS)
    _write_history(db, [
        (this_midnight.astimezone(ZoneInfo("UTC")).isoformat(), 4000.0, -4000.0),
        (next_midnight.astimezone(ZoneInfo("UTC")).isoformat(), 4000.0, -4000.0),
    ])
    noon = datetime(2026, 6, 15, 12, 0, tzinfo=AMS)
    assert read_already_bought_today(str(db), noon, AMS) == 1.0
    assert read_already_bought_today(str(db), next_midnight, AMS) == 0.0
    next_noon = datetime(2026, 6, 16, 12, 0, tzinfo=AMS)
    assert read_already_bought_today(str(db), next_noon, AMS) == 1.0


def test_sql_dst_window_is_the_local_calendar_day(tmp_path):
    """Spring (23 h) and autumn (25 h) through the SQL reader, including both autumn folds."""
    utc = ZoneInfo("UTC")
    spring_db = tmp_path / "spring.sqlite"
    spring_midnight = datetime(2026, 3, 29, 0, 0, 0, tzinfo=AMS)
    _write_history(spring_db, [
        (datetime(2026, 3, 28, 23, 30, tzinfo=AMS).astimezone(utc).isoformat(), 4000.0, -4000.0),
        (spring_midnight.astimezone(utc).isoformat(), 4000.0, -4000.0),
        (datetime(2026, 3, 29, 1, 30, tzinfo=AMS).astimezone(utc).isoformat(), 4000.0, -4000.0),
        (datetime(2026, 3, 29, 3, 30, tzinfo=AMS).astimezone(utc).isoformat(), 4000.0, -4000.0),
        # Inside a naive +24 h window from spring midnight, but already 30 March.
        (datetime(2026, 3, 30, 0, 30, tzinfo=AMS).astimezone(utc).isoformat(), 4000.0, -4000.0),
    ])
    assert read_already_bought_today(str(spring_db), SPRING, AMS) == 3.0

    autumn_db = tmp_path / "autumn.sqlite"
    first = datetime(2026, 10, 25, 2, 30, tzinfo=AMS, fold=0)
    second = datetime(2026, 10, 25, 2, 30, tzinfo=AMS, fold=1)
    assert first.astimezone(utc) != second.astimezone(utc)
    late = datetime(2026, 10, 25, 23, 30, tzinfo=AMS)  # after midnight+24 h on this 25 h day
    _write_history(autumn_db, [
        (first.astimezone(utc).isoformat(), 4000.0, -4000.0),
        (second.astimezone(utc).isoformat(), 4000.0, -4000.0),
        (late.astimezone(utc).isoformat(), 4000.0, -4000.0),
        (datetime(2026, 10, 26, 0, 15, tzinfo=AMS).astimezone(utc).isoformat(), 4000.0, -4000.0),
    ])
    assert read_already_bought_today(
        str(autumn_db), datetime(2026, 10, 25, 23, 50, tzinfo=AMS), AMS,
    ) == 3.0


def test_clamp_on_23h_and_25h_days_uses_the_calendar_date():
    """A slot just after the short day, and one in the long day's extra hour, stay on their date."""
    spring_created = datetime(2026, 3, 29, 22, 0, tzinfo=AMS)
    spring_today = tuple(_charge(spring_created + i * SLOT) for i in range(4))  # 22:00–23:45
    # 00:30 on 30 March is < 24 h after 29 March 00:00, but it is the next calendar day.
    spring_next = datetime(2026, 3, 30, 0, 30, tzinfo=AMS)
    spring = clamp_grid_charge(
        Plan(created_at=spring_created, slots=spring_today + tuple(_charge(spring_next + i * SLOT)
                                                                   for i in range(6))),
        10.0, 12.0, 1.0, tz=AMS,
    )
    assert planned_grid_charge_kwh(spring, 1.0, tz=AMS, day=spring_created.date()) <= 2.0 + 1e-9
    assert planned_grid_charge_kwh(spring, 1.0, tz=AMS, day=spring_next.date()) == 6.0

    autumn_created = datetime(2026, 10, 25, 22, 0, tzinfo=AMS)
    # 23:30 is still 25 October (the +24 h mark on this day is 23:00).
    late = datetime(2026, 10, 25, 23, 30, tzinfo=AMS)
    later = datetime(2026, 10, 25, 23, 45, tzinfo=AMS)
    next_day = datetime(2026, 10, 26, 0, 15, tzinfo=AMS)
    autumn = clamp_grid_charge(
        Plan(created_at=autumn_created, slots=(
            _charge(late), _charge(later), _charge(next_day),
        )),
        11.0, 12.0, 1.0, tz=AMS,
    )
    assert planned_grid_charge_kwh(autumn, 1.0, tz=AMS, day=late.date()) == 1.0
    assert planned_grid_charge_kwh(autumn, 1.0, tz=AMS, day=next_day.date()) == 1.0


def test_plan_crossing_midnight_with_history_resets_at_000000(tmp_path):
    """created_at 23:50: today's history limits today; exactly 00:00:00 the counter is 0."""
    utc = ZoneInfo("UTC")
    day = datetime(2026, 6, 15, 0, 0, tzinfo=AMS)
    rows = []
    for i in range(8):
        ts = (day + timedelta(minutes=15 * i)).astimezone(utc).isoformat()
        rows.append((ts, 4000.0, -4000.0))
    db = tmp_path / "ems.sqlite"
    _write_history(db, rows)
    created = datetime(2026, 6, 15, 23, 50, tzinfo=AMS)
    bought = read_already_bought_today(str(db), created, AMS)
    assert bought == 8.0
    midnight = datetime(2026, 6, 16, 0, 0, 0, tzinfo=AMS)
    assert read_already_bought_today(str(db), midnight, AMS) == 0.0

    running = datetime(2026, 6, 15, 23, 45, tzinfo=AMS)
    tomorrow = tuple(_charge(midnight + i * SLOT) for i in range(6))
    plan = clamp_grid_charge(
        Plan(created_at=created, slots=(_charge(running),) + tomorrow),
        bought, 12.0, 1.0, tz=AMS,
    )
    assert planned_grid_charge_kwh(plan, 1.0, tz=AMS, day=created.date()) <= 4.0 + 1e-9
    assert planned_grid_charge_kwh(plan, 1.0, tz=AMS, day=midnight.date()) == 6.0


def test_sql_gap_uses_plan_history_intent(tmp_path):
    """No samples in a GRID_CHARGE slot → full planned power; a non-charge gap stays 0."""
    utc = ZoneInfo("UTC")
    day = datetime(2026, 6, 15, 0, 0, tzinfo=AMS)
    db = tmp_path / "gap.sqlite"
    _write_history(db, [
        ((day + timedelta(minutes=5)).astimezone(utc).isoformat(), 4000.0, -4000.0),
    ])
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE plan_history (ts TEXT, strategy TEXT, target_soc REAL, deadline TEXT, "
        "soc_pct REAL, intent TEXT, plan_version TEXT, floor_soc REAL)"
    )
    charge_at = (day + timedelta(minutes=20)).astimezone(utc).isoformat()  # inside 00:15
    idle_at = (day + timedelta(minutes=35)).astimezone(utc).isoformat()  # inside 00:30
    conn.execute(
        "INSERT INTO plan_history (ts, intent) VALUES (?, ?)",
        (charge_at, "grid_charge_to_target"),
    )
    conn.execute(
        "INSERT INTO plan_history (ts, intent) VALUES (?, ?)",
        (idle_at, "allow_self_consumption"),
    )
    conn.commit()
    conn.close()
    now = datetime(2026, 6, 15, 2, 0, tzinfo=AMS)
    assert read_already_bought_today(str(db), now, AMS, planned_charge_w=4000.0) == 2.0
