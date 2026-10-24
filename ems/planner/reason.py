"""Structured decision-reason schema for optimization explainability (B-74 / #84).

Every plan decision carries the same factual object so web, iOS, logs and diagnostics can render
one honest story. Slice 1 defines the schema + exposes it on `/api/battery-plan`. Slice 2 renders
that same object in the web UI and includes it in logs / diagnostics export (no new schema).
Slice 3 (iOS) is out of this module.

Pure builders — no I/O, no device writes. The reason is assembled from the **same** recovered plan
the control path acts on (`plan_with_recovery` / `current_plan`) plus the factual gate outcomes of
the decide path (validator finding, failsafe, dwell, switch-cap, unconfirmed).
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any

from ems.domain import BatteryIntent
from ems.planner.buy_pool import format_empty_buy_summary
from ems.planner.projection import BatteryModel, ProjectedSlot, project_energy
from ems.planner.rule_based import stale_discharge_floor_soc
from ems.planner.schedule import Plan, PlanSlot
from ems.savings import estimate_daily_savings_eur

_CHARGE = BatteryIntent.GRID_CHARGE_TO_TARGET
MUST_CHARGE_REASON = "Voorzorgslading: batterij staat laag voor de avondpiek"
MUST_CHARGE_REASON_PREFIX = "Voorzorgslading"
_MUST_CHARGE_CEILING_MARKER = "[plafond-instelling €"


def format_must_charge_slot_reason(max_price_eur_per_kwh: float) -> str:
    return (
        f"{MUST_CHARGE_REASON} {_MUST_CHARGE_CEILING_MARKER}"
        f"{max_price_eur_per_kwh:.2f}/kWh]"
    )


def must_charge_configured_ceiling_eur(slots: tuple[PlanSlot, ...]) -> float | None:
    for slot in slots:
        if not is_must_charge_slot(slot):
            continue
        reason = slot.reason or ""
        idx = reason.find(_MUST_CHARGE_CEILING_MARKER)
        if idx < 0:
            continue
        tail = reason[idx + len(_MUST_CHARGE_CEILING_MARKER):]
        end = tail.find("/kWh]")
        if end <= 0:
            continue
        try:
            return float(tail[:end])
        except ValueError:
            continue
    return None


def is_must_charge_slot(slot: PlanSlot) -> bool:
    return (slot.reason or "").startswith(MUST_CHARGE_REASON_PREFIX)


def must_charge_slots(plan: Plan) -> tuple[PlanSlot, ...]:
    return tuple(s for s in plan.slots if is_must_charge_slot(s))


def estimate_must_charge_cost_eur(
    slots: tuple[PlanSlot, ...],
    price_by: dict[datetime, float],
) -> tuple[float, float]:
    """Return (total_kwh_ac, total_eur) for must-charge slots (AC grid energy)."""
    total_kwh = 0.0
    total_eur = 0.0
    for s in slots:
        price = price_by.get(s.start)
        if price is None:
            continue
        if s.power_w is not None and s.power_w > 0:
            kwh = float(s.power_w) * 0.25 / 1000.0
        elif s.target_kwh is not None and s.target_kwh > 0:
            kwh = float(s.target_kwh) / 0.95
        else:
            continue
        total_kwh += kwh
        total_eur += kwh * price
    return total_kwh, total_eur
_DISCHARGE = BatteryIntent.DISCHARGE_FOR_LOAD
_HOLD = BatteryIntent.HOLD_RESERVE
_SELF = BatteryIntent.ALLOW_SELF_CONSUMPTION

# Homeowner-facing action vocabulary aligned with /api/battery-plan current_action.
_ACTION_PAUSED = "paused"
_ACTION_PROCEED = "proceed"
_ACTION_HOLD = "hold"
_ACTION_DISCHARGE = "discharge"
_ACTION_GRID_CHARGE = "grid_charge"
_ACTION_FULL_SPEED = "full_speed_discharge"
_ACTION_SELF = "self_consume"
_ACTION_SOLAR_CHARGE = "solar_charge"
_BATTERY_W_IDLE_BAND_W = 50.0

# F-13 (#313): fail-safe copy when the plan badge is price_fallback (mock/backup/no Tibber).
PRICE_FALLBACK_SAFETY_CODE = "no_live_prices"
PRICE_FALLBACK_SAFETY_MESSAGE = "Geen live Tibber — inkopen uit (fail-safe)"

# F-23 / #332: cautious planning when EV or grid meter freshness is uncertain.
STALE_METER_PLAN_REASON = "Voorzichtig: meter verouderd"
_EXPORT = BatteryIntent.EXPORT_FOR_PROFIT

# Stable top-level keys of DecisionReason.to_dict() — contract for web / logs / diagnostics (#84).
REASON_DICT_KEYS = frozenset({
    "chosen_window", "rejected_alternative", "expected_benefit", "risk",
    "safety_constraint", "gates", "summary", "buy_pool",
})
GATE_DICT_KEYS = frozenset({
    "validator_code", "failsafe", "dwell", "cap_reached", "unconfirmed",
})


@dataclass(frozen=True)
class ChosenWindow:
    """The window the planner selected (typically the cheap charge block, else discharge/hold)."""

    start: datetime | None
    end: datetime | None
    intent: str | None
    label: str | None
    eur_per_kwh_min: float | None = None
    eur_per_kwh_max: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "start": self.start.isoformat() if self.start is not None else None,
            "end": self.end.isoformat() if self.end is not None else None,
            "intent": self.intent,
            "label": self.label,
            "eur_per_kwh_min": self.eur_per_kwh_min,
            "eur_per_kwh_max": self.eur_per_kwh_max,
        }


@dataclass(frozen=True)
class RejectedAlternative:
    """A concrete alternative the planner considered and rejected (or the no-trade path)."""

    intent: str | None
    reason: str
    window_start: datetime | None = None
    window_end: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent": self.intent,
            "reason": self.reason,
            "window_start": self.window_start.isoformat() if self.window_start else None,
            "window_end": self.window_end.isoformat() if self.window_end else None,
        }


@dataclass(frozen=True)
class ExpectedBenefit:
    """Expected net benefit of following the plan (€, conservative estimate)."""

    eur: float | None
    summary: str

    def to_dict(self) -> dict[str, Any]:
        return {"eur": self.eur, "summary": self.summary}


@dataclass(frozen=True)
class Risk:
    """Risk / wear margin applied when sizing the arbitrage."""

    margin_eur_per_kwh: float | None
    summary: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "margin_eur_per_kwh": self.margin_eur_per_kwh,
            "summary": self.summary,
        }


@dataclass(frozen=True)
class SafetyConstraint:
    """Why control is held or free to act — code matches the §8.11 finding when unsafe."""

    code: str | None
    message: str | None
    action: str  # "paused" | "proceed"

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "action": self.action}


@dataclass(frozen=True)
class GateOutcomes:
    """Factual outcomes of the control gates for this decision cycle."""

    validator_code: str | None
    failsafe: bool
    dwell: bool
    cap_reached: bool
    unconfirmed: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "validator_code": self.validator_code,
            "failsafe": self.failsafe,
            "dwell": self.dwell,
            "cap_reached": self.cap_reached,
            "unconfirmed": self.unconfirmed,
        }


@dataclass(frozen=True)
class DecisionReason:
    """Cross-surface structured reason for one optimization / control decision (B-74)."""

    chosen_window: ChosenWindow | None
    rejected_alternative: RejectedAlternative | None
    expected_benefit: ExpectedBenefit | None
    risk: Risk | None
    safety_constraint: SafetyConstraint
    gates: GateOutcomes
    summary: str
    buy_pool: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "chosen_window": self.chosen_window.to_dict() if self.chosen_window else None,
            "rejected_alternative": (
                self.rejected_alternative.to_dict() if self.rejected_alternative else None
            ),
            "expected_benefit": (
                self.expected_benefit.to_dict() if self.expected_benefit else None
            ),
            "risk": self.risk.to_dict() if self.risk else None,
            "safety_constraint": self.safety_constraint.to_dict(),
            "gates": self.gates.to_dict(),
            "summary": self.summary,
            "buy_pool": self.buy_pool,
        }


def _is_floor_hold_slot(slot: PlanSlot) -> bool:
    """True when the slot is a floor hold (plan/UI vocabulary #312 / F-16b)."""
    if slot.intent is not _HOLD:
        return False
    low = (slot.reason or "").lower()
    return "batterijvloer" in low or "vloer" in low


def current_action_from_slot(
    slot: PlanSlot,
    *,
    battery_w: float = 0.0,
) -> str:
    """Map the active plan slot → ``current_action`` token (aligned with /api/battery-plan).

    ``battery_w`` is the projected or observed slot power (+discharge / −charge); when omitted
    callers may pass ``slot.power_w`` — floor holds are detected from slot reason/intent (#312).
    """
    if _is_floor_hold_slot(slot):
        return _ACTION_HOLD
    intent = slot.intent
    if intent is _CHARGE:
        return _ACTION_GRID_CHARGE
    if intent is _HOLD:
        return _ACTION_HOLD
    if intent is BatteryIntent.EXPORT_FOR_PROFIT:
        return _ACTION_FULL_SPEED
    if intent is _DISCHARGE:
        if abs(battery_w) <= _BATTERY_W_IDLE_BAND_W and "vloer" in (slot.reason or "").lower():
            return _ACTION_HOLD
        return _ACTION_DISCHARGE
    if intent is _SELF:
        if battery_w < -_BATTERY_W_IDLE_BAND_W:
            return _ACTION_SOLAR_CHARGE
        return _ACTION_SELF
    return _ACTION_SELF


def format_reason_log_line(reason: dict[str, Any] | DecisionReason) -> str:
    """One compact log line from the structured reason — same facts web/diagnostics render."""
    d = reason.to_dict() if isinstance(reason, DecisionReason) else reason
    safety = d.get("safety_constraint") or {}
    benefit = d.get("expected_benefit") or {}
    chosen = d.get("chosen_window") or {}
    gates = d.get("gates") or {}
    summary = str(d.get("summary") or "-").replace("\n", " ").strip()
    bits = [
        f"action={safety.get('action') or '-'}",
        f"summary={summary!r}",
    ]
    if chosen.get("intent"):
        bits.append(f"window={chosen.get('intent')}")
    if chosen.get("label"):
        bits.append(f"label={chosen.get('label')!r}")
    eur = benefit.get("eur")
    if eur is not None:
        bits.append(f"benefit_eur={eur}")
    if safety.get("code"):
        bits.append(f"safety={safety.get('code')}")
    if gates.get("validator_code"):
        bits.append(f"validator={gates.get('validator_code')}")
    for g in ("failsafe", "dwell", "cap_reached", "unconfirmed"):
        if gates.get(g):
            bits.append(f"{g}=1")
    return "decision.reason " + " ".join(bits)


def _slot_block(slots: tuple[PlanSlot, ...], intent: BatteryIntent) -> tuple[PlanSlot, ...] | None:
    """First contiguous run of `intent` slots (the chosen window for that intent)."""
    block: list[PlanSlot] = []
    for s in slots:
        if s.intent is intent:
            block.append(s)
        elif block:
            break
    # Prefer the earliest contiguous run; if intent appears later after a gap, restart.
    if block:
        return tuple(block)
    return None


def _window_from_block(
    block: tuple[PlanSlot, ...],
    price_by: dict[datetime, float],
    *,
    label: str,
) -> ChosenWindow:
    prices = [price_by[s.start] for s in block if s.start in price_by]
    return ChosenWindow(
        start=block[0].start,
        end=block[-1].slot_end,
        intent=str(block[0].intent),
        label=label,
        eur_per_kwh_min=round(min(prices), 4) if prices else None,
        eur_per_kwh_max=round(max(prices), 4) if prices else None,
    )


def _chosen_window(plan: Plan, price_by: dict[datetime, float]) -> ChosenWindow | None:
    """Prefer the grid-charge window; else discharge; else hold; else current self-consume span."""
    if not plan.slots:
        return None
    for intent, label in (
        (_CHARGE, "cheap charge window"),
        # DISCHARGE_FOR_LOAD is vendor self-consumption serving the house — not forced dump.
        (_DISCHARGE, "expensive self-consumption window"),
        (_HOLD, "hold-reserve window"),
    ):
        block = _slot_block(plan.slots, intent)
        if block:
            return _window_from_block(block, price_by, label=label)
    # No-trade / self-consume-only plan: report the full horizon as the chosen (passive) window.
    return _window_from_block(plan.slots, price_by, label="self-consumption window")


def _rejected_alternative(plan: Plan) -> RejectedAlternative | None:
    """Derive the rejected alternative from plan slot reasons (deterministic, no second planner)."""
    if not plan.slots:
        return None
    reasons = [s.reason for s in plan.slots if s.reason]
    # No-trade day: the plan rejected arbitrage.
    for r in reasons:
        low = r.lower()
        if "no-trade" in low or "spread below" in low:
            return RejectedAlternative(
                intent=str(_CHARGE),
                reason=r.split("(")[0].strip().rstrip(":") or r,
            )
    charge = [s for s in plan.slots if s.intent is _CHARGE]
    if charge:
        # Actively arbitraging → rejected the do-nothing self-consumption alternative.
        return RejectedAlternative(
            intent=str(_SELF),
            reason="self-consumption only — rejected in favour of the selected charge window",
            window_start=charge[0].start,
            window_end=charge[-1].slot_end,
        )
    discharge = [s for s in plan.slots if s.intent is _DISCHARGE]
    if discharge:
        return RejectedAlternative(
            intent=str(_SELF),
            reason=(
                "holding through the peak — rejected in favour of self-consumption "
                "to serve house load"
            ),
            window_start=discharge[0].start,
            window_end=discharge[-1].slot_end,
        )
    return RejectedAlternative(
        intent=str(_CHARGE),
        reason="grid charge — not selected for this horizon",
    )


def _top_unsafe_code(validation: Any | None) -> tuple[str | None, str | None]:
    """Return (code, message) of the first control-blocking finding, if any."""
    if validation is None:
        return None, None
    findings = getattr(validation, "findings", None) or ()
    for f in findings:
        if getattr(f, "severity", None) == "unsafe":
            return getattr(f, "code", None), getattr(f, "message", None)
    # Fallback: first finding when status is unsafe even without severity tag.
    status = getattr(validation, "status", None)
    if status == "unsafe" and findings:
        f0 = findings[0]
        return getattr(f0, "code", None), getattr(f0, "message", None)
    return None, None


# Warn codes the waarom-UI (#85 slice 2) keys off while control still proceeds.
_WHY_WARN_CODES = frozenset({
    "capability_unknown_conservative",
    "power_clamped_to_capability",
    "settings_capability_power_mismatch",
})


def _top_why_warn_code(validation: Any | None) -> tuple[str | None, str | None]:
    """Return (code, message) of the first waarom-relevant warn finding, if any."""
    if validation is None:
        return None, None
    findings = getattr(validation, "findings", None) or ()
    for f in findings:
        if getattr(f, "severity", None) != "warn":
            continue
        code = getattr(f, "code", None)
        if code in _WHY_WARN_CODES:
            return code, getattr(f, "message", None)
    return None, None


def _homeowner_summary(raw: str | None, chosen: ChosenWindow | None) -> str:
    """Turn planner-internal slot reasons into homeowner Summary copy.

    Winter peak slots used to say ``discharge: €… > break-even €…`` — that reads as a forced
    dump next to the ``expensive self-consumption window`` label. Rewrite the bare ``discharge:``
    prefix; leave other reasons (manual override, car cover, fail-safe) untouched.
    """
    if raw:
        text = raw.strip()
        low = text.lower()
        if low.startswith("discharge:"):
            rest = text.split(":", 1)[1].strip()
            if rest:
                return f"Self-consumption while {rest}"
            return (
                chosen.label
                if chosen and chosen.label
                else "Self-consumption to serve house load"
            )
        return text
    if chosen and chosen.label:
        return chosen.label
    return "Following the current plan."


def empty_decision_reason(
    *,
    summary: str = "No plan is available yet.",
    validator_code: str | None = None,
    failsafe: bool = False,
    dwell: bool = False,
    cap_reached: bool = False,
    unconfirmed: bool = False,
    safety_message: str | None = None,
    action: str = _ACTION_PAUSED,
) -> DecisionReason:
    """Empty contract — same shape so clients never see a missing `reason` key.

    Default action is paused (no plan). Pass ``action="proceed"`` for an accepted
    override that still commands without a built plan (#85).
    """
    code = validator_code
    return DecisionReason(
        chosen_window=None,
        rejected_alternative=None,
        expected_benefit=ExpectedBenefit(eur=None, summary="No plan to estimate yet."),
        risk=Risk(margin_eur_per_kwh=None, summary="No plan risk margin yet."),
        safety_constraint=SafetyConstraint(
            code=code,
            message=safety_message or summary,
            action=action,
        ),
        gates=GateOutcomes(
            validator_code=code,
            failsafe=failsafe,
            dwell=dwell,
            cap_reached=cap_reached,
            unconfirmed=unconfirmed,
        ),
        summary=summary,
        buy_pool=None,
    )


def build_decision_reason(
    plan: Plan | None,
    *,
    price_by: dict[datetime, float] | None = None,
    validation: Any | None = None,
    plan_reason: str | None = None,
    risk_margin_eur_per_kwh: float | None = None,
    degradation_eur_per_kwh: float = 0.05,
    round_trip_efficiency: float = 0.90,
    decision_outcome: str | None = None,
    failsafe: bool = False,
    unconfirmed: bool = False,
    paused: bool = False,
    summary: str | None = None,
    data_quality: str | None = None,
) -> DecisionReason:
    """Assemble the structured reason from the recovered plan + factual gate outcomes.

    `validation` is a `PlanValidation` (or duck-typed equivalent). `decision_outcome` is the
    ModeController preview/decide outcome (`dwell` | `cap_reached` | `unconfirmed` | …).
    When `paused` or validation is unsafe, `safety_constraint.action` is `"paused"` and
    `safety_constraint.code` equals the top unsafe finding code.
    """
    price_by = price_by or {}
    validator_code, validator_message = _top_unsafe_code(validation)
    validation_ok = True
    if validation is not None:
        default_ok = getattr(validation, "status", "") != "unsafe"
        validation_ok = bool(getattr(validation, "ok", default_ok))

    dwell = decision_outcome == "dwell"
    cap_reached = decision_outcome == "cap_reached"
    if decision_outcome == "unconfirmed":
        unconfirmed = True

    # Incomplete prices hold self-use without a validator finding (#85 slice 2 waarom).
    incomplete_prices = bool(
        plan_reason and "incomplete prices" in plan_reason.lower()
    ) or bool(
        summary and "incomplete prices" in summary.lower()
    )
    price_fallback = data_quality == "price_fallback"

    is_paused = (
        paused or not validation_ok or failsafe or incomplete_prices or price_fallback
    )
    if plan is None:
        pf_msg = PRICE_FALLBACK_SAFETY_MESSAGE if price_fallback else None
        return empty_decision_reason(
            summary=pf_msg or summary or plan_reason or "No plan is available yet.",
            validator_code=validator_code or (
                PRICE_FALLBACK_SAFETY_CODE if price_fallback
                else ("incomplete_prices" if incomplete_prices else None)
            ),
            failsafe=failsafe or incomplete_prices or price_fallback,
            dwell=dwell,
            cap_reached=cap_reached,
            unconfirmed=unconfirmed,
            safety_message=pf_msg or validator_message,
        )

    chosen = _chosen_window(plan, price_by)
    rejected = _rejected_alternative(plan)
    savings = estimate_daily_savings_eur(
        plan, price_by,
        efficiency=round_trip_efficiency,
        degradation_eur_per_kwh=degradation_eur_per_kwh,
        risk_margin_eur_per_kwh=(
            risk_margin_eur_per_kwh if risk_margin_eur_per_kwh is not None else 0.02
        ),
    )
    benefit = ExpectedBenefit(
        eur=savings,
        summary=(
            f"Estimated net benefit ≈ €{savings:.2f} for this plan."
            if savings > 0
            else "No positive arbitrage benefit estimated for this plan."
        ),
    )
    risk = Risk(
        margin_eur_per_kwh=risk_margin_eur_per_kwh,
        summary=(
            f"Risk margin €{risk_margin_eur_per_kwh:.3f}/kWh applied to break-even."
            if risk_margin_eur_per_kwh is not None
            else "No explicit risk margin configured."
        ),
    )

    if is_paused:
        if price_fallback:
            pause_code = PRICE_FALLBACK_SAFETY_CODE
            pause_message = PRICE_FALLBACK_SAFETY_MESSAGE
        else:
            pause_code = validator_code or (
                "incomplete_prices" if incomplete_prices else None
            )
            pause_message = (
                validator_message or plan_reason or summary or "Plan paused safely."
            )
        safety = SafetyConstraint(
            code=pause_code,
            message=pause_message,
            action=_ACTION_PAUSED,
        )
    else:
        # Surface cautious-continue warn codes for the waarom UI (#85 slice 2) while
        # keeping action=proceed — unknown capability / clamped power are not pauses.
        warn_code, warn_message = _top_why_warn_code(validation)
        safety = SafetyConstraint(
            code=warn_code,
            message=warn_message,
            action=_ACTION_PROCEED,
        )

    gates = GateOutcomes(
        validator_code=validator_code,
        failsafe=failsafe,
        dwell=dwell,
        cap_reached=cap_reached,
        unconfirmed=unconfirmed,
    )
    buy_pool_dict = plan.buy_pool.to_dict() if plan.buy_pool is not None else None
    mc = must_charge_slots(plan)
    if mc and price_by:
        kwh, eur = estimate_must_charge_cost_eur(mc, price_by)
        avg = eur / kwh if kwh > 1e-9 else 0.0
        ceiling = must_charge_configured_ceiling_eur(mc)
        if ceiling is None:
            ceiling = max(price_by.get(s.start, 0.0) for s in mc)
        summary_mc = (
            f"Voorzorgslading: circa {kwh:.1f} kWh × €{avg:.2f}/kWh "
            f"(plafond €{ceiling:.2f}/kWh)"
        )
        buy_pool_dict = dict(buy_pool_dict or {})
        buy_pool_dict["must_charge_summary"] = summary_mc.replace(".", ",")
        buy_pool_dict["must_charge_ceiling_eur_per_kwh"] = round(ceiling, 4)
    if (
        not price_fallback
        and plan.buy_pool is not None
        and plan.buy_pool.state == "empty"
        and plan.buy_pool.shortfall_kwh > 1e-9
        and not mc
    ):
        text = format_empty_buy_summary(plan.buy_pool)
    elif mc:
        text = (
            buy_pool_dict.get("must_charge_summary", MUST_CHARGE_REASON)
            if buy_pool_dict else MUST_CHARGE_REASON
        )
    else:
        text = (
            PRICE_FALLBACK_SAFETY_MESSAGE if price_fallback
            else _homeowner_summary(summary or plan_reason, chosen)
        )
    if plan is not None and plan_has_stale_meter_reason(plan):
        if STALE_METER_PLAN_REASON not in text:
            text = f"{STALE_METER_PLAN_REASON}. {text}"
    return DecisionReason(
        chosen_window=chosen,
        rejected_alternative=rejected,
        expected_benefit=benefit,
        risk=risk,
        safety_constraint=safety,
        gates=gates,
        summary=text,
        buy_pool=buy_pool_dict,
    )


def stale_meter_reason_prefix(reason: str) -> str:
    """Prefix slot/plan copy when cautious stale-meter planning is active."""
    text = (reason or "").strip()
    if STALE_METER_PLAN_REASON in text:
        return text
    return f"{STALE_METER_PLAN_REASON} — {text}" if text else STALE_METER_PLAN_REASON


def is_aggressive_discharge_slot(slot: PlanSlot) -> bool:
    """EXPORT or price-driven discharge slots trimmed when a meter is stale (#332)."""
    if slot.intent is _EXPORT:
        return True
    if slot.intent is not _DISCHARGE:
        return False
    low = (slot.reason or "").lower()
    return (
        low.startswith("discharge:")
        or low.startswith("shave the €")
        or low.startswith("self-consumption:")
    )


def _stale_hold_slot_for_projection(
    slot: PlanSlot,
    proj: ProjectedSlot,
    *,
    effective_floor: float,
) -> bool:
    """True when operating the slot would end SoC below the stale discharge floor."""
    if proj.soc_pct >= effective_floor:
        return False
    if proj.battery_w <= 0:
        return False
    if slot.intent is _EXPORT:
        return True
    if slot.intent is _SELF:
        return True
    return slot.intent is _DISCHARGE and is_aggressive_discharge_slot(slot)


def apply_stale_meter_caution(
    plan: Plan,
    *,
    base_reserve_soc_pct: float,
    buffer_pp: float,
    start_soc_pct: float,
    projection_model: BatteryModel,
    solar_w_by: dict[datetime, float],
    load_w_by: dict[datetime, float],
) -> Plan:
    """Raise discharge floor; hold slots that would discharge below it — never add grid charge."""
    effective_floor = stale_discharge_floor_soc(
        base_reserve_soc_pct, buffer_pp=buffer_pp,
    )
    baseline_discharge = {
        s.start for s in plan.slots if s.intent is _DISCHARGE or s.intent is _EXPORT
    }
    out: list[PlanSlot] = []
    running_soc = start_soc_pct
    for s in plan.slots:
        charge_cap = s.target_soc if s.target_soc is not None else None
        proj = project_energy(
            [s],
            start_soc_pct=running_soc,
            solar_w_by=solar_w_by,
            load_w_by=load_w_by,
            model=projection_model,
            charge_target_soc_pct=charge_cap,
        )[0]
        if s.intent is _CHARGE:
            floor_soc = s.floor_soc
        else:
            floor_soc = effective_floor
            if s.floor_soc is not None:
                floor_soc = max(float(s.floor_soc), effective_floor)
        intent = s.intent
        reason = stale_meter_reason_prefix(s.reason)
        power_w = s.power_w
        if _stale_hold_slot_for_projection(s, proj, effective_floor=effective_floor):
            intent = _HOLD
            power_w = None
            reason = stale_meter_reason_prefix(
                "hold reserve while meter data is uncertain",
            )
            proj = project_energy(
                [replace(s, intent=_HOLD, power_w=None)],
                start_soc_pct=running_soc,
                solar_w_by=solar_w_by,
                load_w_by=load_w_by,
                model=projection_model,
                charge_target_soc_pct=charge_cap,
            )[0]
        running_soc = proj.soc_pct
        out.append(replace(
            s, intent=intent, reason=reason, floor_soc=floor_soc,
            target_soc=s.target_soc, target_kwh=s.target_kwh, power_w=power_w,
            deadline=s.deadline, end=s.end,
        ))
    discharge_after = {
        s.start for s in out if s.intent is _DISCHARGE or s.intent is _EXPORT
    }
    assert discharge_after <= baseline_discharge
    return replace(plan, slots=tuple(out))


def plan_has_stale_meter_reason(plan: Plan | None) -> bool:
    if plan is None or not plan.slots:
        return False
    return any(STALE_METER_PLAN_REASON in (s.reason or "") for s in plan.slots)
