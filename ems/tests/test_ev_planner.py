"""Correctness pin for `ems/ev_planner.plan_car_charging` — the EV "math core" (design 2026-07-12).

All datetimes are tz-aware (Europe/Amsterdam, or UTC where DST correctness is under test). Numeric
identities are hand-computed in comments. Reference constants throughout:

    P = 11 kW, η_c = 0.90  ⇒  a full 15-min slot delivers  c = 11·0.25·0.9 = 2.475 battery-kWh
                              and draws on the AC side       ac = 11·0.25     = 2.75  AC-kWh
                              costing  price · 2.75  €.
"""
import itertools
import random
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from ems.ev_planner import SLOT, _allocate, plan_car_charging
from ems.sources.prices import PriceSlot

AMS = ZoneInfo("Europe/Amsterdam")
POWER = 11.0
ETA = 0.9
C_SLOT = POWER * 0.25 * ETA  # 2.475 battery-kWh per full slot


def _slots(base: datetime, prices: list[float]) -> list[PriceSlot]:
    return [PriceSlot(base + i * SLOT, p) for i, p in enumerate(prices)]


def _dl(ready_by: datetime, min_pct: float) -> dict:
    return {"ready_by": ready_by, "min_pct": min_pct, "day": ready_by.strftime("%a")}


# --- 1. single deadline, flat prices → earliest slots, exact kWh/cost math -------------------

def test_single_deadline_flat_prices_picks_earliest_slots():
    base = datetime(2026, 7, 13, 23, 0, tzinfo=AMS)  # Mon 23:00
    # C=9.9, soc 0, min 50 ⇒ E = R = 0.5·9.9 = 4.95 battery-kWh = exactly two full slots.
    plan = plan_car_charging(
        base, [_dl(base + timedelta(hours=8), 50)], _slots(base, [0.20] * 8), {},
        soc_pct=0.0, battery_net_kwh=9.9, power_kw=POWER,
    )
    assert len(plan["slots"]) == 2
    assert plan["slots"][0]["start"] == base.isoformat()
    assert plan["slots"][1]["start"] == (base + SLOT).isoformat()
    for s in plan["slots"]:
        assert s["kw"] == 11.0
        assert s["battery_kwh"] == 2.48  # round(2.475, 2)
        assert s["ac_kwh"] == 2.75
        assert s["eur_per_kwh_effective"] == 0.2
        assert s["est_cost_eur"] == 0.55  # 0.20 · 2.75
        assert s["solar_surplus"] is False
        assert s["for_deadline"] == (base + timedelta(hours=8)).isoformat()
    assert plan["total_planned_kwh"] == 4.95
    assert plan["total_est_cost_eur"] == 1.1  # 2 · 0.55
    d0 = plan["deadlines"][0]
    assert (d0["required_kwh"], d0["planned_kwh"], d0["pending_kwh"]) == (4.95, 4.95, 0.0)
    assert d0["already_met"] is False and d0["feasible"] is True


# --- 2. cheap valley later → allocation prefers the valley, not the earliest slots -----------

def test_prefers_cheap_valley_over_earliest():
    base = datetime(2026, 7, 13, 22, 0, tzinfo=AMS)
    prices = [0.30] * 8
    prices[4] = prices[5] = 0.04  # a two-slot valley later in the horizon
    plan = plan_car_charging(
        base, [_dl(base + timedelta(hours=8), 50)], _slots(base, prices), {},
        soc_pct=0.0, battery_net_kwh=9.9, power_kw=POWER,  # need two slots
    )
    starts = sorted(s["start"] for s in plan["slots"])
    assert starts == [(base + 4 * SLOT).isoformat(), (base + 5 * SLOT).isoformat()]
    assert plan["total_est_cost_eur"] == 0.22  # 2 · 0.04 · 2.75, beats 2 · 0.30 · 2.75 = 1.65


