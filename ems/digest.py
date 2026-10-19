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

# Driver keys surfaced in `negative_days` (audit F-49 / #325). Each amount is derived only from
# rows the digest gather already loads — never invented:
#   grid_charge_cost        — finance.battery_contribution_eur + energy.grid_to_battery
#   ev_import_during_hold   — energy.grid_to_car / battery_to_car + audit hold_reserve that day
#   expensive_peak_import   — finance.avoided_expensive_eur when negative (priced peak import)
#   low_spread              — audit/plan text for no-trade below planner.daily_min_savings_eur
_NO_TRADE_MARKERS = ("geen handel", "no-trade", "daily_min", "hele dag no-trade")
_PROJECTED_SAVINGS_RE = re.compile(
    r"geprojecteerde besparing €\s*([0-9]+(?:[.,][0-9]+)?)", re.IGNORECASE)
_HOLD_MARKERS = ("hold_reserve", "hold reserve", "Manual override: hold_reserve")

_DUTCH_WEEKDAYS = (
    "maandag", "dinsdag", "woensdag", "donderdag", "vrijdag", "zaterdag", "zondag",
)


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


def _unstable_advisor_line(advice: dict) -> str:
    """The explainability line shown INSTEAD of the advisor nudge when its suggestion would move
    the setting >= 5pp but the recommendation itself is not yet SETTLED (flips day-to-day). We hold
    rather than send the user chasing a number that will change next week, and we say why — the
    "explain why it is NOT acting" side of the explainability-first principle."""
    spread = advice.get("spread_pp")
    days = advice.get("window_days")
    if isinstance(spread, int | float):
        detail = f"spread {spread:.0f}pp over {days} days"
    else:
        detail = "not enough daily history yet"
    return f"Advisor recommendation unstable ({detail}) — holding until it settles."


def _tweak(
    advice: dict | None, week_label: str, export_price_model: str
) -> tuple[str | None, bool]:
    """The digest's one settings line + whether it is an ACTIONABLE tweak, in precedence order
    (roadmap P2 "one suggested tweak"):
      1. The solar-confidence advisor (`ems.analysis.recommend_solar_confidence`), when its
         suggestion differs from the current setting by >= 5 percentage points either way — a
         smaller gap is noise, not worth a Sunday nudge. Surfaced as an actionable nudge ONLY when
         the recommendation is STABLE (`advice["stable"]`); when it would nudge but is unstable, we
         return the hold/explain line instead — non-actionable, so the headline stays calm.
      2. The export-model reminder, once the reported week reaches October 2026 (a few months of
         runway before net metering is expected to phase out at 2027) AND the household is still
         on `net_metering`.
      3. Otherwise: nothing actionable this week — said plainly, not left blank.

    Returns `(line, is_actionable)`. `is_actionable` drives the headline tail ("One tweak worth a
    look below." vs. the calm "Steady week — settings look right."), so an unstable-advisor HOLD
    line is shown without the headline pretending there's an action to take."""
    delta = advice.get("delta_pct") if advice else None
    if advice is not None and delta is not None and abs(delta) >= _DELTA_THRESHOLD_PP:
        # `stable` is absent on legacy/older advice dicts — treat only an EXPLICIT False as
        # unstable, so pre-stability-gate callers keep nudging exactly as before.
        if advice.get("stable") is False:
            return _unstable_advisor_line(advice), False
        running = "low" if delta > 0 else "high"
        return (
            f"Apply the advisor suggestion — your solar confidence setting has been running "
            f"{running} lately; set solar confidence to {advice['recommended_pct']:.0f}% "
            f"(from {advice['current_pct']:.0f}%) to match what the forecast actually delivered."
        ), True
    monday = _week_monday(week_label)
    due = export_price_model == "net_metering" and monday is not None
    if due and monday >= _EXPORT_MODEL_CUTOFF:
        return (
            "Review your export model before 2027 — net metering (saldering) is expected to phase "
            "out then, and switching early beats switching in a hurry."
        ), True
    # Null case: return None — the headline's "Steady week — settings look right." tail already
    # says it, and rendering BOTH duplicated the sentence in production. Calm = absence.
    return None, False


def _marginal_import_eur_per_kwh(finance: dict) -> float | None:
    imp = finance.get("grid_import_kwh")
    cost = finance.get("grid_cost_eur")
    if imp is None or cost is None or imp < 0.05:
        return None
    return cost / imp


def _hold_active_that_day(audit_rows: list[dict]) -> bool:
    for row in audit_rows:
        text = f"{row.get('summary', '')} {_row_text(row)}".lower()
        if any(m.lower() in text for m in _HOLD_MARKERS):
            return True
    return False


def _fmt_eur_nl(amount: float) -> str:
    """€1,20 — comma decimal only; leave sentence periods alone."""
    return f"€{abs(amount):.2f}".replace(".", ",")


