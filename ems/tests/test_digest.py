"""The weekly digest (BACKLOG B-58 / roadmap P2 "the Sunday read"): build_digest is pure — canned
finance rows / flows / scores / audit rows / advisor advice in, the digest dict out. See
ems/digest.py for the exact audit-string patterns these tests exercise."""
from ems.digest import build_digest

WEEK_LABEL = "Week of 2026-07-06"


def _fin(day: str, saved: float | None, *, has_data: bool = True) -> dict:
    return {"day": day, "has_data": has_data, "saved_eur": saved}


FULL_WEEK = [
    _fin("2026-07-06", 1.20),
    _fin("2026-07-07", 0.80),
    _fin("2026-07-08", 2.50),  # best day
    _fin("2026-07-09", -0.10),
    _fin("2026-07-10", 0.40),
    _fin("2026-07-11", 1.00),
    _fin("2026-07-12", 0.90),
]

FLOWS = {"self_sufficiency_pct": 78.4, "solar_kwh": 24.5}

SCORES = [
    {"key": "self_consumption", "label": "Self-consumption", "value": 80, "raw": 80, "unit": "%",
     "explanation": "Kept 80% of your solar on-site."},
    {"key": "co2", "label": "CO₂", "value": 62, "raw": 12.0, "unit": "kg",
     "explanation": "Avoided 62% of a no-solar home's CO₂ (12 kg vs 32 kg)."},
    {"key": "best_price", "label": "Best price", "value": 75, "raw": 0.13, "unit": "€/kWh",
     "explanation": "Imported at €0.13/kWh vs the period's €0.08–€0.30 range."},
]


def _row(category: str, summary: str, detail: dict | None = None) -> dict:
    return {"category": category, "summary": summary, "detail": detail or {}}


# --- saved_eur / best_day -----------------------------------------------------------------------

def test_saved_eur_sums_available_days_none_safe():
    rows = [_fin("2026-07-06", 1.0), _fin("2026-07-07", None), _fin("2026-07-08", 2.5)]
    d = build_digest(finance_rows=rows, flows=FLOWS, scores=SCORES, audit_rows=[], advice=None,
                     week_label=WEEK_LABEL)
    assert d["saved_eur"] == 3.5


def test_saved_eur_none_when_no_day_has_a_figure():
    rows = [_fin("2026-07-06", None), _fin("2026-07-07", None)]
    d = build_digest(finance_rows=rows, flows=FLOWS, scores=SCORES, audit_rows=[], advice=None,
                     week_label=WEEK_LABEL)
    assert d["saved_eur"] is None


def test_best_day_is_the_max_saved_day():
    d = build_digest(finance_rows=FULL_WEEK, flows=FLOWS, scores=SCORES, audit_rows=[],
                     advice=None, week_label=WEEK_LABEL)
    assert d["best_day"] == {"date": "2026-07-08", "saved_eur": 2.5}


def test_best_day_none_when_no_priced_days():
    rows = [_fin("2026-07-06", None, has_data=False)]
    d = build_digest(finance_rows=rows, flows=FLOWS, scores=SCORES, audit_rows=[], advice=None,
                     week_label=WEEK_LABEL)
    assert d["best_day"] is None


# --- partial-week honesty -------------------------------------------------------------------------

def test_partial_week_reports_measured_vs_total():
    rows = FULL_WEEK[:5]  # only 5 of 7 days recorded at all
    d = build_digest(finance_rows=rows, flows=FLOWS, scores=SCORES, audit_rows=[], advice=None,
                     week_label=WEEK_LABEL)
    assert d["days_measured"] == 5
    assert d["days_total"] == 5  # the caller only hands in the days it has — 5 of a 7-day week


def test_full_week_all_seven_measured():
    d = build_digest(finance_rows=FULL_WEEK, flows=FLOWS, scores=SCORES, audit_rows=[],
                     advice=None, week_label=WEEK_LABEL)
    assert d["days_measured"] == 7
    assert d["days_total"] == 7


# --- co2 note --------------------------------------------------------------------------------

def test_co2_note_is_the_co2_scores_own_explanation():
    d = build_digest(finance_rows=FULL_WEEK, flows=FLOWS, scores=SCORES, audit_rows=[],
                     advice=None, week_label=WEEK_LABEL)
    assert d["co2_avoided_note"] == "Avoided 62% of a no-solar home's CO₂ (12 kg vs 32 kg)."


def test_co2_note_none_when_missing():
    d = build_digest(finance_rows=FULL_WEEK, flows=FLOWS, scores=[], audit_rows=[], advice=None,
                     week_label=WEEK_LABEL)
    assert d["co2_avoided_note"] is None


# --- actions: counted from real audit-log string patterns -------------------------------------

def test_counts_confirmed_live_mode_switches():
    rows = [
        _row("battery_decision", "Battery mode idle → discharge_for_load — command sent",
             {"reason": "discharge: €0.35/kWh > break-even €0.20"}),
        _row("battery_decision", "Battery mode discharge_for_load → auto — command sent",
             {"reason": "self-consumption (€0.10/kWh)"}),
    ]
    d = build_digest(finance_rows=FULL_WEEK, flows=FLOWS, scores=SCORES, audit_rows=rows,
                     advice=None, week_label=WEEK_LABEL)
    assert d["actions"] == {"mode_switches": 2, "negative_soaks": 0, "overrides": 0}


