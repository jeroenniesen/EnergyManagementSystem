"""Weekly digest ("the Sunday read" — BACKLOG B-58, roadmap P2 "Trust at a glance"): what you
saved, what the system did, one suggested tweak, in the advisor voice.

Pure — no clock, no I/O. The caller (`/api/digest` and the Sunday delivery job in
`ems/web/api.py`) gathers the week's finance rows (`_ensure_day_finance` per day), the week's
`Report.flows` / `Report.scores` (via `resolve_window`/`build_report`), the week's audit rows
(`AuditStore.between`) and the solar-confidence advisor's advice (`recommend_solar_confidence`);
this module only turns already-fetched data into the digest dict.

The bar (roadmap P2): a non-operator household member answers "did we do well this week?" from
the digest alone in 10 seconds — one headline sentence, one hero €, three facts, one tweak.
"""
from __future__ import annotations

import re
from datetime import date as date_cls

_MONDAY_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})\s*$")

# Roadmap: "review your export model before 2027" — net metering (saldering) is expected to phase
# out at the 2027 boundary, so the nudge starts a few months early rather than on New Year's Eve.
_EXPORT_MODEL_CUTOFF = date_cls(2026, 10, 1)

# An advisor delta below this is noise, not worth a Sunday nudge (mirrors the settings-UI advisor
# hint, which shows the number either way but only this digest gates a TWEAK on it).
_DELTA_THRESHOLD_PP = 5.0

# Audit `category` values this module looks for — see ems/storage/audit.py + the call sites in
# ems/web/api.py (`_audit_decision_loop`, `_control_tick`, `set_override`) for the exact strings.
_CAT_BATTERY_DECISION = "battery_decision"
_CAT_MANUAL_OVERRIDE = "manual_override"

# A battery_decision row counts as an ACTUAL mode switch (not a held/unconfirmed/drift row — see
# `_control_tick`'s outcome branches) when its text carries one of these markers:
#   - "— command sent"        : a live, CONFIRMED write (ems/web/api.py `_control_tick`)
#   - "Would set battery →"   : the dry-run advisory decision (`_audit_decision_loop`, dry_run)
#   - "Commanding battery →"  : the same advisory loop's (unused in practice) live-mode verb
_SWITCH_MARKERS = ("— command sent", "Would set battery →", "Commanding battery →")

# The negative-price-soak reason text, verbatim from `ems/planner/rule_based.py`'s
# `_soak_negative` ("...you are paid to charge"). Present in the summary for the dry-run advisory
# row, or in `detail.reason` for a live confirmed write.
_SOAK_MARKER = "paid to charge"


