"""Daily charge-need readout (SPEC §8.3 step 1, simplified).

Two flavours, both pure + unit-testable:

* `compute_charge_need` — advisory overnight budget from config (overnight load + night reserve).
  Does **not** drive the controller; the planner sizes commitments from required kWh / reachable
  slots (SPEC §7 / §8.9, P10 for commitments).
* `charge_need_from_target` — UI/on-track need from an **explicit committed** `target_soc`
  (plan header / honest partial). `/api/battery-plan` and energy-story must prefer this when the
  active plan has a target (#180), so `behind_target` never screams a fantasy ~88% while the plan
  committed 0 kWh.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

_SLOT_HOURS = 0.25  # hours in a 15-min slot (energy = power × this)


def stored_kwh_per_slot(
    max_charge_w: float, round_trip_efficiency: float, *, slot_hours: float = _SLOT_HOURS
) -> float:
    """DC energy (kWh) a single full-power charge slot adds to the pack.

    eta = √round_trip_efficiency is the charge-side share of the round trip (SPEC §4 / projection).
    Single source of the per-slot charge quantum so the winter planner (`rule_based`) and the
    missed-window catch-up (`recovery`) size a charge slot identically — the math lives here once,
    not copied into each caller."""
    eta = math.sqrt(max(1e-6, min(1.0, round_trip_efficiency)))
    return max_charge_w * slot_hours / 1000.0 * eta


@dataclass(frozen=True)
class ChargeNeed:
    usable_kwh: float
    current_soc_pct: float
    current_kwh: float
    reserve_kwh: float  # the floor we never discharge below
    target_kwh: float  # energy we want stored by the overnight deadline (incl. the reserve floor)
    target_soc_pct: float
    deficit_kwh: float  # extra energy still to store (0 when already on track)
    on_track: bool
    reason: str

    def to_dict(self) -> dict:
        return {
            "usable_kwh": round(self.usable_kwh, 2),
            "current_soc_pct": round(self.current_soc_pct, 1),
            "current_kwh": round(self.current_kwh, 2),
            "reserve_kwh": round(self.reserve_kwh, 2),
            "target_kwh": round(self.target_kwh, 2),
            "target_soc_pct": round(self.target_soc_pct, 1),
            "deficit_kwh": round(self.deficit_kwh, 2),
            "on_track": self.on_track,
            "reason": self.reason,
        }


def compute_charge_need(
    *,
    soc_pct: float,
    usable_kwh: float,
    min_reserve_soc: float,
    night_reserve_kwh: float,
    overnight_load_kwh: float,
    round_trip_efficiency: float = 1.0,
) -> ChargeNeed:
    """Target = overnight load + night reserve (converted to the DC store they need) + reserve
    floor, capped at usable capacity.

    `overnight_load_kwh` and `night_reserve_kwh` are AC energy the battery must *deliver* over the
    night; to deliver E kWh at the AC terminals the pack must hold E / eta kWh of DC (eta =
    one-way discharge efficiency = sqrt(round_trip_efficiency)). The reserve floor is already a DC
    store level, so it is added afterwards. With the default efficiency of 1.0 this reduces to the
    plain sum (so existing callers/tests are unchanged).

    Degrades safely: a non-positive `usable_kwh` yields a zeroed, on-track result with an
    explanatory reason rather than dividing by zero."""
    soc = max(0.0, min(100.0, soc_pct))
    if usable_kwh <= 0:
        return ChargeNeed(
            usable_kwh=0.0, current_soc_pct=soc, current_kwh=0.0, reserve_kwh=0.0,
            target_kwh=0.0, target_soc_pct=0.0, deficit_kwh=0.0, on_track=True,
            reason="battery capacity not configured",
        )
    eta = math.sqrt(max(1e-6, min(1.0, round_trip_efficiency)))
    reserve_kwh = usable_kwh * max(0.0, min_reserve_soc) / 100.0
    current_kwh = usable_kwh * soc / 100.0
    target_kwh = min(
        usable_kwh, (overnight_load_kwh + night_reserve_kwh) / eta + reserve_kwh
    )
    target_soc_pct = target_kwh / usable_kwh * 100.0
    deficit_kwh = max(0.0, target_kwh - current_kwh)
    on_track = deficit_kwh <= 1e-9
    if on_track:
        reason = (
            f"On track: {current_kwh:.1f} kWh stored already covers tonight's "
            f"target (~{target_kwh:.1f} kWh)."
        )
    else:
        reason = (
            f"Need ~{deficit_kwh:.1f} kWh more to reach {target_soc_pct:.0f}% before the "
            f"overnight period (load {overnight_load_kwh:.1f} + reserve {night_reserve_kwh:.1f} "
            f"+ floor {reserve_kwh:.1f} kWh)."
        )
    return ChargeNeed(
        usable_kwh=usable_kwh, current_soc_pct=soc, current_kwh=current_kwh,
        reserve_kwh=reserve_kwh, target_kwh=target_kwh, target_soc_pct=target_soc_pct,
        deficit_kwh=deficit_kwh, on_track=on_track, reason=reason,
    )


def charge_need_from_target(
    *,
    soc_pct: float,
    usable_kwh: float,
    min_reserve_soc: float,
    target_soc_pct: float,
) -> ChargeNeed:
    """ChargeNeed from a committed plan `target_soc` (honest partial / required-kWh sizing).

    Keeps UI verdicts (`behind_target`, target line, deficit) aligned with the plan header and
    decision path — never re-inflate to the advisory overnight config ceiling (#180 / #162).
    """
    soc = max(0.0, min(100.0, soc_pct))
    if usable_kwh <= 0:
        return ChargeNeed(
            usable_kwh=0.0, current_soc_pct=soc, current_kwh=0.0, reserve_kwh=0.0,
            target_kwh=0.0, target_soc_pct=0.0, deficit_kwh=0.0, on_track=True,
            reason="battery capacity not configured",
        )
    reserve_kwh = usable_kwh * max(0.0, min_reserve_soc) / 100.0
    current_kwh = usable_kwh * soc / 100.0
    target_soc = max(0.0, min(100.0, target_soc_pct))
    target_kwh = usable_kwh * target_soc / 100.0
    deficit_kwh = max(0.0, target_kwh - current_kwh)
    on_track = deficit_kwh <= 1e-9
    if on_track:
        reason = (
            f"On track: {current_kwh:.1f} kWh stored already covers the plan target "
            f"(~{target_kwh:.1f} kWh / {target_soc:.0f}%)."
        )
    else:
        reason = (
            f"Need ~{deficit_kwh:.1f} kWh more to reach the plan target of {target_soc:.0f}% "
            f"(~{target_kwh:.1f} kWh)."
        )
    return ChargeNeed(
        usable_kwh=usable_kwh, current_soc_pct=soc, current_kwh=current_kwh,
        reserve_kwh=reserve_kwh, target_kwh=target_kwh, target_soc_pct=target_soc,
        deficit_kwh=deficit_kwh, on_track=on_track, reason=reason,
    )


def ui_charge_need(
    *,
    soc_pct: float,
    usable_kwh: float,
    min_reserve_soc: float,
    night_reserve_kwh: float,
    overnight_load_kwh: float,
    round_trip_efficiency: float = 1.0,
    plan_target_soc: float | None = None,
) -> ChargeNeed:
    """Prefer the active plan's committed target when present; else the advisory overnight need.

    #180: battery-plan / energy-story on-track must mirror plan.target_soc (required kWh / honest
    partial), not a separate ambitious config night ceiling, when the planner already committed.
    """
    if plan_target_soc is not None:
        return charge_need_from_target(
            soc_pct=soc_pct, usable_kwh=usable_kwh, min_reserve_soc=min_reserve_soc,
            target_soc_pct=plan_target_soc,
        )
    return compute_charge_need(
        soc_pct=soc_pct, usable_kwh=usable_kwh, min_reserve_soc=min_reserve_soc,
        night_reserve_kwh=night_reserve_kwh, overnight_load_kwh=overnight_load_kwh,
        round_trip_efficiency=round_trip_efficiency,
    )
