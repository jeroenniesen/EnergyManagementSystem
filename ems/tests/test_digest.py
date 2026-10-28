"""The weekly digest (BACKLOG B-58 / roadmap P2 "the Sunday read"): build_digest is pure — canned
finance rows / flows / scores / audit rows / advisor advice in, the digest dict out. See
ems/digest.py for the exact audit-string patterns these tests exercise."""
from datetime import UTC, datetime, timedelta

from ems.digest import build_digest, explain_negative_day, latest_solar_confidence_apply

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
    assert d["tweak"] is None  # null case: calm = absence (headline tail says it)


def test_tweak_nudges_when_advisor_is_stable():
    # A settled recommendation (stable True) with a >=5pp delta surfaces the actionable nudge.
    advice = {"recommended_pct": 75.0, "current_pct": 65.0, "delta_pct": 10.0, "n_slots": 60,
              "median_ratio_pct": 80.0, "p25_ratio_pct": 75.0, "stable": True,
              "spread_pp": 0.0, "window_days": 7, "recent_recommendations": [75.0] * 7}
    d = build_digest(finance_rows=FULL_WEEK, flows=FLOWS, scores=SCORES, audit_rows=[],
                     advice=advice, week_label=WEEK_LABEL)
    assert d["tweak"].startswith("Apply the advisor suggestion")
    assert d["headline"].endswith("One tweak worth a look below.")


def test_tweak_holds_and_explains_when_advisor_is_unstable():
    # Same >=5pp delta, but the recommendation flips day-to-day (stable False) — do NOT nudge; show
    # the hold/explain line instead, and keep the headline calm (no "one tweak worth a look").
    advice = {"recommended_pct": 75.0, "current_pct": 65.0, "delta_pct": 10.0, "n_slots": 60,
              "median_ratio_pct": 80.0, "p25_ratio_pct": 75.0, "stable": False,
              "spread_pp": 10.0, "window_days": 7,
              "recent_recommendations": [70.0, 80.0, 70.0, 80.0, 70.0, 80.0, 75.0]}
    d = build_digest(finance_rows=FULL_WEEK, flows=FLOWS, scores=SCORES, audit_rows=[],
                     advice=advice, week_label=WEEK_LABEL, export_price_model="spot_minus_tax")
    assert d["tweak"] == (
        "Advisor recommendation unstable (spread 10pp over 7 days) — holding until it settles.")
    assert "75%" not in d["tweak"]  # the number the user must not be sent chasing
    assert d["headline"].endswith("Steady week — settings look right.")


def test_tweak_unstable_below_5pp_delta_needs_no_hold_line():
    # Nothing to nudge (delta < 5pp), so instability is moot — falls straight through to the null
    # case, not a spurious "holding until it settles" line.
    advice = {"recommended_pct": 82.0, "current_pct": 80.0, "delta_pct": 2.0, "n_slots": 60,
              "median_ratio_pct": 80.0, "p25_ratio_pct": 82.0, "stable": False,
              "spread_pp": 15.0, "window_days": 7, "recent_recommendations": [70.0, 85.0, 80.0]}
    d = build_digest(finance_rows=FULL_WEEK, flows=FLOWS, scores=SCORES, audit_rows=[],
                     advice=advice, week_label=WEEK_LABEL, export_price_model="spot_minus_tax")
    assert d["tweak"] is None


def test_tweak_falls_back_to_export_model_reminder_near_the_2027_boundary():
    d = build_digest(finance_rows=FULL_WEEK, flows=FLOWS, scores=SCORES, audit_rows=[],
                     advice=None, week_label="Week of 2026-10-05",
                     export_price_model="net_metering")
    assert d["tweak"].startswith("Review your export model before 2027")


