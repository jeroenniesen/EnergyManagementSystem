"""Hard plan validator (SPEC §8.11) — the gate every Plan passes before it may be applied.

Energy review #4/#6: "reject plans with impossible charging, missing target, stale inputs, invalid
SoC projection, excessive switch count, sub-dwell slot churn, or missing battery capability." The
result is advisory in dry-run (surfaced in the UI) and control-blocking when live: an `unsafe`
verdict means the controller must NOT apply the plan and stays on the battery's own AUTO
(self-consumption) — never worse than "no EMS" (CLAUDE.md "fail safe").

Pure + unit-tested: no I/O. The caller supplies the current SoC, the data-quality badge, optional
capability + projection, and the same dwell/switch limits the controller enforces.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import timedelta

from ems.domain import BatteryIntent, CapabilityReport
from ems.planner.projection import ProjectedSlot
from ems.planner.schedule import Plan, PlanSlot

# Severity order: unsafe (control-blocking) > warn (degraded, still usable) > (none).
_UNSAFE, _WARN = "unsafe", "warn"
_CHARGE_INTENTS = (BatteryIntent.GRID_CHARGE_TO_TARGET,)
_EXPORT_INTENTS = (BatteryIntent.EXPORT_FOR_PROFIT,)
_DISCHARGE_POWER_INTENTS = (BatteryIntent.DISCHARGE_FOR_LOAD, BatteryIntent.EXPORT_FOR_PROFIT)
# SolidFlex OpenData per-tower ceiling — used when capability is unknown (#85 / Jeroen 2026-09-27).
ONE_UNIT_POWER_W = 2400.0
_ONE_UNIT_POWER_W = ONE_UNIT_POWER_W  # private alias for call sites in this module


@dataclass(frozen=True)
class Finding:
    severity: str  # "unsafe" | "warn"
    code: str
    message: str

    def to_dict(self) -> dict:
        return {"severity": self.severity, "code": self.code, "message": self.message}


@dataclass(frozen=True)
class PlanValidation:
    status: str  # "valid" | "warn" | "unsafe"
    findings: tuple[Finding, ...] = field(default_factory=tuple)

    @property
    def ok(self) -> bool:
        """True when the plan may be applied (no control-blocking finding)."""
        return self.status != _UNSAFE

    def to_dict(self) -> dict:
        return {"status": self.status, "ok": self.ok,
                "findings": [f.to_dict() for f in self.findings]}


def effective_power_limit_w(
    *,
    capability_w: float | None,
    settings_w: float | None,
) -> float | None:
    """Capability-driven charge/discharge ceiling: min(settings, capability) when both known.

    Prefer the live CapabilityReport over a hardcoded wattage so Gen-2 (or any device whose
    real rating exceeds the SolidFlex OpenData 2400 W/tower default) is not silently capped
    below what the probe advertised (#164)."""
    if capability_w is None and settings_w is None:
        return None
    if capability_w is None:
        return float(settings_w) if settings_w is not None else None
    if settings_w is None:
        return float(capability_w)
    return float(min(capability_w, settings_w))


def clamp_plan_power(
    plan: Plan,
    *,
    capability: CapabilityReport | None,
    settings_max_charge_w: float | None = None,
    settings_max_discharge_w: float | None = None,
) -> tuple[Plan, tuple[Finding, ...]]:
    """Clamp each slot's `power_w` to min(settings, capability) **before** §8.11 validate (#164).

    Stops a healthy catch-up/recovery plan sized at settings (e.g. 4800 W for 2×2400) from being
    rejected or warned solely because CapabilityReport under-reported the cluster total. Returns
    the (possibly replaced) plan plus warn findings when settings and capability diverge or a
    slot was clamped. Pure — no I/O."""
    findings: list[Finding] = []
    if capability is None:
        # Unknown capability: proceed cautiously at one-unit power (#85 criterion 6).
        new_slots: list[PlanSlot] = []
        changed = False
        for s in plan.slots:
            if s.power_w is None:
                new_slots.append(s)
                continue
            if s.intent not in _CHARGE_INTENTS and s.intent not in _DISCHARGE_POWER_INTENTS:
                new_slots.append(s)
                continue
            if s.power_w <= _ONE_UNIT_POWER_W + 1e-6:
                new_slots.append(s)
                continue
            new_slots.append(replace(s, power_w=_ONE_UNIT_POWER_W))
            changed = True
        if not changed:
            return plan, ()
        findings.append(Finding(
            _WARN, "capability_unknown_conservative",
            f"Battery capability unknown — proceeding cautiously at "
            f"{_ONE_UNIT_POWER_W:.0f} W (one unit).",
        ))
        return replace(plan, slots=tuple(new_slots)), tuple(findings)

    charge_limit = effective_power_limit_w(
        capability_w=capability.max_charge_w, settings_w=settings_max_charge_w,
    )
    discharge_limit = effective_power_limit_w(
        capability_w=capability.max_discharge_w, settings_w=settings_max_discharge_w,
    )

    # Debugability: surface settings vs capability divergence even when no slot needs a clamp.
    if (settings_max_charge_w is not None
            and abs(float(settings_max_charge_w) - capability.max_charge_w) > 1e-6):
        findings.append(Finding(
            _WARN, "settings_capability_power_mismatch",
            f"Settings max_charge_w={settings_max_charge_w:.0f} W diverges from capability "
            f"{capability.max_charge_w:.0f} W — clamping plan power to "
            f"{charge_limit:.0f} W (the lower figure).",
        ))
    elif (settings_max_discharge_w is not None
          and abs(float(settings_max_discharge_w) - capability.max_discharge_w) > 1e-6):
        findings.append(Finding(
            _WARN, "settings_capability_power_mismatch",
            f"Settings max_discharge_w={settings_max_discharge_w:.0f} W diverges from capability "
            f"{capability.max_discharge_w:.0f} W — clamping plan power to "
            f"{discharge_limit:.0f} W (the lower figure).",
        ))

    # #164 clamp only when settings advertise *more* than the probe (under-reported capability).
    # When settings and capability agree (or settings are lower), leave an over-ask alone so
    # validate_plan can mark power_exceeds_capability unsafe and pause (#85 criterion 5).
    charge_underreported = (
        settings_max_charge_w is not None
        and float(settings_max_charge_w) > capability.max_charge_w + 1e-6
    )
    discharge_underreported = (
        settings_max_discharge_w is not None
        and float(settings_max_discharge_w) > capability.max_discharge_w + 1e-6
    )

    new_slots: list[PlanSlot] = []
    changed = False
    for s in plan.slots:
        if s.power_w is None:
            new_slots.append(s)
            continue
        limit = charge_limit if s.intent in _CHARGE_INTENTS else discharge_limit
        if limit is None or s.power_w <= limit + 1e-6:
            new_slots.append(s)
            continue
        # Only clamp charge / export / DFL power requests — never invent power on idle/auto.
        if s.intent not in _CHARGE_INTENTS and s.intent not in _DISCHARGE_POWER_INTENTS:
            new_slots.append(s)
            continue
        underreported = (
            charge_underreported if s.intent in _CHARGE_INTENTS else discharge_underreported
        )
        if not underreported:
            # Trusted known limit — do not silently shrink; validate will pause (#85).
            new_slots.append(s)
            continue
        new_slots.append(replace(s, power_w=float(limit)))
        changed = True
        if not any(f.code == "power_clamped_to_capability" for f in findings):
            findings.append(Finding(
                _WARN, "power_clamped_to_capability",
                f"A slot requested {s.power_w:.0f} W; clamped to {limit:.0f} W "
                f"(min of settings and capability) before validate.",
            ))

    if not changed:
        return plan, tuple(findings)
    return replace(plan, slots=tuple(new_slots)), tuple(findings)


def validate_plan(
    plan: Plan,
    *,
    soc_pct: float | None,
    data_quality: str,
    min_reserve_soc: float,
    capability: CapabilityReport | None = None,
    projection: list[ProjectedSlot] | None = None,
    max_switches_per_day: int = 10,
    min_dwell: timedelta = timedelta(seconds=600),
    slot_horizon: int = 96,
    validate_projection: bool = True,
    projection_target_margin_pp: float = 5.0,
    grid_limit_w: float | None = None,
    expected_load_w: float | None = None,
    load_w_by: dict | None = None,
    settings_max_charge_w: float | None = None,
    settings_max_discharge_w: float | None = None,
    max_export_kwh_per_day: float | None = None,
    allow_export_discharge: bool = False,
) -> PlanValidation:
    """Validate `plan` against the current conditions. Returns a PlanValidation; `unsafe` ⇒ the
    controller must hold AUTO. Each check appends at most one representative finding (not one per
    slot) so the result reads as a short, actionable list.

    `validate_projection` (default on — the SPEC §8.5 gate, BACKLOG B-22 / #162) adds the
    projected-target reachability check below. A shortfall is **warn** (plan stays applicable —
    honest-partial / best-effort charge); only reserve breaches and unsafe inputs block control.

    `grid_limit_w` (SPEC §8.11 / #133) is the main-fuse ceiling. When set (>0), any grid-charge
    slot whose charge power + expected house load exceeds it is `unsafe`. Prefer per-slot
    `load_w_by`; fall back to scalar `expected_load_w`. Zero/None disables the check.

    Optional `settings_max_*_w` enrich the power-exceeds finding when settings and capability
    diverge (#164) — callers that already ran `clamp_plan_power` normally won't hit that check.

    E-11 / B-105: `EXPORT_FOR_PROFIT` requires discharge capability + reserve floor; optional
    `max_export_kwh_per_day` (>0) caps planned export; `allow_export_discharge=False` warns
    (writes stay dry-run / AUTO via intent_to_mode)."""
    findings: list[Finding] = []
    slots = plan.slots[:slot_horizon]

    # 1. Stale/missing critical inputs make any non-self-consumption action unsafe (matches the
    #    per-slot fail-safe, lifted to a plan-level gate). Unknown SoC (#134) is the same class of
    #    failure — never plan against a fabricated 0.0.
    if data_quality == "unsafe" or soc_pct is None:
        findings.append(Finding(_UNSAFE, "stale_inputs",
                                "Critical sensor data is stale or missing — holding self-use."))

    # 2. Target-SoC sanity on charge slots (the abstraction that lets us NOT default to full).
    charge = [s for s in slots if s.intent in _CHARGE_INTENTS]
    if any(s.target_soc is None for s in charge):
        findings.append(Finding(_WARN, "charge_target_unsized",
                                "A grid-charge slot has no target SoC yet — it won't execute until "
                                "sized (the driver refuses a target-less charge)."))
    for s in charge:
        t = s.target_soc
        if t is None:
            continue
        if not (0.0 <= t <= 100.0):
            findings.append(Finding(_UNSAFE, "target_out_of_range",
                                    f"Charge target {t:.0f}% is outside 0–100%."))
            break
        floor = s.floor_soc if s.floor_soc is not None else min_reserve_soc
        if t < floor:
            findings.append(Finding(_UNSAFE, "target_below_reserve",
                                    f"Charge target {t:.0f}% is below the reserve floor "
                                    f"{floor:.0f}% — impossible/contradictory."))
            break

    # 3. Power must not exceed what the battery can do (when capability is known). Check against the
    #    DIRECTION-appropriate limit — a charge slot vs max_charge_w, otherwise max_discharge_w.
    #    Prefer min(settings, capability) when settings are supplied so a divergent pair is named
    #    in the finding (#164) rather than a bare capability number.
    #    Known exceedance is control-blocking (#85 / Jeroen 2026-09-27): pause rather than fight
    #    the device. Callers that already ran clamp_plan_power normally won't hit this check.
    if capability is not None:
        for s in slots:
            if s.power_w is None:
                continue
            cap_limit = (capability.max_charge_w if s.intent in _CHARGE_INTENTS
                         else capability.max_discharge_w)
            settings_w = (settings_max_charge_w if s.intent in _CHARGE_INTENTS
                          else settings_max_discharge_w)
            limit = effective_power_limit_w(capability_w=cap_limit, settings_w=settings_w)
            if limit is None:
                continue
            if s.power_w > limit + 1e-6:
                msg = (f"A slot requests {s.power_w:.0f} W, above the battery's "
                       f"{limit:.0f} W rated power.")
                if (settings_w is not None
                        and abs(float(settings_w) - float(cap_limit)) > 1e-6):
                    msg += (f" Settings advertise {float(settings_w):.0f} W vs capability "
                            f"{cap_limit:.0f} W.")
                findings.append(Finding(_UNSAFE, "power_exceeds_capability", msg))
                break

    # 4. Excessive mode switches / sub-dwell churn — protect the battery from thrash.
    transitions = [(slots[i - 1], slots[i]) for i in range(1, len(slots))
                   if slots[i].intent is not slots[i - 1].intent]
    if len(transitions) > max_switches_per_day:
        findings.append(Finding(_WARN, "excessive_switches",
                                f"The plan switches mode {len(transitions)}× — above the "
                                f"{max_switches_per_day}/day budget."))
    if any((b.start - a.start) < min_dwell for a, b in transitions):
        findings.append(Finding(_WARN, "dwell_churn",
                                "The plan changes mode faster than the minimum dwell time."))

    # 5. Projected SoC must stay within [reserve, 100] when a projection is supplied. If the
    # battery is already below reserve, don't turn that starting condition into a false unsafe
    # finding; only block plans that make it worse. Skipped when SoC is unknown (#134).
    if projection and soc_pct is not None:
        floor = min(min_reserve_soc, soc_pct)
        if any(p.soc_pct < floor - 1e-6 for p in projection):
            findings.append(Finding(_UNSAFE, "projection_below_reserve",
                                    "The plan is projected to discharge below the reserve floor."))
        if any(p.soc_pct > 100.0 + 1e-6 for p in projection):
            findings.append(Finding(_WARN, "projection_overfill",
                                    "The plan is projected to overfill the battery."))

    # 6. Projected-target reachability (SPEC §8.5 / B-22, honest-partial #162): a plan that COMMITS
    #    to grid-charging toward a target SoC by a deadline, but whose own forward projection can't
    #    reach that target by a clear margin, is flagged. Scoped to winter grid-charge plans (a
    #    summer solar plan's target is weather-hoped, not committed). Data-quality-aware: only runs
    #    on `complete` inputs. Severity is **warn** (not unsafe): rejecting to AUTO with zero charge
    #    when the battery is already at the floor is a death spiral — prefer B-16 honest-partial
    #    (lower target, keep charging). Reserve breaches stay unsafe via check #5; stale/missing
    #    inputs stay unsafe via check #1.
    if (validate_projection and projection and data_quality == "complete"
            and plan.strategy == "winter"
            and plan.target_soc is not None and plan.deadline is not None
            and any(s.intent in _CHARGE_INTENTS for s in slots)):
        # Check each committed charge window independently.  Winter plans can span
        # multiple peaks while the plan-level deadline refers to the first one.
        commitments = {(s.deadline or plan.deadline,
                        s.target_soc if s.target_soc is not None else plan.target_soc)
                       for s in slots if s.intent in _CHARGE_INTENTS}
        for deadline, target in sorted(commitments, key=lambda x: x[0]):
            # ProjectedSlot.soc_pct is the *end* SoC of its slot.  A slot that
            # starts at the deadline has not completed by the deadline and must
            # not count toward reachability.
            reached_values = [p.soc_pct for p in projection
                              if p.start + timedelta(minutes=15) <= deadline]
            if reached_values:
                # Use the value at the latest projected instant, not the maximum
                # transient value (which can hide a drop before the deadline).
                reached = reached_values[-1]
                if reached >= target - projection_target_margin_pp:
                    continue
                when = deadline.strftime("%H:%M")
                findings.append(Finding(
                    _WARN, "projection_short_of_target",
                    f"Plan targets {target:.0f}% by {when} but projects only "
                    f"{reached:.0f}% — charging best-effort toward what's reachable "
                    f"(honest partial; not holding AUTO)."))
                break

    # 7. Grid fuse / netlimiet (#133): charge power + expected house load must not exceed the
    #    configured main-fuse ceiling. Conservative (ignores solar credit) so a cloudy slot
    #    can't trip the hoofdzekering. Mode-switch only — never a live power-tracking loop.
    #    Disabled when grid_limit_w is None/≤0. Skips a slot when charge power or load is unknown
    #    (other gates cover missing inputs; we don't invent load).
    if grid_limit_w is not None and grid_limit_w > 0:
        for s in charge:
            charge_w = s.power_w
            if charge_w is None and capability is not None:
                charge_w = effective_power_limit_w(
                    capability_w=capability.max_charge_w,
                    settings_w=settings_max_charge_w,
                )
            if charge_w is None:
                continue
            if load_w_by is not None and s.start in load_w_by:
                load_w = float(load_w_by[s.start])
            elif expected_load_w is not None:
                load_w = float(expected_load_w)
            else:
                continue
            demand_w = charge_w + max(0.0, load_w)
            if demand_w > grid_limit_w + 1e-6:
                findings.append(Finding(
                    _UNSAFE, "grid_limit_exceeded",
                    f"Grid-charge {charge_w:.0f} W + house load {load_w:.0f} W = "
                    f"{demand_w:.0f} W exceeds the grid fuse limit "
                    f"{grid_limit_w:.0f} W — holding self-use."))
                break

    # 8. EXPORT_FOR_PROFIT guardrails (E-11 / B-105 / SPEC §7.1 §8.3a / §8.11).
    export = [s for s in slots if s.intent in _EXPORT_INTENTS]
    if export:
        if not allow_export_discharge:
            findings.append(Finding(
                _WARN, "export_not_armed",
                "Plan includes EXPORT_FOR_PROFIT but allow_export_discharge is off — "
                "forced DISCHARGE writes will not run (dry-run / watch-only safe).",
            ))
        if capability is None or "discharge" not in capability.services:
            findings.append(Finding(
                _UNSAFE, "export_capability_missing",
                "Export-for-profit needs a probed discharge service — holding self-use.",
            ))
        elif capability.max_discharge_w <= 0:
            findings.append(Finding(
                _UNSAFE, "export_no_discharge_power",
                "Export-for-profit needs max_discharge_w > 0 from the capability probe.",
            ))
        elif not capability.p1_paired:
            findings.append(Finding(
                _WARN, "export_p1_unpaired",
                "P1 is not paired on the battery probe — export behaviour is less certain.",
            ))
        for s in export:
            floor = s.floor_soc if s.floor_soc is not None else min_reserve_soc
            if floor < min_reserve_soc - 1e-6:
                findings.append(Finding(
                    _UNSAFE, "export_floor_below_reserve",
                    f"Export floor {floor:.0f}% is below reserve {min_reserve_soc:.0f}% — "
                    "impossible/contradictory.",
                ))
                break
        # Optional day cap (0 / None = uncapped within reserve).
        if max_export_kwh_per_day is not None and max_export_kwh_per_day > 0:
            planned_kwh = 0.0
            for s in export:
                if s.target_kwh is not None:
                    planned_kwh += float(s.target_kwh)
                elif s.power_w is not None:
                    planned_kwh += float(s.power_w) * 0.25 / 1000.0
            if planned_kwh > max_export_kwh_per_day + 1e-6:
                findings.append(Finding(
                    _UNSAFE, "export_day_cap_exceeded",
                    f"Planned export ~{planned_kwh:.1f} kWh exceeds the day cap "
                    f"{max_export_kwh_per_day:.1f} kWh.",
                ))
        # Charge and export must not share a 15-min slot (Plan is exclusive per start, but
        # also reject adjacent same-start duplicates if a buggy planner emits them).
        starts = {}
        for s in slots:
            if s.start in starts and (
                starts[s.start] in _CHARGE_INTENTS and s.intent in _EXPORT_INTENTS
                or starts[s.start] in _EXPORT_INTENTS and s.intent in _CHARGE_INTENTS
            ):
                findings.append(Finding(
                    _UNSAFE, "export_charge_overlap",
                    "Export and grid-charge overlap in the same slot — holding self-use.",
                ))
                break
            starts[s.start] = s.intent

    status = (_UNSAFE if any(f.severity == _UNSAFE for f in findings)
              else _WARN if findings else "valid")
    return PlanValidation(status=status, findings=tuple(findings))