# --- 3. two deadlines, second SMALLER (Mon 80%, Fri 60%): Fri adds nothing --------------------

def test_second_deadline_smaller_adds_nothing():
    now = datetime(2026, 7, 12, 20, 0, tzinfo=AMS)  # Sun evening
    mon = datetime(2026, 7, 13, 7, 30, tzinfo=AMS)
    fri = datetime(2026, 7, 17, 7, 30, tzinfo=AMS)
    # soc 50, C 10 ⇒ E_Mon = (80-50)/100·10 = 3.0 ; E_Fri = (60-50)/100·10 = 1.0.
    # R_Mon = 3.0 ; R_Fri = max(3.0, 1.0) = 3.0  → Fri's binding requirement is already covered.
    plan = plan_car_charging(
        now, [_dl(mon, 80), _dl(fri, 60)], _slots(now, [0.10] * 8), {},
        soc_pct=50.0, battery_net_kwh=10.0, power_kw=POWER,
    )
    mon_d, fri_d = plan["deadlines"]
    assert (mon_d["required_kwh"], mon_d["planned_kwh"]) == (3.0, 3.0)
    assert mon_d["already_met"] is False
    # Fri: E_Fri (1.0) > 0 so NOT already_met; but R makes its incremental requirement 0 → no alloc.
    assert fri_d["already_met"] is False
    assert (fri_d["required_kwh"], fri_d["planned_kwh"], fri_d["pending_kwh"]) == (0.0, 0.0, 0.0)
    assert plan["total_planned_kwh"] == 3.0  # no double allocation
    assert {s["for_deadline"] for s in plan["slots"]} == {mon.isoformat()}


# --- 4. two deadlines, second NEEDS more: D1 never uses slots after D1 ------------------------

def test_second_deadline_needs_more_respects_D1_window():
    now = datetime(2026, 7, 13, 0, 0, tzinfo=AMS)
    d1 = now + timedelta(hours=4)  # 04:00
    d2 = now + timedelta(hours=8)  # 08:00
    # soc 0, C 9.9 ⇒ E_D1 = 0.25·9.9 = 2.475 (one slot) ; E_D2 = 0.50·9.9 = 4.95.
    # R_D1 = 2.475 ; R_D2 = 4.95 ; incremental for D2 = 2.475 (one more slot).
    prices = [0.30] * 32
    prices[20] = 0.05  # a cheaper slot at 05:00 — AFTER D1, usable only by D2
    plan = plan_car_charging(
        now, [_dl(d1, 25), _dl(d2, 50)], _slots(now, prices), {},
        soc_pct=0.0, battery_net_kwh=9.9, power_kw=POWER,
    )
    assert len(plan["slots"]) == 2
    by_dl = {s["for_deadline"]: s for s in plan["slots"]}
    s1, s2 = by_dl[d1.isoformat()], by_dl[d2.isoformat()]
    # D1 must use a 0.30 slot that ENDS by 04:00 — it cannot reach the cheaper 05:00 slot.
    assert s1["eur_per_kwh_effective"] == 0.3
    assert datetime.fromisoformat(s1["start"]) + SLOT <= d1
    # D2 takes the genuinely-cheapest usable slot: the 05:00 valley.
    assert s2["start"] == (now + 20 * SLOT).isoformat()
    assert s2["eur_per_kwh_effective"] == 0.05
    assert plan["total_planned_kwh"] == 4.95


# --- 5. fractional final slot -----------------------------------------------------------------