def test_counts_dry_run_advisory_decisions_as_switches():
    rows = [
        _row("battery_decision", "Would set battery → grid_charge_to_target — "
             "charge: €0.05/kWh <= break-even €0.20",
             {"decided_only": True}),
    ]
    d = build_digest(finance_rows=FULL_WEEK, flows=FLOWS, scores=SCORES, audit_rows=rows,
                     advice=None, week_label=WEEK_LABEL)
    assert d["actions"]["mode_switches"] == 1


def test_does_not_count_failed_unconfirmed_held_or_drift_rows_as_switches():
    rows = [
        _row("battery_decision", "Battery mode auto → discharge_for_load — "
             "command FAILED (timeout)"),
        _row("battery_decision", "Battery discharge_for_load unconfirmed — device slow to "
             "respond; holding and retrying (not reverting)"),
        _row("battery_decision", "Battery NOT switched to discharge_for_load — daily cap reached"),
        _row("battery_decision", "Battery cluster MISMATCH — 1 tower(s) NOT following the "
             "commanded auto: charging"),
        _row("battery_decision", "Battery cluster back in sync — all towers match the "
             "commanded mode"),
    ]
    d = build_digest(finance_rows=FULL_WEEK, flows=FLOWS, scores=SCORES, audit_rows=rows,
                     advice=None, week_label=WEEK_LABEL)
    assert d["actions"] == {"mode_switches": 0, "negative_soaks": 0, "overrides": 0}


def test_counts_negative_price_soak_from_detail_reason_on_a_live_confirmed_switch():
    # A live control-tick summary never repeats the plan reason — it's in detail.reason only.
    rows = [
        _row("battery_decision",
             "Battery mode auto → grid_charge_to_target — command sent",
             {"reason": "charge: price €-0.02/kWh below €0 — you are paid to charge"}),
    ]
    d = build_digest(finance_rows=FULL_WEEK, flows=FLOWS, scores=SCORES, audit_rows=rows,
                     advice=None, week_label=WEEK_LABEL)
    assert d["actions"] == {"mode_switches": 1, "negative_soaks": 1, "overrides": 0}


def test_counts_negative_price_soak_from_dry_run_summary_text():
    rows = [
        _row("battery_decision",
             "Would set battery → grid_charge_to_target — charge: price €-0.01/kWh below €0 — "
             "you are paid to charge"),
    ]
    d = build_digest(finance_rows=FULL_WEEK, flows=FLOWS, scores=SCORES, audit_rows=rows,
                     advice=None, week_label=WEEK_LABEL)
    assert d["actions"]["negative_soaks"] == 1


def test_counts_manual_overrides_set_but_not_clears():
    rows = [
        _row("manual_override", "Manual override: grid_charge_to_target for 60 min"),
        _row("manual_override", "Manual override cleared — back to the automatic plan"),
        _row("manual_override", "Manual override: hold_reserve for 30 min"),
    ]
    d = build_digest(finance_rows=FULL_WEEK, flows=FLOWS, scores=SCORES, audit_rows=rows,
                     advice=None, week_label=WEEK_LABEL)
    assert d["actions"] == {"mode_switches": 0, "negative_soaks": 0, "overrides": 2}


def test_ignores_unrelated_audit_categories():
    rows = [
        _row("config_change", "Changed 1 setting(s): planner.solar_confidence"),
        _row("car_soc_anchor", "Car SoC anchored at 62%"),
        _row("ai_validation", "AI second opinion: looks fine"),
        _row("shutdown_restore", "Graceful shutdown — restored battery to auto (confirmed)"),
    ]
    d = build_digest(finance_rows=FULL_WEEK, flows=FLOWS, scores=SCORES, audit_rows=rows,
                     advice=None, week_label=WEEK_LABEL)
    assert d["actions"] == {"mode_switches": 0, "negative_soaks": 0, "overrides": 0}


# --- tweak precedence ----------------------------------------------------------------------------

def test_tweak_prefers_the_advisor_suggestion_when_delta_is_at_least_5pp():
    advice = {"recommended_pct": 75.0, "current_pct": 65.0, "delta_pct": 10.0, "n_slots": 60,
              "median_ratio_pct": 80.0, "p25_ratio_pct": 75.0}
    d = build_digest(finance_rows=FULL_WEEK, flows=FLOWS, scores=SCORES, audit_rows=[],
                     advice=advice, week_label=WEEK_LABEL)
    assert d["tweak"].startswith("Apply the advisor suggestion")
    assert "75%" in d["tweak"] and "65%" in d["tweak"]