def test_tweak_export_model_reminder_does_not_fire_before_october_2026():
    d = build_digest(finance_rows=FULL_WEEK, flows=FLOWS, scores=SCORES, audit_rows=[],
                     advice=None, week_label="Week of 2026-09-28",
                     export_price_model="net_metering")
    assert d["tweak"] is None  # null case: calm = absence (headline tail says it)


def test_tweak_export_model_reminder_does_not_fire_off_net_metering():
    d = build_digest(finance_rows=FULL_WEEK, flows=FLOWS, scores=SCORES, audit_rows=[],
                     advice=None, week_label="Week of 2026-11-01",
                     export_price_model="spot_minus_tax")
    assert d["tweak"] is None  # null case: calm = absence (headline tail says it)


def test_tweak_null_case_is_the_exact_reassuring_sentence():
    d = build_digest(finance_rows=FULL_WEEK, flows=FLOWS, scores=SCORES, audit_rows=[],
                     advice=None, week_label=WEEK_LABEL)
    assert d["tweak"] is None  # null case: calm = absence (headline tail says it)


def test_tweak_survives_an_unparseable_week_label():
    d = build_digest(finance_rows=FULL_WEEK, flows=FLOWS, scores=SCORES, audit_rows=[],
                     advice=None, week_label="This week", export_price_model="net_metering")
    assert d["tweak"] is None  # null case: calm = absence (headline tail says it)


# --- headline --------------------------------------------------------------------------------