def test_fractional_final_slot():
    base = datetime(2026, 7, 13, 23, 0, tzinfo=AMS)
    # soc 0, C 10, min 50 ⇒ E = R = 5.0 battery-kWh. Two full slots = 4.95, leaving 0.05 → the
    # third slot is fractional: 0.05 / 2.475 of a slot, everything scaled, kw still 11.
    plan = plan_car_charging(
        base, [_dl(base + timedelta(hours=8), 50)], _slots(base, [0.20] * 8), {},
        soc_pct=0.0, battery_net_kwh=10.0, power_kw=POWER,
    )
    assert len(plan["slots"]) == 3
    frac = plan["slots"][2]
    assert frac["kw"] == 11.0  # power unchanged; the FRACTION of the 15 min is what shrinks
    assert frac["battery_kwh"] == 0.05
    assert frac["ac_kwh"] == 0.06  # round(0.05 / 0.9, 2)
    assert frac["est_cost_eur"] == 0.01  # round(0.20 · 0.05 / 0.9, 2)
    assert plan["total_planned_kwh"] == 5.0
    assert plan["total_est_cost_eur"] == 1.11  # round(0.20 · 5.0 / 0.9, 2)


# --- 6. BRUTE-FORCE CROSS-CHECK: greedy total cost == exhaustive optimum ----------------------

def _brute_min_cost(idxs: list[int], eff_prices: list[float], reqs: list[float]) -> float:
    """Exhaustive optimum for the nested-deadline allocation on a ½-slot grid.

    Requirements are multiples of c/2, so x_j ∈ {0, 0.5, 1} spans every vertex of the feasible
    polytope: its constraint matrix is prefix-sums (consecutive-ones ⇒ totally unimodular), so with
    RHS on the ½-grid all LP vertices — hence the optimum — land on that grid. This search is
    therefore EXACT and fully independent of the greedy under test. `idxs[i]` = number of slots
    usable by deadline i; `reqs[i]` = R_i (battery-kWh). Deliver exactly R_k (no over-charging)."""
    n = len(eff_prices)
    ac_full = POWER * 0.25
    total_req = reqs[-1]
    best = None
    for combo in itertools.product((0.0, 0.5, 1.0), repeat=n):
        if abs(sum(combo) * C_SLOT - total_req) > 1e-9:
            continue  # must deliver exactly R_k
        if any(sum(combo[:idx]) * C_SLOT + 1e-9 < reqs[i] for i, idx in enumerate(idxs)):
            continue  # every deadline's cumulative constraint must hold
        cost = sum(combo[j] * eff_prices[j] * ac_full for j in range(n))
        if best is None or cost < best:
            best = cost
    return best


def test_brute_force_cross_check():
    rng = random.Random(20260712)
    base = datetime(2026, 7, 13, 0, 0, tzinfo=AMS)
    checked = 0
    for trial in range(60):
        n = rng.randint(3, 8)
        prices = [round(rng.uniform(-0.10, 0.50), 2) for _ in range(n)]  # negatives included
        k = rng.randint(1, 3)
        idxs = sorted(rng.sample(range(1, n + 1), k))
        # Requirements as multiples of c/2, non-decreasing, m_i ≤ 2·idx_i (guarantees both band
        # capacity AND physical feasibility, so the greedy fully allocates with no pending).
        m: list[int] = []
        prev = 0
        for idx in idxs:
            prev = rng.randint(prev, 2 * idx)
            m.append(prev)
        reqs = [mi * (C_SLOT / 2) for mi in m]
        # C = 99 kWh, soc 0 ⇒ E_i = (min_pct_i/100)·99 ; min_pct_i = 1.25·m_i ⇒ E_i = m_i·(c/2).
        deadlines = [_dl(base + idx * SLOT, 1.25 * mi) for idx, mi in zip(idxs, m, strict=True)]
        slots = _slots(base, prices)

        states, records = _allocate(
            base, deadlines, slots, {}, soc_pct=0.0, battery_net_kwh=99.0,
            charge_efficiency=ETA, power_kw=POWER, export_model="net_metering",
            energy_tax_eur_per_kwh=0.13, fixed_feed_in_eur_per_kwh=0.01, surplus_threshold_w=1000.0,
        )
        # Greedy must have delivered exactly R_k (feasible, fully within the priced horizon).
        assert abs(sum(s["alloc"] for s in states) - reqs[-1]) < 1e-9
        assert all(r["pending_kwh"] < 1e-9 and r["feasible"] for r in records)
        greedy_cost = sum(s["alloc"] / ETA * s["eff"] for s in states)  # full precision, unrounded

        brute = _brute_min_cost(idxs, prices, reqs)
        assert brute is not None
        assert abs(greedy_cost - brute) < 1e-9, (
            f"trial {trial}: greedy {greedy_cost} != brute {brute}; "
            f"prices={prices} idxs={idxs} m={m}"
        )
        checked += 1
    assert checked == 60