def _week_monday(week_label: str) -> date_cls | None:
    """The Monday `week_label` (as `resolve_window` labels a week: "Week of YYYY-MM-DD") names, or
    None if the label carries no parseable trailing date — callers then just skip whatever needed
    the date, rather than raising on an unexpected label."""
    m = _MONDAY_RE.search(week_label)
    if not m:
        return None
    try:
        return date_cls(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def _sum_saved(finance_rows: list[dict]) -> float | None:
    """None-safe sum of the week's `saved_eur` — None only when NO day has a figure (a day with no
    priced slots reports `saved_eur: None`, per `ems/finance.py`); otherwise sums whatever days do
    have one, exactly like `/api/finance`'s own totals."""
    vals = [r["saved_eur"] for r in finance_rows if r.get("saved_eur") is not None]
    return round(sum(vals), 2) if vals else None


def _best_day(finance_rows: list[dict]) -> dict | None:
    priced = [r for r in finance_rows if r.get("saved_eur") is not None]
    if not priced:
        return None
    best = max(priced, key=lambda r: r["saved_eur"])
    return {"date": best["day"], "saved_eur": round(best["saved_eur"], 2)}


def _co2_note(scores: list[dict]) -> str | None:
    """The co2 score's own explanation, verbatim — it already reads as a note ("Avoided 62% of a
    no-solar home's CO₂ ...", stepping down honestly once gas is folded in — see
    `ems.scores.co2_score`). None if the scores list carries no `co2` entry."""
    for s in scores:
        if s.get("key") == "co2":
            return s.get("explanation")
    return None


def _row_text(row: dict) -> str:
    """Summary + detail.reason (when present) — the reason a LIVE confirmed write's summary omits
    (see `_control_tick`'s "Battery mode X → Y — command sent", which carries the plan's reason
    only in `detail.reason`, not in the summary itself) but the dry-run advisory row carries
    inline. Checking both means a marker like "paid to charge" is found regardless of which loop
    logged it."""
    summary = str(row.get("summary", ""))
    detail = row.get("detail")
    reason = str(detail.get("reason", "")) if isinstance(detail, dict) else ""
    return f"{summary} {reason}"


def _count_actions(audit_rows: list[dict]) -> dict:
    """What the system DID this week, counted from the audit trail's own summary/detail strings
    (see the module docstring for the exact patterns this matches):
      - mode_switches: battery_decision rows that are an actual switch (confirmed write, or the
        dry-run stand-in) — NOT held/unconfirmed/cluster-drift rows, which explain inaction.
      - negative_soaks: of those switches, how many were the negative-price soak (paid to charge).
      - overrides: manual overrides SET this week (a clear isn't a new intervention, so it's not
        double-counted alongside the set it undoes)."""
    switches = 0
    soaks = 0
    overrides = 0
    for row in audit_rows:
        category = row.get("category")
        if category == _CAT_BATTERY_DECISION:
            text = _row_text(row)
            if any(marker in text for marker in _SWITCH_MARKERS):
                switches += 1
                if _SOAK_MARKER in text:
                    soaks += 1
        elif category == _CAT_MANUAL_OVERRIDE and str(row.get("summary", "")).startswith(
            "Manual override:"
        ):
            overrides += 1
    return {"mode_switches": switches, "negative_soaks": soaks, "overrides": overrides}


def _tweak(advice: dict | None, week_label: str, export_price_model: str) -> str:
    """ONE suggestion, in precedence order (roadmap P2 "one suggested tweak"):
      1. The solar-confidence advisor (`ems.analysis.recommend_solar_confidence`), when its
         suggestion differs from the current setting by >= 5 percentage points either way — a
         smaller gap is noise, not worth a Sunday nudge.
      2. The export-model reminder, once the reported week reaches October 2026 (a few months of
         runway before net metering is expected to phase out at 2027) AND the household is still
         on `net_metering`.
      3. Otherwise: nothing actionable this week — said plainly, not left blank."""
    delta = advice.get("delta_pct") if advice else None
    if delta is not None and abs(delta) >= _DELTA_THRESHOLD_PP:
        running = "low" if delta > 0 else "high"
        return (
            f"Apply the advisor suggestion — your solar confidence setting has been running "
            f"{running} lately; set solar confidence to {advice['recommended_pct']:.0f}% "
            f"(from {advice['current_pct']:.0f}%) to match what the forecast actually delivered."
        )
    monday = _week_monday(week_label)
    due = export_price_model == "net_metering" and monday is not None
    if due and monday >= _EXPORT_MODEL_CUTOFF:
        return (
            "Review your export model before 2027 — net metering (saldering) is expected to phase "
            "out then, and switching early beats switching in a hurry."
        )
    return "No tweak this week — settings look right."


def _headline(
    *, saved_eur: float | None, self_sufficiency_pct: float | None, solar_kwh: float,
    tweak_is_null: bool,
) -> str:
    """One warm, advisor-voice sentence synthesising the week — the 10-second answer to "did we do
    well?" (roadmap P2 bar). Never invents a number: a week with no priced days says so plainly
    instead of showing a false €0.00."""
    if saved_eur is None:
        lead = "No priced days yet this week, so savings can't be measured"
    else:
        sign = "−" if saved_eur < 0 else ""
        lead = f"You saved {sign}€{abs(saved_eur):.2f} this week"
    bits = []
    if self_sufficiency_pct is not None:
        bits.append(f"ran {self_sufficiency_pct:.0f}% self-sufficient")
    if solar_kwh > 0.05:
        bits.append(f"the panels made {solar_kwh:.1f} kWh")
    if bits:
        lead += ", " + " and ".join(bits)
    tail = ("Steady week — settings look right." if tweak_is_null
            else "One tweak worth a look below.")
    return f"{lead}. {tail}"


def build_digest(
    *,
    finance_rows: list[dict],
    flows: dict,
    scores: list[dict],
    audit_rows: list[dict],
    advice: dict | None,
    week_label: str,
    export_price_model: str = "net_metering",
) -> dict:
    """Assemble the weekly digest from already-gathered data (see the module docstring for what
    each argument is and who gathers it).

    `finance_rows`: this week's `/api/finance`-style day dicts (`day`, `saved_eur`, ...) — however
    many days are actually available; a day the store has no data for is simply absent, which is
    exactly what makes `days_measured`/`days_total` honest about a gap (never fabricated as €0).
    `flows` / `scores`: the WEEK-window `Report.flows` dict / `Report.scores` list (i.e.
    `build_report(..., period="week", ...)`'s output — one flows dict + 3 scores for the whole
    week, not per-day).
    `audit_rows`: the week's audit_log rows (`AuditStore.between`), oldest-or-newest-first, either
    order — only categories/substrings are inspected, order never matters.
    `advice`: `ems.analysis.recommend_solar_confidence`'s result (or None — not enough evidence
    yet), unchanged.
    """
    saved_eur = _sum_saved(finance_rows)
    days_measured = sum(1 for r in finance_rows if r.get("saved_eur") is not None)
    days_total = len(finance_rows)
    tweak = _tweak(advice, week_label, export_price_model)
    tweak_is_null = tweak.startswith("No tweak this week")
    self_sufficiency_pct = flows.get("self_sufficiency_pct")
    solar_kwh = round(float(flows.get("solar_kwh") or 0.0), 2)

    return {
        "week_label": week_label,
        "saved_eur": saved_eur,
        "best_day": _best_day(finance_rows),
        "self_sufficiency_pct": self_sufficiency_pct,
        "solar_kwh": solar_kwh,
        "co2_avoided_note": _co2_note(scores),
        "actions": _count_actions(audit_rows),
        "tweak": tweak,
        "headline": _headline(
            saved_eur=saved_eur, self_sufficiency_pct=self_sufficiency_pct, solar_kwh=solar_kwh,
            tweak_is_null=tweak_is_null,
        ),
        "days_measured": days_measured,
        "days_total": days_total,
    }