def test_headline_synthesizes_the_week_when_settings_look_right():
    d = build_digest(finance_rows=FULL_WEEK, flows=FLOWS, scores=SCORES, audit_rows=[],
                     advice=None, week_label=WEEK_LABEL)
    assert d["headline"] == (
        "You saved €6.70 this week (Verlies op donderdag: oorzaak niet te bepalen.), "
        "ran 78% self-sufficient and the panels made 24.5 kWh. "
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


# --- negative savings days (audit F-49 / #325) ---------------------------------------------------

def test_explain_negative_day_fixture_top3_sorted_and_bounded():
    """AC1: saved −€1.20 with known rows → ≤3 drivers, sum ≤ |loss| + €0.05."""
    day_rows = {
        "finance": {
            "day": "2026-07-07",
            "saved_eur": -1.20,
            "has_data": True,
            "grid_cost_eur": 4.00,
            "grid_import_kwh": 8.0,
            "battery_charge_kwh": 2.5,
            "battery_contribution_eur": -0.70,
            "avoided_expensive_eur": -0.35,
            "solar_self_use_eur": 0.10,
        },
        "energy": {
            "grid_to_battery": 2.0,
            "grid_to_car": 1.0,
            "battery_to_car": 0.0,
        },
        "audit": [
            {"category": "battery_decision", "summary": "Would set battery → hold_reserve",
             "detail": {"reason": "reserve"}},
            {"category": "battery_decision", "summary": "Plan note",
             "detail": {"reason": (
                 "Geen handel: geprojecteerde besparing €0.10 < daily_min €0.20 — "
                 "hele dag no-trade."
             )}},
        ],
    }
    drivers = explain_negative_day(day_rows, daily_min_savings_eur=0.20)
    assert len(drivers) == 3
    assert drivers == sorted(drivers, key=lambda d: abs(d["eur"]), reverse=True)
    assert sum(d["eur"] for d in drivers) <= 1.20 + 0.05
    assert [d["key"] for d in drivers] == [
        "grid_charge_cost", "ev_import_during_hold", "expensive_peak_import",
    ]
    # Prose cites the capped euro (NL comma), not the pre-scale component and not "ca,".
    assert drivers[0]["eur"] == 0.54
    assert drivers[0]["text"] == (
        "Netladen van de batterij (2,0 kWh van het net) kostte ca. €0,54."
    )
    assert drivers[1]["text"] == (
        "EV laadde (1,0 kWh) terwijl de batterij vastgehouden werd — ca. €0,39 netimport."
    )
    assert drivers[2]["text"] == (
        "Import tijdens dure uren kostte ca. €0,27 extra (ten opzichte van zonder batterij)."
    )
    assert "low_spread" not in {d["key"] for d in drivers}
    for d in drivers:
        cents = f"{d['eur']:.2f}".replace(".", ",")
        assert f"€{cents}" in d["text"]
        assert d["text"].endswith(".")
        assert "ca," not in d["text"]


def test_explain_negative_day_missing_data_returns_empty_drivers():
    day_rows = {"finance": {"day": "2026-07-07", "saved_eur": -2.0, "has_data": True}}
    assert explain_negative_day(day_rows) == []


def test_saved_zero_and_positive_days_are_not_negative_days():
    rows = [_fin("2026-07-06", 0.0), _fin("2026-07-07", 0.40), _fin("2026-07-08", -0.20)]
    d = build_digest(finance_rows=rows, flows=FLOWS, scores=SCORES, audit_rows=[],
                     advice=None, week_label=WEEK_LABEL)
    assert [item["date"] for item in d["negative_days"]] == ["2026-07-08"]
    assert d["negative_days"][0]["drivers"] == []
    assert "Oorzaak niet te bepalen" in d["headline"] or "oorzaak niet te bepalen" in d["headline"]


def test_negative_days_omit_non_loss_days_and_appear_in_digest():
    rows = FULL_WEEK[:]
    d = build_digest(finance_rows=rows, flows=FLOWS, scores=SCORES, audit_rows=[],
                     advice=None, week_label=WEEK_LABEL)
    assert all(item["saved_eur"] < 0 for item in d["negative_days"])
    assert {x["date"] for x in d["negative_days"]} == {"2026-07-09"}
    assert "negative_days" in d


def test_low_spread_copy_is_generic_until_spaarvloer_setting_ships():
    drivers = explain_negative_day({
        "finance": {"day": "2026-07-01", "saved_eur": -0.50, "has_data": True},
        "audit": [{"category": "plan", "summary": "",
                   "detail": {"reason": (
                       "Geen handel: geprojecteerde besparing €0.05 < daily_min €0.20 — "
                       "hele dag no-trade."
                   )}}],
    }, daily_min_savings_eur=0.20, low_spread_names_spaarvloer=False)
    low = next(d for d in drivers if d["key"] == "low_spread")
    assert low["eur"] == 0.15
    assert low["text"] == (
        "Krappe prijs-spread: weinig te verdienen met laden/ontladen kostte ca. €0,15."
    )
    assert "spaarvloer" not in low["text"].lower()


def test_low_spread_names_spaarvloer_when_enabled():
    drivers = explain_negative_day({
        "finance": {"day": "2026-07-01", "saved_eur": -0.50, "has_data": True},
        "audit": [{"category": "plan", "summary": "",
                   "detail": {"reason": (
                       "Geen handel: geprojecteerde besparing €0,05 < daily_min €0,20 — "
                       "hele dag no-trade."
                   )}}],
    }, daily_min_savings_eur=0.20, low_spread_names_spaarvloer=True)
    low = next(d for d in drivers if d["key"] == "low_spread")
    assert "spaarvloer" in low["text"].lower()
    assert "€0,20" in low["text"]
    assert "€0.20" not in low["text"]


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
        "tweak": None,
        "headline": (
            "You saved €6.70 this week (Verlies op donderdag: oorzaak niet te bepalen.), "
            "ran 78% self-sufficient and the panels made 24.5 kWh. "
            "Steady week — settings look right."
        ),
        "days_measured": 7,
        "days_total": 7,
        "negative_days": [{"date": "2026-07-09", "saved_eur": -0.1, "drivers": []}],
    }


def _advice(recommended: float, current: float, *, stable: bool = True) -> dict:
    return {
        "recommended_pct": recommended,
        "current_pct": current,
        "delta_pct": recommended - current,
        "n_slots": 60,
        "stable": stable,
    }