# --- 7. surplus slots priced at export_value -------------------------------------------------

def test_surplus_pricing_shifts_allocation_under_spot_minus_tax():
    now = datetime(2026, 7, 13, 0, 0, tzinfo=AMS)
    night = now  # 00:00, sticker 0.10, no solar
    midday = now + 48 * SLOT  # 12:00, sticker 0.17, solar surplus
    slots = [PriceSlot(night, 0.10), PriceSlot(midday, 0.17)]
    p50 = {midday: 2000.0}
    dl = [_dl(now + timedelta(hours=20), 25)]  # need one slot (E = 0.25·9.9 = 2.475)
    kw = dict(soc_pct=0.0, battery_net_kwh=9.9, power_kw=POWER)

    # net_metering: export_value == price, so surplus is irrelevant — pure arbitrage picks night.
    net = plan_car_charging(now, dl, slots, p50, export_model="net_metering", **kw)
    assert len(net["slots"]) == 1
    assert net["slots"][0]["start"] == night.isoformat()
    assert net["slots"][0]["solar_surplus"] is False
    assert net["slots"][0]["eur_per_kwh_effective"] == 0.1

    # spot_minus_tax: midday's surplus is valued at export_value = 0.17 − 0.13 = 0.04, which beats
    # night's full 0.10 → the allocation SHIFTS to the sunny slot (the post-2027 behaviour change).
    dyn = plan_car_charging(
        now, dl, slots, p50, export_model="spot_minus_tax", energy_tax_eur_per_kwh=0.13, **kw
    )
    assert len(dyn["slots"]) == 1
    assert dyn["slots"][0]["start"] == midday.isoformat()
    assert dyn["slots"][0]["solar_surplus"] is True
    assert dyn["slots"][0]["eur_per_kwh_effective"] == 0.04
    assert dyn["slots"][0]["est_cost_eur"] == 0.11  # 0.04 · 2.75


# --- 8. negative effective prices allocated first, cost negative ------------------------------

def test_negative_prices_allocated_first_negative_cost():
    base = datetime(2026, 7, 13, 0, 0, tzinfo=AMS)
    prices = [0.20] * 6
    prices[3] = prices[4] = -0.04  # negative slots, deliberately not the earliest
    plan = plan_car_charging(
        base, [_dl(base + timedelta(hours=6), 50)], _slots(base, prices), {},
        soc_pct=0.0, battery_net_kwh=9.9, power_kw=POWER,  # need two slots
    )
    starts = sorted(s["start"] for s in plan["slots"])
    assert starts == [(base + 3 * SLOT).isoformat(), (base + 4 * SLOT).isoformat()]
    assert all(s["eur_per_kwh_effective"] == -0.04 for s in plan["slots"])
    assert plan["total_est_cost_eur"] == -0.22  # 2 · (−0.04) · 2.75 — being paid to charge
    assert plan["total_est_cost_eur"] < 0


# --- 9. horizon honesty: beyond-horizon deadline → pending; in-horizon one still planned ------