def test_tweak_advisor_gate_is_on_the_absolute_delta_negative_direction_too():
    advice = {"recommended_pct": 60.0, "current_pct": 80.0, "delta_pct": -20.0, "n_slots": 60,
              "median_ratio_pct": 55.0, "p25_ratio_pct": 60.0}
    d = build_digest(finance_rows=FULL_WEEK, flows=FLOWS, scores=SCORES, audit_rows=[],
                     advice=advice, week_label=WEEK_LABEL)
    assert d["tweak"].startswith("Apply the advisor suggestion")


def test_tweak_ignores_advisor_when_delta_is_below_5pp():
    advice = {"recommended_pct": 82.0, "current_pct": 80.0, "delta_pct": 2.0, "n_slots": 60,
              "median_ratio_pct": 80.0, "p25_ratio_pct": 82.0}
    d = build_digest(finance_rows=FULL_WEEK, flows=FLOWS, scores=SCORES, audit_rows=[],
                     advice=advice, week_label=WEEK_LABEL, export_price_model="spot_minus_tax")
    assert d["tweak"] == "No tweak this week — settings look right."


def test_tweak_falls_back_to_export_model_reminder_near_the_2027_boundary():
    d = build_digest(finance_rows=FULL_WEEK, flows=FLOWS, scores=SCORES, audit_rows=[],
                     advice=None, week_label="Week of 2026-10-05",
                     export_price_model="net_metering")
    assert d["tweak"].startswith("Review your export model before 2027")


def test_tweak_export_model_reminder_does_not_fire_before_october_2026():
    d = build_digest(finance_rows=FULL_WEEK, flows=FLOWS, scores=SCORES, audit_rows=[],
                     advice=None, week_label="Week of 2026-09-28",
                     export_price_model="net_metering")
    assert d["tweak"] == "No tweak this week — settings look right."


def test_tweak_export_model_reminder_does_not_fire_off_net_metering():
    d = build_digest(finance_rows=FULL_WEEK, flows=FLOWS, scores=SCORES, audit_rows=[],
                     advice=None, week_label="Week of 2026-11-01",
                     export_price_model="spot_minus_tax")
    assert d["tweak"] == "No tweak this week — settings look right."


def test_tweak_null_case_is_the_exact_reassuring_sentence():
    d = build_digest(finance_rows=FULL_WEEK, flows=FLOWS, scores=SCORES, audit_rows=[],
                     advice=None, week_label=WEEK_LABEL)
    assert d["tweak"] == "No tweak this week — settings look right."


def test_tweak_survives_an_unparseable_week_label():
    d = build_digest(finance_rows=FULL_WEEK, flows=FLOWS, scores=SCORES, audit_rows=[],
                     advice=None, week_label="This week", export_price_model="net_metering")
    assert d["tweak"] == "No tweak this week — settings look right."


# --- headline --------------------------------------------------------------------------------

def test_headline_synthesizes_the_week_when_settings_look_right():
    d = build_digest(finance_rows=FULL_WEEK, flows=FLOWS, scores=SCORES, audit_rows=[],
                     advice=None, week_label=WEEK_LABEL)
    assert d["headline"] == (
        "You saved €6.70 this week, ran 78% self-sufficient and the panels made 24.5 kWh. "
        "Steady week — settings look right."
    )


def test_headline_points_at_the_tweak_when_one_exists():
    advice = {"recommended_pct": 75.0, "current_pct": 65.0, "delta_pct": 10.0, "n_slots": 60,
              "median_ratio_pct": 80.0, "p25_ratio_pct": 75.0}
    d = build_digest(finance_rows=FULL_WEEK, flows=FLOWS, scores=SCORES, audit_rows=[],
                     advice=advice, week_label=WEEK_LABEL)
    assert d["headline"].endswith("One tweak worth a look below.")


def test_headline_is_honest_with_no_priced_days():
    rows = [_fin("2026-07-06", None, has_data=False)]
    flows = {"self_sufficiency_pct": None, "solar_kwh": 0.0}
    d = build_digest(finance_rows=rows, flows=flows, scores=SCORES, audit_rows=[], advice=None,
                     week_label=WEEK_LABEL)
    assert d["headline"].startswith("No priced days yet this week, so savings can't be measured.")


# --- full-shape sanity ----------------------------------------------------------------------------

def test_build_digest_full_shape():
    audit_rows = [_row("manual_override", "Manual override: hold_reserve for 30 min")]
    d = build_digest(finance_rows=FULL_WEEK, flows=FLOWS, scores=SCORES, audit_rows=audit_rows,
                     advice=None, week_label=WEEK_LABEL)
    assert d == {
        "week_label": "Week of 2026-07-06",
        "saved_eur": 6.70,
        "best_day": {"date": "2026-07-08", "saved_eur": 2.5},
        "self_sufficiency_pct": 78.4,
        "solar_kwh": 24.5,
        "co2_avoided_note": "Avoided 62% of a no-solar home's CO₂ (12 kg vs 32 kg).",
        "actions": {"mode_switches": 0, "negative_soaks": 0, "overrides": 1},
        "tweak": "No tweak this week — settings look right.",
        "headline": "You saved €6.70 this week, ran 78% self-sufficient and the panels made "
                    "24.5 kWh. Steady week — settings look right.",
        "days_measured": 7,
        "days_total": 7,
    }
