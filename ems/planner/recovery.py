"""Charge-completion & missed-window recovery (SPEC §8.12, BACKLOG B-16).

Two pure pieces the control loop leans on when a committed grid-charge plan slips:

1. `check_charge_completion(plan, now, soc_pct)` — is the plan's charge on-pace, a little behind
   (tolerated), or genuinely MISSED? "Missed" is the costly one: the cheap window came and went
   (an outage, a run of held decisions, or prices that spiked so the arbitrage pool emptied) and
   the battery is still short of the target it committed to before the deadline — the "woke up
   short before the morning peak" failure.

2. `build_catch_up_plan(...)` — when a window was missed and the deadline is STILL ahead, reshape
   the plan to charge in the cheapest REMAINING slots before the deadline, sized to the energy
   still short. Not enough hours left to reach the full target ⇒ an honest PARTIAL target (never a
   fantasy), with a note the wiring can surface.

Both are pure + deterministic (no I/O, no clock reads). Recovery only ever ADDS charging toward an
already-committed, already-validated target: the recovered `Plan` goes back through the SAME §8.11
validator (including the B-22 projection_short_of_target gate) and the SAME control-layer caps/
dwell — it bypasses nothing. It also never touches strategy selection, so the §8.4 seasonal
hysteresis counter is untouched: recovery reshapes the CURRENT strategy's charge slots, it never
re-picks summer vs winter.

Sizing reuses `charge_need.stored_kwh_per_slot` — the per-slot charge quantum is defined once.

The grid-charge catch-up is scoped (in `recover_if_needed`) to the WINTER arbitrage strategy: only
there is `target_soc` a *committed* grid-charge target. A summer solar-first target is filled by the
day's PV, so grid-charging to "catch it up" would over-buy — recovery leaves summer plans untouched.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime

from ems.domain import BatteryIntent
from ems.planner.charge_need import stored_kwh_per_slot
from ems.planner.schedule import SLOT, Plan, PlanSlot
from ems.sources.prices import PriceSlot

# Completion states. Only MISSED asks the wiring to act; BEHIND is a tolerated in-window lag.
ON_PACE = "on_pace"
BEHIND = "behind"
MISSED = "missed"
COMPLETE = "complete"
NOT_APPLICABLE = "not_applicable"

_CHARGE = BatteryIntent.GRID_CHARGE_TO_TARGET


@dataclass(frozen=True)
class CompletionStatus:
    """The verdict of `check_charge_completion`. `gap_pp` is target − current SoC (percentage
    points; 0 when at/above target). `reason` is plain, human-readable."""

    status: str
    gap_pp: float
    reason: str

    @property
    def needs_recovery(self) -> bool:
        """Only a MISSED window triggers a catch-up. on-pace / behind (within margin) / complete /
        not-applicable all pass untouched — recovery never fights a plan that is still on track."""
        return self.status == MISSED

    def to_dict(self) -> dict:
        return {"status": self.status, "gap_pp": round(self.gap_pp, 1), "reason": self.reason,
                "needs_recovery": self.needs_recovery}


def _charge_slots(plan: Plan) -> list[PlanSlot]:
    return [s for s in plan.slots if s.intent is _CHARGE]


def _window_label(plan: Plan) -> str:
    """"02:00–04:00" from the plan's own charge slots, or a generic phrase when the plan committed
    to a target but scheduled no charge slots at all (the arbitrage pool was priced out)."""
    charge = _charge_slots(plan)
    if not charge:
        return "the cheap window"
    return f"{min(s.start for s in charge):%H:%M}–{max(s.slot_end for s in charge):%H:%M}"


def check_charge_completion(
    plan: Plan, now: datetime, soc_pct: float, *, margin_pp: float = 5.0
) -> CompletionStatus:
    """Classify the plan's committed charge as on-pace / behind / missed / complete (SPEC §8.12).

    Keys off the PLAN-LEVEL `target_soc` + `deadline` (a committed grid-charge target), not on
    charge slots existing — because the exact failure we care about is a plan that committed to a
    target but, priced out or knocked offline, has NO charge slots left before the deadline.

    MISSED (⇒ recovery) means: still short of target by more than `margin_pp`, the deadline is
    still ahead, and either the deadline has arrived OR nothing is scheduled to charge before it
    OR we have clearly fallen off the pace ramp with little runway left. BEHIND is a short-but-
    tolerated in-window lag (there is still enough planned charging ahead) and passes."""
    target, deadline = plan.target_soc, plan.deadline
    if target is None or deadline is None:
        # No committed grid-charge target ⇒ nothing to recover (e.g. a no-trade or pure-solar plan
        # sets neither; only a plan that intends to charge toward a level by a time carries both).
        return CompletionStatus(NOT_APPLICABLE, 0.0, "no committed charge target to track")

    gap = target - soc_pct
    if gap <= 1e-9:
        return CompletionStatus(COMPLETE, 0.0,
                                f"reached the {target:.0f}% target — charge complete")
    if gap <= margin_pp:
        return CompletionStatus(ON_PACE, gap,
                                f"within {margin_pp:.0f} pp of the {target:.0f}% target")

    future_charge = [s for s in _charge_slots(plan) if s.slot_end > now and s.start < deadline]
    if now >= deadline:
        return CompletionStatus(
            MISSED, gap,
            f"deadline {deadline:%H:%M} passed still {gap:.0f} pp short of {target:.0f}%")
    if not future_charge:
        return CompletionStatus(
            MISSED, gap,
            f"{gap:.0f} pp short of {target:.0f}% with nothing scheduled to charge before "
            f"{deadline:%H:%M}")

    # Charge slots remain and the deadline is ahead — judge the pace. The tolerated gap ramps from
    # the full target (window start: any level is fine, charging hasn't happened) down to the margin
    # (deadline: must be there). Falling below that ramp with little runway left ⇒ MISSED.
    window_start = min(s.start for s in _charge_slots(plan))
    span = (deadline - window_start).total_seconds()
    frac = 0.0 if span <= 0 else max(0.0, min(1.0, (now - window_start).total_seconds() / span))
    tolerated = margin_pp + (1.0 - frac) * (target - margin_pp)
    if gap > tolerated:
        return CompletionStatus(
            MISSED, gap,
            f"{gap:.0f} pp short of {target:.0f}% and off pace for {deadline:%H:%M}")
    return CompletionStatus(BEHIND, gap,
                            f"{gap:.0f} pp short of {target:.0f}%, still on the ramp to "
                            f"{deadline:%H:%M}")


@dataclass(frozen=True)
class CatchUpResult:
    """The outcome of `build_catch_up_plan`. `plan` is the reshaped, still-to-be-validated Plan.
    `feasible` is True when the full committed target is reachable in the remaining slots; False
    ⇒ an honest partial (`target_soc` lowered to what's achievable, `kwh_short` names the gap).
    `reason` is the audit line; `note` is the plain, calm sentence for the notification."""

    plan: Plan
    feasible: bool
    target_soc: float
    kwh_short: float
    slots_used: int
    reason: str
    note: str


def build_catch_up_plan(
    plan: Plan,
    now: datetime,
    *,
    soc_pct: float,
    prices: list[PriceSlot],
    usable_kwh: float,
    reserve_soc_pct: float,
    max_charge_w: float,
    round_trip_efficiency: float,
) -> CatchUpResult:
    """Reshape `plan` to catch up a missed charge window (SPEC §8.12).

    Buys the CHEAPEST remaining price slots before the plan's `deadline`, sized to the DC energy
    still short of the committed `target_soc` (per-slot quantum from
    `charge_need.stored_kwh_per_slot` — the sizing math is not duplicated here). Slots the plan is
    discharging into (an evening/morning peak) are never overwritten with a charge. Not enough
    cheap slots before the deadline ⇒ a partial: use all of them and lower `target_soc` to the
    achievable level so the returned
    plan stays self-consistent and PASSES the §8.11 projection gate (an honest partial, not a
    target it cannot hit).

    `plan.target_soc` and `plan.deadline` MUST be set (guaranteed by `check_charge_completion`
    returning MISSED). Pure — the caller validates, audits and installs the result."""
    assert plan.target_soc is not None and plan.deadline is not None
    target, deadline = plan.target_soc, plan.deadline
    reserve = max(0.0, reserve_soc_pct)

    slot_kwh = stored_kwh_per_slot(max_charge_w, round_trip_efficiency)
    remaining_dc = max(0.0, (target - soc_pct) / 100.0 * usable_kwh)
    n_needed = math.ceil(remaining_dc / slot_kwh) if slot_kwh > 0 and remaining_dc > 1e-9 else 0

    # Candidate slots: still ahead (slot_end > now), before the deadline, and NOT a peak the plan is
    # discharging into. Cheapest first, ties by earliest start (deterministic).
    discharging = {s.start for s in plan.slots if s.intent is BatteryIntent.DISCHARGE_FOR_LOAD}
    candidates = sorted(
        (p for p in prices
         if p.start + SLOT > now and p.start < deadline and p.start not in discharging),
        key=lambda p: (p.eur_per_kwh, p.start),
    )

    label = _window_label(plan)
    remaining_kwh = round(remaining_dc, 1)
    if len(candidates) >= n_needed:
        chosen = candidates[:n_needed]
        feasible, recovered_target, kwh_short = True, target, 0.0
        reason = (f"catch-up: missed {label} ({remaining_kwh:.1f} kWh short), using "
                  f"{n_needed} remaining cheap slot(s) to reach {target:.0f}% by {deadline:%H:%M}")
        note = (f"EMS missed the {label} charge window, so it will top up in the cheapest "
                f"{n_needed} slot(s) before {deadline:%H:%M}. Your battery is safe; nothing to do.")
    else:
        chosen = candidates
        achievable_dc = len(chosen) * slot_kwh
        recovered_target = min(target, max(reserve, soc_pct + achievable_dc / usable_kwh * 100.0)) \
            if usable_kwh > 0 else reserve
        feasible = False
        kwh_short = round(max(0.0, remaining_dc - achievable_dc), 1)
        reason = (f"catch-up (partial): missed {label} ({remaining_kwh:.1f} kWh short) but only "
                  f"{len(chosen)} cheap slot(s) before {deadline:%H:%M} — targeting "
                  f"{recovered_target:.0f}% (not {target:.0f}%), {kwh_short:.1f} kWh short")
        note = (f"EMS missed the {label} charge window and can only partly catch up before "
                f"{deadline:%H:%M} (about {kwh_short:.1f} kWh short of the {target:.0f}% target). "
                f"Your battery is safe; it will charge as much as it economically can.")

    chosen_starts = {p.start for p in chosen}
    per_slot_kwh = round(slot_kwh, 3)
    out: list[PlanSlot] = []
    for s in plan.slots:
        if s.start in chosen_starts:
            out.append(PlanSlot(
                s.start, _CHARGE,
                f"catch-up charge: cheapest remaining slot before {deadline:%H:%M}",
                target_soc=recovered_target, target_kwh=per_slot_kwh, power_w=max_charge_w,
                floor_soc=reserve, deadline=deadline, end=s.end,
            ))
        elif s.intent is _CHARGE:
            # The plan's ORIGINAL charge slots are superseded by the catch-up set (they were the
            # missed/priced-out window, or a slot we didn't re-pick) — drop them to self-consumption
            # so the recovered plan charges in EXACTLY the chosen slots, nothing double-counted.
            out.append(PlanSlot(s.start, BatteryIntent.ALLOW_SELF_CONSUMPTION,
                                "self-consumption: superseded by catch-up window",
                                floor_soc=s.floor_soc, end=s.end))
        else:
            out.append(s)

    recovered = Plan(created_at=now, slots=tuple(out), strategy=plan.strategy,
                     target_soc=recovered_target, deadline=deadline)
    return CatchUpResult(
        plan=recovered, feasible=feasible, target_soc=recovered_target, kwh_short=kwh_short,
        slots_used=len(chosen), reason=reason, note=note,
    )


def recover_if_needed(
    plan: Plan,
    now: datetime,
    *,
    soc_pct: float,
    prices: list[PriceSlot],
    usable_kwh: float,
    reserve_soc_pct: float,
    max_charge_w: float,
    round_trip_efficiency: float,
    enabled: bool = True,
    margin_pp: float = 5.0,
) -> tuple[Plan, CompletionStatus, CatchUpResult | None]:
    """Pure control-loop entry point: return the plan to act on this cycle plus the diagnosis.

    When recovery is enabled and the charge is MISSED, returns the catch-up plan (which still flows
    through the caller's §8.11 validator + control caps — it is not applied here); otherwise returns
    the plan unchanged. Deterministic, so `_current_plan` can call it every cycle and the UI, the
    validator and the controller all see the SAME plan. Side effects (audit, notify, KV dedupe) are
    the caller's — this only decides.

    Scoped to the WINTER (arbitrage) strategy: only there is `target_soc` a *committed* grid-charge
    target worth topping up from the grid. A summer solar-first plan's target is weather-hoped
    (filled by the day's PV, not the grid), so grid-charging to "catch it up" would over-buy —
    exactly the failure the §8.11 B-22 gate also declines to touch for summer plans. Recovery never
    re-picks the season, so the §8.4 hysteresis counter is untouched."""
    status = check_charge_completion(plan, now, soc_pct, margin_pp=margin_pp)
    if not enabled or not status.needs_recovery or plan.strategy != "winter":
        return plan, status, None
    catch_up = build_catch_up_plan(
        plan, now, soc_pct=soc_pct, prices=prices, usable_kwh=usable_kwh,
        reserve_soc_pct=reserve_soc_pct, max_charge_w=max_charge_w,
        round_trip_efficiency=round_trip_efficiency,
    )
    return catch_up.plan, status, catch_up