def test_horizon_pending_vs_planned():
    now = datetime(2026, 7, 13, 0, 0, tzinfo=AMS)
    d1 = now + timedelta(hours=2)  # within the priced horizon
    d2 = now + timedelta(hours=100)  # far beyond the last priced slot
    # soc 0, C 9.9 ⇒ R_D1 = 2.475 (one slot) ; R_D2 = 4.95. Only ONE priced slot exists.
    plan = plan_car_charging(
        now, [_dl(d1, 25), _dl(d2, 50)], _slots(now, [0.10]), {},
        soc_pct=0.0, battery_net_kwh=9.9, power_kw=POWER,
    )
    d1_d, d2_d = plan["deadlines"]
    assert (d1_d["planned_kwh"], d1_d["pending_kwh"]) == (2.48, 0.0)  # fully planned
    assert d1_d["feasible"] is True
    # D2's extra 2.475 has no priced slot before it → reported as pending, never fabricated.
    assert (d2_d["required_kwh"], d2_d["planned_kwh"], d2_d["pending_kwh"]) == (2.48, 0.0, 2.48)
    assert d2_d["feasible"] is True  # far away → physically fine, just awaiting prices
    assert plan["total_planned_kwh"] == 2.48


# --- 10. physical infeasibility --------------------------------------------------------------

def test_infeasible_flagged_with_shortfall():
    now = datetime(2026, 7, 13, 0, 0, tzinfo=AMS)
    ready = now + timedelta(hours=2)
    # 40 battery-kWh needed by a deadline 2 h away at 11 kW · 0.9: even continuous charging delivers
    # only 2 · 11 · 0.9 = 19.8 → shortfall 40 − 19.8 = 20.2. Computed independently of prices.
    plan = plan_car_charging(
        now, [_dl(ready, 100)], _slots(now, [0.20] * 8), {},
        soc_pct=0.0, battery_net_kwh=40.0, power_kw=POWER,
    )
    d0 = plan["deadlines"][0]
    assert d0["feasible"] is False
    assert d0["shortfall_kwh"] == 20.2


# --- 11. a slot must END by ready_by ---------------------------------------------------------

def test_slot_must_end_by_ready_by():
    now = datetime(2026, 7, 13, 7, 0, tzinfo=AMS)
    ready = datetime(2026, 7, 13, 7, 30, tzinfo=AMS)
    # 07:15 slot ends 07:30 (== deadline) → usable. 07:30 slot ends 07:45 → NOT usable, despite
    # being far cheaper. Need one slot (E = 0.25·9.9 = 2.475).
    slots = [PriceSlot(datetime(2026, 7, 13, 7, 15, tzinfo=AMS), 0.20),
             PriceSlot(ready, 0.01)]
    plan = plan_car_charging(
        now, [_dl(ready, 25)], slots, {}, soc_pct=0.0, battery_net_kwh=9.9, power_kw=POWER
    )
    assert len(plan["slots"]) == 1
    assert plan["slots"][0]["start"] == datetime(2026, 7, 13, 7, 15, tzinfo=AMS).isoformat()
    assert plan["slots"][0]["eur_per_kwh_effective"] == 0.2  # NOT the cheaper 07:30 slot


# --- 12. DST fall-back day (Sun 2026-10-25, 25-hour Amsterdam day) ----------------------------

def test_dst_fall_back_day():
    # Slots constructed in UTC (unambiguous across the repeated 02:00–03:00 local hour); the
    # deadline is a local Amsterdam wall time. Cross-tz comparison must stay consistent.
    now = datetime(2026, 10, 25, 0, 0, tzinfo=UTC)  # 02:00 CEST, just before the fall-back
    ready_local = datetime(2026, 10, 25, 7, 30, tzinfo=AMS)  # 06:30 UTC (CET, after fall-back)
    slots = _slots(now, [0.10] * 28)  # 7 h of UTC slots, 15-min each
    plan = plan_car_charging(
        now, [_dl(ready_local, 25)], slots, {}, soc_pct=0.0, battery_net_kwh=9.9, power_kw=POWER
    )
    assert len(plan["slots"]) == 1  # one slot needed, no double-counting of the repeated hour
    assert plan["slots"][0]["start"] == now.isoformat()
    d0 = plan["deadlines"][0]
    assert d0["planned_kwh"] == 2.48 and d0["pending_kwh"] == 0.0 and d0["feasible"] is True
    # Every chosen slot genuinely ends by the (tz-aware) local deadline.
    for s in plan["slots"]:
        assert datetime.fromisoformat(s["start"]) + SLOT <= ready_local