def _fmt_kwh_nl(kwh: float) -> str:
    return f"{kwh:.1f}".replace(".", ",")


def _low_spread_text(eur: float, *, names_spaarvloer: bool, daily_min: float) -> str:
    amt = _fmt_eur_nl(eur)
    if names_spaarvloer:
        floor = _fmt_eur_nl(daily_min)
        return (
            f"Krappe prijs-spread: handel onder de spaarvloer "
            f"(instelling Min. verwachte besparing per dag, nu {floor}) kostte ca. {amt}."
        )
    return (
        f"Krappe prijs-spread: weinig te verdienen met laden/ontladen kostte ca. {amt}."
    )


def _driver_text(spec: dict, *, names_spaarvloer: bool, daily_min: float) -> str:
    """Dutch sentence for a driver. `eur` is the capped amount (after the top-3 scale)."""
    eur = float(spec["eur"])
    amt = _fmt_eur_nl(eur)
    key = spec["key"]
    if key == "grid_charge_cost":
        return (
            f"Netladen van de batterij ({_fmt_kwh_nl(spec['kwh'])} kWh van het net) "
            f"kostte ca. {amt}."
        )
    if key == "expensive_peak_import":
        return (
            f"Import tijdens dure uren kostte ca. {amt} extra "
            f"(ten opzichte van zonder batterij)."
        )
    if key == "ev_import_during_hold":
        return (
            f"EV laadde ({_fmt_kwh_nl(spec['kwh'])} kWh) terwijl de batterij vastgehouden werd "
            f"— ca. {amt} netimport."
        )
    if key == "low_spread":
        return _low_spread_text(eur, names_spaarvloer=names_spaarvloer, daily_min=daily_min)
    return amt


def explain_negative_day(
    day_rows: dict,
    *,
    daily_min_savings_eur: float = 0.20,
    low_spread_names_spaarvloer: bool = False,
) -> list[dict]:
    """Top loss drivers for one day (audit F-49). `day_rows` carries:
      - `finance`: one `/api/finance`-style day dict (`saved_eur`, B-36 breakdown, kWh, costs)
      - `audit`: that day's audit_log rows (category/summary/detail — same store as the week view)
      - `energy`: optional day `Report.flows` dict (`grid_to_battery`, `grid_to_car`, …)

    Returns up to three `{key, eur, text}` items sorted by |eur| desc. Amounts are capped so their
    sum is ≤ |saved_eur| + €0.05, and `text` cites that same capped euro (NL comma). Missing inputs
    → `[]` (callers show 'Oorzaak niet te bepalen')."""
    finance = day_rows.get("finance") or {}
    saved = finance.get("saved_eur")
    if saved is None or saved >= -1e-9:
        return []
    loss = abs(float(saved))
    energy = day_rows.get("energy") or {}
    audit_rows = list(day_rows.get("audit") or [])
    candidates: list[dict] = []

    grid_to_batt = float(energy.get("grid_to_battery") or 0.0)
    batt_contrib = finance.get("battery_contribution_eur")
    if grid_to_batt > 0.05 and isinstance(batt_contrib, int | float) and not isinstance(
        batt_contrib, bool,
    ) and batt_contrib < -0.01:
        eur = round(min(loss, abs(batt_contrib)), 2)
        if eur > 0:
            candidates.append({"key": "grid_charge_cost", "eur": eur, "kwh": grid_to_batt})

    avoided = finance.get("avoided_expensive_eur")
    if isinstance(avoided, int | float) and not isinstance(avoided, bool) and avoided < -0.01:
        eur = round(min(loss, abs(avoided)), 2)
        if eur > 0:
            candidates.append({"key": "expensive_peak_import", "eur": eur})

    hold = _hold_active_that_day(audit_rows)
    grid_to_car = float(energy.get("grid_to_car") or 0.0)
    batt_to_car = float(energy.get("battery_to_car") or 0.0)
    ev_kwh = grid_to_car + batt_to_car
    if hold and ev_kwh > 0.05:
        marginal = _marginal_import_eur_per_kwh(finance)
        if marginal is not None:
            eur = round(min(loss, ev_kwh * marginal), 2)
            if eur > 0:
                candidates.append({"key": "ev_import_during_hold", "eur": eur, "kwh": ev_kwh})

    audit_blob = " ".join(_row_text(r) for r in audit_rows).lower()
    if any(marker in audit_blob for marker in _NO_TRADE_MARKERS):
        projected: float | None = None
        m = _PROJECTED_SAVINGS_RE.search(audit_blob)
        if m:
            try:
                projected = float(m.group(1).replace(",", "."))
            except ValueError:
                projected = None
        if projected is not None and daily_min_savings_eur > projected + 1e-9:
            eur = round(min(loss, daily_min_savings_eur - projected), 2)
            if eur > 0:
                candidates.append({"key": "low_spread", "eur": eur})

    if not candidates:
        return []

    candidates.sort(key=lambda d: abs(d["eur"]), reverse=True)
    picked = candidates[:3]
    total = sum(d["eur"] for d in picked)
    cap = loss + 0.05
    if total > cap and total > 0:
        scale = loss / total
        for d in picked:
            d["eur"] = round(d["eur"] * scale, 2)
    out: list[dict] = []
    for d in picked:
        if d["eur"] <= 0:
            continue
        out.append({
            "key": d["key"],
            "eur": d["eur"],
            "text": _driver_text(
                d, names_spaarvloer=low_spread_names_spaarvloer,
                daily_min=daily_min_savings_eur,
            ),
        })
    return out