def _digest_with(advice: dict, **kw):
    return build_digest(
        finance_rows=FULL_WEEK, flows=FLOWS, scores=SCORES, audit_rows=[],
        advice=advice, week_label=WEEK_LABEL, export_price_model="spot_minus_tax", **kw,
    )


# --- #337 / F-36: one-click solar apply + hysteresis -------------------------------------------

def test_solar_tweak_apply_payload_is_exactly_planner_solar_confidence():
    d = _digest_with(_advice(75.0, 65.0))
    assert d["tweak_apply"] == {
        "key": "planner.solar_confidence",
        "value": 75.0,
        "current": 65.0,
    }
    assert "control.dry_run" not in d["tweak_apply"]
    assert "control.operational" not in d["tweak_apply"]


def test_delta_below_5pp_offers_no_apply_even_when_advice_oscillates():
    for recommended in (82.0, 78.0, 84.0, 76.0, 80.0):
        d = _digest_with(_advice(recommended, 80.0))
        assert "tweak_apply" not in d
        assert d["tweak"] is None


def test_unstable_oscillation_has_no_apply_button():
    d = _digest_with(_advice(75.0, 65.0, stable=False))
    assert "tweak_apply" not in d
    assert d["tweak"].startswith("Advisor recommendation unstable")


def test_hysteresis_suppresses_oscillating_advice_for_seven_days_after_apply():
    applied = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
    now = applied + timedelta(days=6, hours=23)
    swings = ((90.0, 70.0), (50.0, 70.0), (80.0, 50.0), (60.0, 85.0), (75.0, 70.0))
    for recommended, current in swings:
        d = _digest_with(
            _advice(recommended, current),
            now=now, solar_confidence_applied_at=applied,
        )
        assert "tweak_apply" not in d
        assert d["tweak"] is None or "solar confidence" not in d["tweak"].lower()


def test_hysteresis_lifts_at_exactly_seven_days():
    applied = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
    now = applied + timedelta(days=7)
    d = _digest_with(
        _advice(70.0, 80.0), now=now, solar_confidence_applied_at=applied,
    )
    assert d["tweak_apply"]["key"] == "planner.solar_confidence"
    assert d["tweak_apply"]["value"] == 70.0


def test_hysteresis_does_not_hide_the_export_model_reminder():
    applied = datetime(2026, 10, 10, 8, 0, tzinfo=UTC)
    now = applied + timedelta(days=1)
    d = build_digest(
        finance_rows=FULL_WEEK, flows=FLOWS, scores=SCORES, audit_rows=[],
        advice=_advice(70.0, 80.0), week_label="Week of 2026-10-12",
        export_price_model="net_metering", now=now, solar_confidence_applied_at=applied,
    )
    assert d["tweak"].startswith("Review your export model before 2027")
    assert "tweak_apply" not in d


def test_latest_solar_confidence_apply_uses_the_newest_config_change():
    older = datetime(2026, 10, 1, 8, 0, tzinfo=UTC)
    newer = datetime(2026, 10, 3, 8, 0, tzinfo=UTC)
    rows = [
        {"ts": older.isoformat(), "category": "config_change",
         "summary": "Changed 1 setting(s): planner.solar_confidence",
         "detail": {"keys": ["planner.solar_confidence"]}},
        {"ts": newer.isoformat(), "category": "battery_decision",
         "summary": "planner.solar_confidence mentioned in a decision", "detail": {}},
        {"ts": (newer + timedelta(hours=1)).isoformat(), "category": "config_change",
         "summary": "Changed 1 setting(s): ui.theme", "detail": {"keys": ["ui.theme"]}},
        {"ts": newer.isoformat(), "category": "config_change",
         "summary": "Changed 1 setting(s): planner.solar_confidence",
         "detail": {"keys": ["planner.solar_confidence"]}},
    ]
    assert latest_solar_confidence_apply(rows) == newer