# --- 13. windows merging, solar_share_pct, advice sentence -----------------------------------

def test_windows_merge_and_advice_sentence():
    base = datetime(2026, 7, 13, 23, 0, tzinfo=AMS)  # Monday 23:00
    ready = datetime(2026, 7, 14, 7, 30, tzinfo=AMS)  # Tuesday 07:30
    # C 19.8, min 50 ⇒ E = R = 9.9 = four full slots. First two carry a solar surplus.
    slots = _slots(base, [0.10] * 4)
    p50 = {base: 2000.0, base + SLOT: 2000.0}
    plan = plan_car_charging(
        base, [_dl(ready, 50)], slots, p50,
        soc_pct=0.0, battery_net_kwh=19.8, power_kw=POWER,
    )
    assert len(plan["windows"]) == 1  # four consecutive slots merge into one plug-in window
    w = plan["windows"][0]
    assert w["start"] == base.isoformat()
    assert w["end"] == (base + 4 * SLOT).isoformat()
    assert w["battery_kwh"] == 9.9
    assert w["est_cost_eur"] == 1.1  # 4 · 0.10 · 2.75
    assert w["solar_share_pct"] == 50  # 2 of 4 slots overlap surplus → round(100·2/4)

    advice = plan["advice"]
    assert advice.startswith(f"Plug in {base:%a %H:%M}–{(base + 4 * SLOT):%H:%M}")
    assert "9.9 kWh" in advice
    assert "≈ €1.10" in advice
    assert f"to reach 50% by {ready:%a %H:%M}." in advice  # deadline day + time


def test_windows_split_when_not_consecutive():
    base = datetime(2026, 7, 13, 0, 0, tzinfo=AMS)
    prices = [0.50] * 8
    prices[0] = prices[1] = prices[5] = prices[6] = 0.05  # two separated cheap pairs
    plan = plan_car_charging(
        base, [_dl(base + timedelta(hours=8), 50)], _slots(base, prices), {},
        soc_pct=0.0, battery_net_kwh=19.8, power_kw=POWER,  # need four slots
    )
    assert len(plan["windows"]) == 2  # {0,1} and {5,6} are not contiguous → not merged
    assert plan["windows"][0]["start"] == base.isoformat()
    assert plan["windows"][1]["start"] == (base + 5 * SLOT).isoformat()


# --- 14. everything already met, and the empty schedule --------------------------------------

def test_everything_already_met_is_calm():
    now = datetime(2026, 7, 12, 20, 0, tzinfo=AMS)
    mon = datetime(2026, 7, 13, 7, 30, tzinfo=AMS)
    fri = datetime(2026, 7, 17, 7, 30, tzinfo=AMS)
    # soc 90 ≥ every minimum ⇒ E_i = 0 for all → nothing to charge.
    plan = plan_car_charging(
        now, [_dl(mon, 60), _dl(fri, 80)], _slots(now, [0.10] * 8), {},
        soc_pct=90.0, battery_net_kwh=10.0, power_kw=POWER,
    )
    assert plan["slots"] == []
    assert plan["windows"] == []
    assert plan["total_planned_kwh"] == 0.0
    assert plan["total_est_cost_eur"] == 0.0
    for d in plan["deadlines"]:
        assert d["already_met"] is True
        assert (d["required_kwh"], d["planned_kwh"]) == (0.0, 0.0)
    assert plan["advice"] == "The car already meets every scheduled minimum — no charging needed."