def _negative_days(
    day_contexts: list[dict],
    *,
    daily_min_savings_eur: float,
    low_spread_names_spaarvloer: bool,
) -> list[dict]:
    out: list[dict] = []
    for ctx in day_contexts:
        finance = ctx.get("finance") or {}
        saved = finance.get("saved_eur")
        if saved is None or saved >= -1e-9:
            continue
        drivers = explain_negative_day(
            ctx, daily_min_savings_eur=daily_min_savings_eur,
            low_spread_names_spaarvloer=low_spread_names_spaarvloer,
        )
        out.append({
            "date": finance.get("day"),
            "saved_eur": round(float(saved), 2),
            "drivers": drivers,
        })
    out.sort(key=lambda d: d["saved_eur"])
    return out


def _dutch_weekday(day_iso: str) -> str | None:
    try:
        wd = date_cls.fromisoformat(day_iso).weekday()
    except ValueError:
        return None
    return _DUTCH_WEEKDAYS[wd]


def _loss_headline_snippet(negative_days: list[dict]) -> str | None:
    if not negative_days:
        return None
    worst = negative_days[0]
    day = worst.get("date")
    weekday = _dutch_weekday(str(day)) if day else None
    drivers = worst.get("drivers") or []
    if not weekday:
        return None
    if not drivers:
        return f"Verlies op {weekday}: oorzaak niet te bepalen."
    bits = []
    for d in drivers[:2]:
        key = d.get("key", "")
        if key == "grid_charge_cost":
            bits.append("netladen")
        elif key == "ev_import_during_hold":
            bits.append("EV tijdens vasthouden")
        elif key == "expensive_peak_import":
            bits.append("dure piekimport")
        elif key == "low_spread":
            bits.append("krappe spread")
        else:
            bits.append("meerdere factoren")
    tail = " en ".join(bits[:2]) if len(bits) > 1 else bits[0]
    return f"Verlies op {weekday}: vooral {tail}."


def _headline(
    *, saved_eur: float | None, self_sufficiency_pct: float | None, solar_kwh: float,
    tweak_is_null: bool, negative_days: list[dict] | None = None,
) -> str:
    """One warm, advisor-voice sentence synthesising the week — the 10-second answer to "did we do
    well?" (roadmap P2 bar). Never invents a number: a week with no priced days says so plainly
    instead of showing a false €0.00."""
    loss_note = _loss_headline_snippet(negative_days or [])
    if saved_eur is None:
        lead = "No priced days yet this week, so savings can't be measured"
    elif saved_eur < 0:
        lead = f"You lost €{abs(saved_eur):.2f} this week"
        if loss_note:
            lead += f" — {loss_note}"
    else:
        lead = f"You saved €{saved_eur:.2f} this week"
        if loss_note and negative_days:
            lead += f" ({loss_note})"
    bits = []
    if self_sufficiency_pct is not None:
        bits.append(f"ran {self_sufficiency_pct:.0f}% self-sufficient")
    if solar_kwh > 0.05:
        kwh = f"{solar_kwh:.1f}".rstrip("0").rstrip(".")  # 153.0 → 153, 24.5 stays
        bits.append(f"the panels made {kwh} kWh")
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
    day_contexts: list[dict] | None = None,
    daily_min_savings_eur: float = 0.20,
    low_spread_names_spaarvloer: bool = False,
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
    tweak, tweak_is_actionable = _tweak(advice, week_label, export_price_model)
    # A held/explain line (unstable advisor) is present but NOT actionable — the headline stays
    # calm rather than promising "one tweak worth a look" for a hold.
    tweak_is_null = not tweak_is_actionable
    self_sufficiency_pct = flows.get("self_sufficiency_pct")
    solar_kwh = round(float(flows.get("solar_kwh") or 0.0), 2)
    contexts = day_contexts if day_contexts is not None else [
        {"finance": r, "audit": [], "energy": None} for r in finance_rows
    ]
    negative_days = _negative_days(
        contexts, daily_min_savings_eur=daily_min_savings_eur,
        low_spread_names_spaarvloer=low_spread_names_spaarvloer,
    )

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
            tweak_is_null=tweak_is_null, negative_days=negative_days,
        ),
        "negative_days": negative_days,
        "days_measured": days_measured,
        "days_total": days_total,
    }