def test_empty_schedule_has_nothing_to_plan():
    now = datetime(2026, 7, 13, 0, 0, tzinfo=AMS)
    plan = plan_car_charging(
        now, [], _slots(now, [0.10] * 8), {}, soc_pct=50.0, battery_net_kwh=10.0, power_kw=POWER
    )
    assert plan["slots"] == [] and plan["deadlines"] == []
    assert plan["advice"] == "No car charging schedule set — nothing to plan."


def test_accepts_tuple_price_slots():
    base = datetime(2026, 7, 13, 23, 0, tzinfo=AMS)
    # Tuple (start, eur_per_kwh) input path, per the ev_advisor convention.
    tuples = [(base + i * SLOT, 0.20) for i in range(8)]
    plan = plan_car_charging(
        base, [_dl(base + timedelta(hours=8), 50)], tuples, {},
        soc_pct=0.0, battery_net_kwh=9.9, power_kw=POWER,
    )
    assert len(plan["slots"]) == 2
    assert plan["total_planned_kwh"] == 4.95


# --- 15. negative_price_hint: surfaces the unallocated free-money opportunity -----------------

def test_negative_price_hint_when_requirement_already_met():
    base = datetime(2026, 7, 14, 12, 0, tzinfo=AMS)  # Tuesday noon
    prices = [0.20] * 6
    prices[4] = prices[5] = -0.04  # Tue 13:00-13:30 goes negative
    # soc already meets the minimum -> nothing gets allocated, including the negative slots.
    plan = plan_car_charging(
        base, [_dl(base + timedelta(hours=6), 50)], _slots(base, prices), {},
        soc_pct=90.0, battery_net_kwh=9.9, power_kw=POWER,
    )
    assert plan["slots"] == []
    assert plan["negative_price_hint"] == (
        "Prices go negative Tue 13:00–13:30 — you would be PAID to top up beyond the weekly "
        "minimum."
    )


def test_negative_price_hint_none_without_negative_slots():
    base = datetime(2026, 7, 13, 23, 0, tzinfo=AMS)
    plan = plan_car_charging(
        base, [_dl(base + timedelta(hours=8), 50)], _slots(base, [0.20] * 8), {},
        soc_pct=0.0, battery_net_kwh=9.9, power_kw=POWER,
    )
    assert plan["negative_price_hint"] is None


def test_negative_price_hint_none_when_negative_slots_fully_allocated():
    base = datetime(2026, 7, 13, 0, 0, tzinfo=AMS)
    prices = [0.20] * 6
    prices[3] = prices[4] = -0.04  # exactly the two slots the requirement needs
    plan = plan_car_charging(
        base, [_dl(base + timedelta(hours=6), 50)], _slots(base, prices), {},
        soc_pct=0.0, battery_net_kwh=9.9, power_kw=POWER,  # needs exactly two full slots
    )
    assert len(plan["slots"]) == 2  # both negative slots consumed by the requirement
    assert plan["negative_price_hint"] is None


def test_negative_price_hint_picks_cheapest_of_several_unallocated_runs():
    base = datetime(2026, 7, 13, 0, 0, tzinfo=AMS)
    prices = [0.20] * 10
    prices[2] = -0.01  # a mild negative slot, single
    prices[7] = prices[8] = -0.10  # a cheaper negative run, later in the horizon
    # requirement already met -> every slot, including both negative spots, stays unallocated.
    plan = plan_car_charging(
        base, [_dl(base + timedelta(hours=2.5), 50)], _slots(base, prices), {},
        soc_pct=90.0, battery_net_kwh=9.9, power_kw=POWER,
    )
    assert plan["slots"] == []
    run_start = base + 7 * SLOT
    run_end = base + 9 * SLOT
    assert plan["negative_price_hint"] == (
        f"Prices go negative {run_start:%a %H:%M}–{run_end:%H:%M} — you would be PAID to "
        "top up beyond the weekly minimum."
    )
