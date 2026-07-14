"""Export-package assembly (pure): CSV serialisation, ZIP packing, manifest."""
import json

from ems.export_package import (
    AUDIT_COLUMNS,
    EV_SESSION_COLUMNS,
    RAW_COLUMNS,
    build_manifest,
    build_zip,
    ev_price_adherence,
    incident_rollup,
    read_member,
    rows_to_csv,
    validation_summary,
    zip_names,
)


def test_rows_to_csv_has_header_and_ignores_unknown_keys():
    rows = [{"ts": "2026-06-28T10:00:00+00:00", "grid_power_w": 200.0, "solar_power_w": 0.0,
             "battery_power_w": -100.0, "ev_power_w": 0.0, "soc_pct": 55.0, "extra": "drop me"}]
    out = rows_to_csv(rows, RAW_COLUMNS)
    lines = out.strip().splitlines()
    assert lines[0] == ",".join(RAW_COLUMNS)  # stable header
    assert "drop me" not in out               # unknown column ignored
    assert "200.0" in lines[1]


def test_rows_to_csv_json_encodes_dict_cells():
    # An audit detail is a dict — it must be JSON-encoded so the CSV stays one cell.
    rows = [{"id": 1, "ts": "t", "category": "battery_decision", "summary": "set auto",
             "detail": {"intent": "allow_self_consumption", "confirmed": True}}]
    out = rows_to_csv(rows, AUDIT_COLUMNS)
    assert '"intent"' in out and "allow_self_consumption" in out
    assert out.strip().count("\n") == 1  # header + exactly one data row (no embedded newline break)


def test_rows_to_csv_empty_still_writes_header():
    assert rows_to_csv([], RAW_COLUMNS).strip() == ",".join(RAW_COLUMNS)


def test_ev_session_columns_csv_has_header_and_a_row():
    sessions = [{"start": "2026-06-28T09:00:00+00:00", "end": "2026-06-28T09:10:00+00:00",
                 "kwh": 1.75, "avg_kw": 7.0, "peak_kw": 7.0, "samples": 3}]
    out = rows_to_csv(sessions, EV_SESSION_COLUMNS)
    lines = out.strip().splitlines()
    assert lines[0] == ",".join(EV_SESSION_COLUMNS)
    assert "1.75" in lines[1] and "7.0" in lines[1]


def test_ev_session_columns_csv_empty_still_writes_header():
    assert rows_to_csv([], EV_SESSION_COLUMNS).strip() == ",".join(EV_SESSION_COLUMNS)


# ---- ev_price_adherence: volume-weighted price paid for EV charging vs. window average ----

def test_ev_price_adherence_no_sessions_is_none():
    assert ev_price_adherence([], [{"start_ts": "2026-06-28T10:00:00+00:00",
                                     "eur_per_kwh": 0.20}]) is None


def test_ev_price_adherence_weighted_price_hand_computed():
    # Two 5 kWh sessions, each entirely inside its own 15-min slot: one at 0.10 EUR/kWh, one at
    # 0.30 EUR/kWh -> volume-weighted price paid = (5*0.10 + 5*0.30) / 10 = 0.20. The window's four
    # priced slots (0.10/0.20/0.30/0.40) average to 0.25 -> charging ran BELOW the window average.
    sessions = [
        {"start": "2026-06-28T10:00:00+00:00", "end": "2026-06-28T10:14:00+00:00",
         "kwh": 5.0, "avg_kw": 21.4, "peak_kw": 22.0, "samples": 3},
        {"start": "2026-06-28T11:00:00+00:00", "end": "2026-06-28T11:14:00+00:00",
         "kwh": 5.0, "avg_kw": 21.4, "peak_kw": 22.0, "samples": 3},
    ]
    prices = [
        {"start_ts": "2026-06-28T10:00:00+00:00", "eur_per_kwh": 0.10},
        {"start_ts": "2026-06-28T10:15:00+00:00", "eur_per_kwh": 0.20},
        {"start_ts": "2026-06-28T11:00:00+00:00", "eur_per_kwh": 0.30},
        {"start_ts": "2026-06-28T11:15:00+00:00", "eur_per_kwh": 0.40},
    ]
    out = ev_price_adherence(sessions, prices)
    assert out["n_sessions"] == 2
    assert out["total_kwh"] == 10.0
    assert out["priced_kwh"] == 10.0
    assert out["unpriced_kwh"] == 0.0
    assert out["weighted_price_eur_per_kwh"] == 0.20
    assert out["window_avg_price_eur_per_kwh"] == 0.25


def test_ev_price_adherence_excludes_unpriced_portions_from_weighting():
    # A session with no matching price_slots row: its kWh is tallied as unpriced and excluded from
    # both the weighted price and the window average (there are no priced slots at all here).
    sessions = [{"start": "2026-06-28T10:00:00+00:00", "end": "2026-06-28T10:14:00+00:00",
                 "kwh": 4.0, "avg_kw": 16.0, "peak_kw": 16.0, "samples": 3}]
    out = ev_price_adherence(sessions, [])
    assert out["n_sessions"] == 1
    assert out["total_kwh"] == 4.0
    assert out["priced_kwh"] == 0.0
    assert out["unpriced_kwh"] == 4.0
    assert out["weighted_price_eur_per_kwh"] is None
    assert out["window_avg_price_eur_per_kwh"] is None


def test_build_zip_roundtrips_members_sorted_and_deterministic():
    members = {"b.csv": "hello", "a.csv": "world"}
    data = build_zip(members)
    assert zip_names(data) == ["a.csv", "b.csv"]           # sorted
    assert read_member(data, "a.csv") == "world"
    assert build_zip(members) == data                       # deterministic bytes


def test_build_manifest_carries_window_counts_and_extra():
    m = json.loads(build_manifest(
        generated_at="2026-06-28T12:00:00+00:00", app_version="0.0.1",
        window_start="2026-05-28T00:00:00+00:00", window_end="2026-06-28T12:00:00+00:00",
        counts={"raw_samples": 100, "prices": 96},
        extra={"diagnostics": {"ready": True}},
    ))
    assert m["kind"] == "ems-export-package" and m["schema_version"] == 1
    assert m["app_version"] == "0.0.1"
    assert m["counts"]["raw_samples"] == 100
    assert m["window"]["start"].startswith("2026-05-28")
    assert m["diagnostics"] == {"ready": True}  # extra merged in


# ---- incident_rollup: control-health incidents rolled up from the audit log ----

def test_incident_rollup_classifies_counts_and_dates():
    rows = [
        {"id": 1, "ts": "2026-06-20T10:00:00+00:00", "category": "battery_decision",
         "summary": "Battery cluster MISMATCH — 1 tower(s) NOT following the commanded mode",
         "detail": {}},
        {"id": 2, "ts": "2026-06-25T09:00:00+00:00", "category": "battery_decision",
         "summary": "Battery charge unconfirmed — device slow to respond; holding and retrying "
                    "(not reverting)",
         "detail": {}},
        {"id": 3, "ts": "2026-06-26T09:00:00+00:00", "category": "battery_decision",
         "summary": "Battery discharge unconfirmed — device slow to respond; holding and "
                    "retrying (not reverting)",
         "detail": {}},
        {"id": 4, "ts": "2026-06-26T12:00:00+00:00", "category": "battery_decision",
         "summary": "Would set battery to charge — cheap window", "detail": {}},  # benign, ignored
    ]
    out = incident_rollup(rows)
    assert out["total"] == 3
    assert out["by_type"] == {"cluster_mismatch": 1, "command_failed": 2}
    assert out["by_day"] == {"2026-06-20": 1, "2026-06-25": 1, "2026-06-26": 1}
    assert out["most_recent"] == "2026-06-26T09:00:00+00:00"
    assert out["last_7_days"] == 3  # newest day minus 6/20 = 6 days -> all three within 7
    # Same window here (all three incidents land within 7 days of the newest) -> the two
    # by-type breakdowns agree, but they are NOT the same field (see the windowed test below).
    assert out["by_type_last_7_days"] == {"cluster_mismatch": 1, "command_failed": 2}


def test_incident_rollup_empty_input_is_zeros():
    out = incident_rollup([])
    assert out == {
        "total": 0, "by_type": {}, "by_type_last_7_days": {}, "by_day": {},
        "most_recent": None, "last_7_days": 0,
    }


def test_incident_rollup_priority_order_first_match_wins():
    # Contains both "unconfirmed" and "reverted" -> command_failed wins (checked before revert).
    rows = [{"ts": "2026-06-28T10:00:00+00:00", "summary":
             "Battery charge unconfirmed -> reverted to AUTO", "detail": {}}]
    out = incident_rollup(rows)
    assert out["by_type"] == {"command_failed": 1}


def test_incident_rollup_fallback_and_revert_and_detail_text():
    rows = [
        {"ts": "2026-06-01T00:00:00+00:00", "summary": "Held plan",
         "detail": {"reason": "prices unavailable — using failsafe curve"}},
        {"ts": "2026-06-02T00:00:00+00:00", "summary": "Reverted battery mode to AUTO",
         "detail": {}},
    ]
    out = incident_rollup(rows)
    assert out["by_type"] == {"fallback": 1, "revert": 1}
    assert out["total"] == 2


def test_incident_rollup_last_7_days_excludes_older_incidents():
    rows = [
        {"ts": "2026-06-01T00:00:00+00:00", "summary": "Battery cluster mismatch", "detail": {}},
        {"ts": "2026-06-20T00:00:00+00:00", "summary": "Battery cluster mismatch", "detail": {}},
    ]
    out = incident_rollup(rows)
    assert out["total"] == 2
    assert out["last_7_days"] == 1  # only the 6/20 incident is within 7 days of itself


def test_incident_rollup_by_type_last_7_days_is_windowed_not_full():
    # The production trust bug this fixes: a "15 incidents in the last 7 days" headline paired
    # with by-type rows that summed to 28 (the FULL audit window, not the last 7 days). Here: 5
    # cluster_mismatch rows outside the 7-day window (older) + 2 inside it, plus 1
    # command_failed inside it. `by_type` (full window) must sum to `total` (8); `by_type_last_7_
    # days` must sum to `last_7_days` (3) and describe ONLY the recent window.
    rows = [
        {"ts": f"2026-05-{d:02d}T00:00:00+00:00", "summary": "Battery cluster mismatch",
         "detail": {}}
        for d in range(1, 6)  # 5 old incidents, well outside the trailing 7 days
    ] + [
        {"ts": "2026-06-25T00:00:00+00:00", "summary": "Battery cluster mismatch", "detail": {}},
        {"ts": "2026-06-26T09:00:00+00:00", "summary": "Battery charge unconfirmed", "detail": {}},
        {"ts": "2026-06-26T12:00:00+00:00", "summary": "Battery cluster mismatch", "detail": {}},
    ]
    out = incident_rollup(rows)
    assert out["total"] == 8
    assert out["last_7_days"] == 3
    assert out["by_type"] == {"cluster_mismatch": 7, "command_failed": 1}  # full window
    assert sum(out["by_type"].values()) == out["total"]
    assert out["by_type_last_7_days"] == {"cluster_mismatch": 2, "command_failed": 1}  # windowed
    assert sum(out["by_type_last_7_days"].values()) == out["last_7_days"]
    # The headline (last_7_days=3) and the windowed breakdown (sums to 3) now describe the SAME
    # window — unlike `by_type` (sums to 8, the full window), which the headline must NOT be
    # paired against.
    assert sum(out["by_type_last_7_days"].values()) != sum(out["by_type"].values())


def test_validation_summary_includes_incidents_section():
    text = validation_summary(
        generated_at="2026-06-28T12:00:00+00:00", app_version="0.0.1",
        window={"start": "2026-05-28T00:00:00+00:00", "end": "2026-06-28T12:00:00+00:00"},
        counts={"raw_samples": 1}, saved_total_eur=None,
        validation={"incidents": {"total": 2, "last_7_days": 1,
                                   "most_recent": "2026-06-26T09:00:00+00:00",
                                   "by_type": {"cluster_mismatch": 1, "command_failed": 1},
                                   "by_type_last_7_days": {"command_failed": 1},
                                   "by_day": {}}},
    )
    assert "Incidents" in text
    assert "Total:          2 (last 7 days: 1)" in text
    # Both windows appear, distinctly labelled, and do NOT collapse into one line.
    assert "By type (full window):   cluster_mismatch=1, command_failed=1" in text
    assert "By type (last 7 days):   command_failed=1" in text
    assert "cluster_mismatch=1" in text and "command_failed=1" in text
    assert "2026-06-26T09:00:00+00:00" in text


# ---- validation_summary + _forecast_skill_lines: the solar_confidence advisory suggestion ----

_FORECAST_SKILL = {
    "n_slots": 100, "bias_w": -50.0, "mae_w": 120.0, "band_coverage_pct": 88.0,
    "actual_solar_kwh": 12.3, "forecast_p50_kwh": 13.0,
}


def test_validation_summary_adds_suggestion_line_when_advice_given():
    advice = {"recommended_pct": 70.0, "n_slots": 96, "median_ratio_pct": 82.0,
              "p25_ratio_pct": 70.0, "current_pct": 80.0, "delta_pct": -10.0}
    text = validation_summary(
        generated_at="2026-06-28T12:00:00+00:00", app_version="0.0.1",
        window={"start": "2026-05-28T00:00:00+00:00", "end": "2026-06-28T12:00:00+00:00"},
        counts={"raw_samples": 1}, saved_total_eur=None, validation={},
        forecast_skill=_FORECAST_SKILL, solar_confidence_advice=advice,
    )
    assert "Solar forecast skill" in text
    assert "Suggested solar_confidence: 70% (currently 80%)" in text


def test_validation_summary_omits_suggestion_line_when_advice_is_none():
    text = validation_summary(
        generated_at="2026-06-28T12:00:00+00:00", app_version="0.0.1",
        window={"start": "2026-05-28T00:00:00+00:00", "end": "2026-06-28T12:00:00+00:00"},
        counts={"raw_samples": 1}, saved_total_eur=None, validation={},
        forecast_skill=_FORECAST_SKILL,
    )
    assert "Solar forecast skill" in text
    assert "Suggested solar_confidence" not in text


# ---- validation_summary + _prediction_accuracy_lines: plan-execution + load-baseline tracks ----

_PLAN_EXECUTION = {"n_deadlines": 5, "mean_error_pp": -0.3, "mae_pp": 3.7, "hit_rate_pct": 66.7}
_LOAD_BASELINE = {"n_hours": 28, "mape_pct": 2.4, "bias_w": 28.6}


def test_validation_summary_includes_prediction_accuracy_section_when_both_given():
    text = validation_summary(
        generated_at="2026-06-28T12:00:00+00:00", app_version="0.0.1",
        window={"start": "2026-05-28T00:00:00+00:00", "end": "2026-06-28T12:00:00+00:00"},
        counts={"raw_samples": 1}, saved_total_eur=None, validation={},
        plan_execution_error=_PLAN_EXECUTION, load_baseline_error=_LOAD_BASELINE,
    )
    assert "Prediction accuracy" in text
    assert "Plan execution:  5 deadlines, mean error -0.3pp, MAE 3.7pp, hit rate 66.7%" in text
    assert "Load baseline:   28 hours, MAPE 2.4%, bias +28.6 W" in text


def test_validation_summary_prediction_accuracy_shows_only_the_available_line():
    # load_baseline_error not enough evidence yet (None) -> only the plan-execution line shows,
    # but the section itself still appears (plan_execution IS available).
    text = validation_summary(
        generated_at="2026-06-28T12:00:00+00:00", app_version="0.0.1",
        window={"start": "2026-05-28T00:00:00+00:00", "end": "2026-06-28T12:00:00+00:00"},
        counts={"raw_samples": 1}, saved_total_eur=None, validation={},
        plan_execution_error=_PLAN_EXECUTION, load_baseline_error=None,
    )
    assert "Prediction accuracy" in text
    assert "Plan execution:" in text
    assert "Load baseline:" not in text


def test_validation_summary_omits_prediction_accuracy_section_when_neither_given():
    text = validation_summary(
        generated_at="2026-06-28T12:00:00+00:00", app_version="0.0.1",
        window={"start": "2026-05-28T00:00:00+00:00", "end": "2026-06-28T12:00:00+00:00"},
        counts={"raw_samples": 1}, saved_total_eur=None, validation={},
    )
    assert "Prediction accuracy" not in text


# ---- validation_summary + _ev_charging_lines: the "EV charging" section ----

def test_validation_summary_includes_ev_charging_section_below_average():
    adherence = {"n_sessions": 2, "total_kwh": 10.0, "priced_kwh": 10.0, "unpriced_kwh": 0.0,
                 "weighted_price_eur_per_kwh": 0.20, "window_avg_price_eur_per_kwh": 0.25}
    text = validation_summary(
        generated_at="2026-06-28T12:00:00+00:00", app_version="0.0.1",
        window={"start": "2026-05-28T00:00:00+00:00", "end": "2026-06-28T12:00:00+00:00"},
        counts={"raw_samples": 1}, saved_total_eur=None, validation={},
        ev_price_adherence=adherence,
    )
    assert "EV charging" in text
    assert "2 sessions · 10.0 kWh (AC)" in text
    assert "volume-weighted price paid: €0.20/kWh" in text
    assert "window average price:       €0.25/kWh" in text
    assert "charging ran €0.05/kWh below the average" in text
    assert "the schedule advice is being followed" in text


def test_validation_summary_ev_charging_section_above_average_reads_isnt_followed():
    adherence = {"n_sessions": 1, "total_kwh": 5.0, "priced_kwh": 5.0, "unpriced_kwh": 0.0,
                 "weighted_price_eur_per_kwh": 0.35, "window_avg_price_eur_per_kwh": 0.25}
    text = validation_summary(
        generated_at="2026-06-28T12:00:00+00:00", app_version="0.0.1",
        window={"start": "2026-05-28T00:00:00+00:00", "end": "2026-06-28T12:00:00+00:00"},
        counts={"raw_samples": 1}, saved_total_eur=None, validation={},
        ev_price_adherence=adherence,
    )
    assert "charging ran €0.10/kWh above the average" in text
    assert "the schedule advice isn't being followed" in text


def test_validation_summary_ev_charging_section_no_sessions_yet():
    text = validation_summary(
        generated_at="2026-06-28T12:00:00+00:00", app_version="0.0.1",
        window={"start": "2026-05-28T00:00:00+00:00", "end": "2026-06-28T12:00:00+00:00"},
        counts={"raw_samples": 1}, saved_total_eur=None, validation={},
        ev_price_adherence={"n_sessions": 0},
    )
    assert "EV charging" in text
    assert "No charging sessions detected yet." in text


def test_validation_summary_omits_ev_charging_section_when_not_given():
    text = validation_summary(
        generated_at="2026-06-28T12:00:00+00:00", app_version="0.0.1",
        window={"start": "2026-05-28T00:00:00+00:00", "end": "2026-06-28T12:00:00+00:00"},
        counts={"raw_samples": 1}, saved_total_eur=None, validation={},
    )
    assert "EV charging" not in text


# ---- endpoint: GET /api/export/package returns a ZIP of the CSVs + manifest ----
import asyncio  # noqa: E402
from datetime import UTC, datetime, timedelta  # noqa: E402
from zoneinfo import ZoneInfo  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

from ems.domain import RawSample  # noqa: E402
from ems.load_model import reconstruct  # noqa: E402
from ems.sources.mock import MockSource  # noqa: E402
from ems.sources.prices import MockPriceSource  # noqa: E402
from ems.storage.audit import AuditStore  # noqa: E402
from ems.storage.history import HistoryStore  # noqa: E402
from ems.storage.settings import SettingsStore  # noqa: E402
from ems.web.api import _FINANCE_CALC_VERSION, create_app  # noqa: E402

AMS = ZoneInfo("Europe/Amsterdam")


def _seed(db: str) -> None:
    async def go():
        store = HistoryStore(db)
        await store.init()
        raw = RawSample(grid_power_w=1600.0, solar_power_w=3500.0, battery_power_w=-800.0,
                        ev_power_w=4000.0, soc_pct=55.0)
        await store.record("2026-06-28T10:00:00+00:00", raw, reconstruct(raw))
        await store.upsert_price_slots([("2026-06-28T10:00:00+00:00", 0.18)])
        # Canonical prediction-ledger row (design §4.2/§4.3) — the export's forecasts.csv and
        # "Solar forecast skill" section now score `ledger_canonical_between('solar', ...)`.
        await store.ledger_append([
            ("2026-06-28T10:00:00+00:00", "solar", "2026-06-28T10:00:00+00:00",
             1000.0, 2000.0, 3000.0, "test", None, None, 1)])
        # calc_v stamped so the export's backfill (which now runs _ensure_day_finance over every
        # completed day in its window) trusts this as an up-to-date cache hit instead of treating
        # it as stale and recomputing it from the raw sample above (which would overwrite 0.42).
        await store.upsert_daily_finance("2026-06-28", {"day": "2026-06-28", "has_data": True,
                                                         "saved_eur": 0.42, "price_coverage": 1.0,
                                                         "calc_v": _FINANCE_CALC_VERSION})
        await store.record_plan("2026-06-28T10:00:00+00:00", {
            "strategy": "winter", "target_soc": 80.0,
            "deadline": "2026-06-28T18:00:00+00:00", "soc_pct": 55.0,
            "intent": "grid_charge_to_target",
        })
        await store.record_gas("2026-06-28T10:00:00+00:00", 1234.5)
        audit = AuditStore(db)
        await audit.init()
        await audit.append("2026-06-28T10:00:00+00:00", "battery_decision", "set auto",
                           {"intent": "allow_self_consumption", "confirmed": True})
    asyncio.run(go())


def _app(db: str):
    return create_app(
        MockSource(), dry_run=True, dev_mode="mock", tz=AMS, store=HistoryStore(db),
        settings_store=SettingsStore(db), audit_store=AuditStore(db),
        price_source=MockPriceSource(AMS),
    )


def test_export_package_endpoint_returns_zip_with_all_members(tmp_path):
    db = str(tmp_path / "ems.sqlite")
    _seed(db)
    with TestClient(_app(db)) as c:
        r = c.get("/api/export/package?days=400")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    assert "attachment" in r.headers.get("content-disposition", "")
    data = r.content
    names = set(zip_names(data))
    assert {"raw_samples.csv", "derived_samples.csv", "prices.csv", "forecasts.csv",
            "daily_finance.csv", "audit_log.csv", "plan_history.csv", "gas.csv",
            "ev_sessions.csv", "manifest.json"} <= names
    # Real data made it in.
    assert "1600.0" in read_member(data, "raw_samples.csv")
    assert "0.18" in read_member(data, "prices.csv")
    assert "2000.0" in read_member(data, "forecasts.csv")
    assert "0.42" in read_member(data, "daily_finance.csv")
    assert "allow_self_consumption" in read_member(data, "audit_log.csv")
    assert "grid_charge_to_target" in read_member(data, "plan_history.csv")
    assert "1234.5" in read_member(data, "gas.csv")
    # _seed() records a single raw sample -> no session (below min_duration) -> header only.
    ev_sessions_csv = read_member(data, "ev_sessions.csv")
    assert ev_sessions_csv.strip() == ",".join(EV_SESSION_COLUMNS)
    manifest = json.loads(read_member(data, "manifest.json"))
    assert manifest["kind"] == "ems-export-package"
    assert manifest["counts"]["raw_samples"] == 1
    assert manifest["counts"]["prices"] == 1
    assert manifest["counts"]["forecasts"] == 1
    assert manifest["counts"]["plan_history"] == 1
    assert manifest["counts"]["gas"] == 1
    assert manifest["counts"]["ev_sessions"] == 0


def test_export_package_backfills_daily_finance_for_unviewed_days(tmp_path):
    # /api/finance only computes+stores a completed day's rollup when that day's window is
    # actually VIEWED, so a day nobody looked at is missing from `daily_finance` — the 07-04/07-05
    # -style export gap. Seed 3 consecutive completed days of raw+price history but pre-populate
    # `daily_finance` for only ONE of them (simulating "the user only ever opened one day's
    # finance view"); the export must self-complete the other two before reading the table back.
    # Days are relative to "today" (real wall clock) so the test is stable regardless of when it
    # runs, and a tight window (days=6) keeps the backfill sweep small and its count predictable.
    db = str(tmp_path / "ems.sqlite")
    today = datetime.now(UTC).date()
    day_list = [(today - timedelta(days=n)).isoformat() for n in (4, 3, 2)]  # 3 consecutive days

    async def seed():
        store = HistoryStore(db)
        await store.init()
        for d in day_list:
            ts = f"{d}T12:00:00+00:00"
            raw = RawSample(grid_power_w=500.0, solar_power_w=0.0, battery_power_w=0.0,
                            ev_power_w=0.0, soc_pct=50.0)
            await store.record(ts, raw, reconstruct(raw))
            await store.upsert_price_slots([(ts, 0.20)])
        # Only the FIRST day was ever "viewed" — pre-cached with the CURRENT calc_v, so the
        # backfill must trust it as-is (return the sentinel, not a freshly computed value).
        await store.upsert_daily_finance(day_list[0], {
            "day": day_list[0], "has_data": True, "saved_eur": -0.99, "price_coverage": 1.0,
            "calc_v": _FINANCE_CALC_VERSION,
        })
    asyncio.run(seed())

    with TestClient(_app(db)) as c:
        r = c.get("/api/export/package?days=6")
    assert r.status_code == 200
    data = r.content
    csv_text = read_member(data, "daily_finance.csv")
    for d in day_list:
        assert d in csv_text  # all three days present — the export backfilled the missing two
    assert "-0.99" in csv_text  # the pre-cached day's stored value was trusted, not recomputed

    manifest = json.loads(read_member(data, "manifest.json"))
    assert manifest["counts"]["daily_finance"] >= 3  # at least the 3 target days made it in

    async def rollup():
        s = HistoryStore(db)
        return await s.daily_finance_between(day_list[0], today.isoformat())
    rows = asyncio.run(rollup())
    assert {row["day"] for row in rows} >= set(day_list)  # backfill PERSISTED, not just returned


def test_export_package_backfill_is_best_effort_per_day(tmp_path, monkeypatch):
    # A single day's compute blowing up must not take down the whole export — it's logged and
    # skipped, and the rest of the window still exports (200, with the other days present).
    db = str(tmp_path / "ems.sqlite")
    today = datetime.now(UTC).date()
    day_list = [(today - timedelta(days=n)).isoformat() for n in (4, 3, 2)]
    flaky_day = day_list[1]

    async def seed():
        store = HistoryStore(db)
        await store.init()
        for d in day_list:
            ts = f"{d}T12:00:00+00:00"
            raw = RawSample(grid_power_w=500.0, solar_power_w=0.0, battery_power_w=0.0,
                            ev_power_w=0.0, soc_pct=50.0)
            await store.record(ts, raw, reconstruct(raw))
            await store.upsert_price_slots([(ts, 0.20)])
    asyncio.run(seed())

    import ems.web.api as api_mod
    real_day_finance = api_mod.day_finance

    def flaky_day_finance(raw, price_rows, *, day, **kwargs):
        if day == flaky_day:
            raise RuntimeError("boom — simulated compute failure")
        return real_day_finance(raw, price_rows, day=day, **kwargs)

    monkeypatch.setattr(api_mod, "day_finance", flaky_day_finance)

    with TestClient(_app(db)) as c:
        r = c.get("/api/export/package?days=6")
    assert r.status_code == 200  # one bad day doesn't fail the export
    csv_text = read_member(r.content, "daily_finance.csv")
    assert day_list[0] in csv_text and day_list[2] in csv_text
    assert flaky_day not in csv_text  # the failing day is skipped, not fatal


def test_manifest_carries_validation_payload_and_no_secrets(tmp_path):
    db = str(tmp_path / "ems.sqlite")
    _seed(db)
    with TestClient(_app(db)) as c:
        data = c.get("/api/export/package").content
    manifest = json.loads(read_member(data, "manifest.json"))
    # Production-validation payload present.
    assert manifest["operational"]["dry_run"] is True
    assert "timezone" in manifest["operational"]
    assert "strategy.mode" in manifest["config"]              # replay-safe planner knobs
    assert "data_quality" in manifest["health"]
    assert "capability_present" in manifest["health"]
    assert manifest["incidents"] == {  # the seeded audit entry is benign -> zero incidents
        "total": 0, "by_type": {}, "by_type_last_7_days": {}, "by_day": {},
        "most_recent": None, "last_7_days": 0,
    }
    # Privacy: no secrets / IPs / location keys anywhere in the manifest text.
    blob = json.dumps(manifest).lower()
    for leak in ("token", "secret", "_ip", "\"ip\"", "lat", "lon", "password"):
        assert leak not in blob, f"manifest leaked a sensitive key: {leak}"


def test_manifest_ev_block_shape_default_settings_and_null_soc_anchor(tmp_path):
    # No car settings changed, no SoC anchor ever set — the "feature never configured" shape:
    # every ev.* default, and soc_anchor explicitly null (not omitted) so a replay script can
    # always find the key.
    db = str(tmp_path / "ems.sqlite")
    _seed(db)
    with TestClient(_app(db)) as c:
        data = c.get("/api/export/package").content
    ev = json.loads(read_member(data, "manifest.json"))["ev"]
    assert ev["soc_anchor"] is None
    assert ev["advice_enabled"] is False
    assert ev["car_id"] == ""
    assert ev["battery_kwh"] == 57.5
    assert ev["charger_kw"] == 11.0
    assert ev["charge_efficiency"] == 0.90
    assert set(ev["schedule"]) == {"mon", "tue", "wed", "thu", "fri", "sat", "sun"}
    assert ev["schedule"]["mon"] == {"enabled": False, "min_pct": 80, "ready_by": "07:30"}
    # No location/token/IP in the ev block specifically (the config needed to replay the
    # algorithm carries none of those).
    blob = json.dumps(ev).lower()
    for leak in ("token", "secret", "_ip", "\"ip\"", "lat", "lon", "password"):
        assert leak not in blob, f"manifest.ev leaked a sensitive key: {leak}"


def test_manifest_ev_block_carries_config_and_soc_anchor_when_set(tmp_path):
    db = str(tmp_path / "ems.sqlite")
    _seed(db)

    async def seed_ev():
        settings = SettingsStore(db)
        await settings.init()
        await settings.set_many({
            "ev.advice_enabled": True, "ev.car_id": "my-tesla",
            "ev.battery_kwh": 75.0, "ev.charger_kw": 7.4, "ev.charge_efficiency": 0.92,
        })
        store = HistoryStore(db)
        await store.init()
        await store.set_car_soc_anchor(60.0, "2026-06-28T08:00:00+00:00")
    asyncio.run(seed_ev())

    with TestClient(_app(db)) as c:
        data = c.get("/api/export/package").content
    ev = json.loads(read_member(data, "manifest.json"))["ev"]
    assert ev["advice_enabled"] is True
    assert ev["car_id"] == "my-tesla"
    assert ev["battery_kwh"] == 75.0
    assert ev["charger_kw"] == 7.4
    assert ev["charge_efficiency"] == 0.92
    assert ev["soc_anchor"] == {"pct": 60.0, "ts": "2026-06-28T08:00:00+00:00"}


def _seed_ev_charging_block(db: str) -> None:
    # Three samples 5 minutes apart at a steady 7 kW -> one detected session spanning 10 minutes
    # (>= the 5-min default min_duration) with a hand-computable zero-order-hold energy: 3 holds of
    # 5 min each at 7 kW = 3 * 7.0 * (5/60) = 1.75 kWh exactly.
    async def go():
        store = HistoryStore(db)
        await store.init()
        base = datetime(2026, 6, 28, 9, 0, 0, tzinfo=UTC)
        for i in range(3):
            ts = (base + timedelta(minutes=5 * i)).isoformat()
            raw = RawSample(grid_power_w=200.0, solar_power_w=0.0, battery_power_w=0.0,
                            ev_power_w=7000.0, soc_pct=50.0)
            await store.record(ts, raw, reconstruct(raw))
        await store.upsert_price_slots([(base.isoformat(), 0.15)])
    asyncio.run(go())


def test_export_package_ev_sessions_flow_from_seeded_raw_rows(tmp_path):
    db = str(tmp_path / "ems.sqlite")
    _seed_ev_charging_block(db)
    with TestClient(_app(db)) as c:
        data = c.get("/api/export/package?days=30").content
    csv_text = read_member(data, "ev_sessions.csv")
    lines = csv_text.strip().splitlines()
    assert lines[0] == ",".join(EV_SESSION_COLUMNS)
    assert len(lines) == 2  # header + exactly one detected session
    assert "1.75" in csv_text   # kwh
    assert "7.0" in csv_text    # avg_kw / peak_kw
    manifest = json.loads(read_member(data, "manifest.json"))
    assert manifest["counts"]["ev_sessions"] == 1


def test_package_includes_readme_and_validation_summary(tmp_path):
    db = str(tmp_path / "ems.sqlite")
    _seed(db)
    with TestClient(_app(db)) as c:
        data = c.get("/api/export/package").content
    assert {"README.md", "validation_summary.txt"} <= set(zip_names(data))
    readme = read_member(data, "README.md")
    assert "+ = importing" in readme and "+ = discharging" in readme   # sign conventions documented
    assert "raw_samples.csv" in readme
    assert "forecasts.csv" in readme
    assert "plan_history.csv" in readme
    assert "gas.csv" in readme
    assert "ev_sessions.csv" in readme
    assert "DETECTED" in readme and "not reported by the car" in readme  # detected, not telemetry
    assert "manifest.ev" in readme                                       # ev manifest documented
    summary = read_member(data, "validation_summary.txt")
    assert "Run mode:" in summary and "DRY-RUN" in summary              # run mode legible
    assert "Measured savings over the window: €0.42" in summary         # savings total from finance
    assert "Data quality:" in summary
    assert "Incidents" in summary                                       # control-health section
    assert "manifest.incidents" in readme                               # documented in the README


def test_validation_summary_includes_solar_forecast_skill_section(tmp_path):
    # _seed() records one raw sample (solar_power_w=3500.0) and one canonical ledger row
    # (low=1000.0, expected=2000.0, high=3000.0) both at 2026-06-28T10:00:00+00:00 — the same
    # 15-min slot — so the export should match them into exactly one slot and score the error.
    db = str(tmp_path / "ems.sqlite")
    _seed(db)
    with TestClient(_app(db)) as c:
        data = c.get("/api/export/package").content
    summary = read_member(data, "validation_summary.txt")
    assert "Solar forecast skill" in summary
    assert "Matched slots:   1" in summary
    assert "Bias (mean):     1500.0 W" in summary     # actual 3500 - p50 2000
    assert "MAE:             1500.0 W" in summary
    assert "Band coverage:   0.0% within [p10, p90]" in summary  # 3500 is above p90=3000
    assert "Actual vs P50:   0.88 kWh vs 0.5 kWh" in summary
    assert "under-predicted solar" in summary


def test_package_never_leaks_a_stored_secret_value(tmp_path):
    # Definitive redaction check: seed a recognisable secret value + a config-change audit that
    # names the secret KEY, then assert the secret VALUE appears in NO member of the ZIP.
    db = str(tmp_path / "ems.sqlite")
    secret = "S3CRET-TOKEN-DO-NOT-LEAK"

    async def seed_secret():
        settings = SettingsStore(db)
        await settings.init()
        await settings.set_many({"access.web_token": secret, "tibber.token": secret,
                                  "ev.advice_enabled": True, "ev.car_id": "my-tesla"})
        audit = AuditStore(db)
        await audit.init()
        await audit.append("2026-06-28T11:00:00+00:00", "config_change",
                           "Changed 1 setting(s): access.web_token",
                           {"keys": ["access.web_token"], "secrets": ["access.web_token"]})
        store = HistoryStore(db)
        await store.init()
        await store.set_car_soc_anchor(60.0, "2026-06-28T08:00:00+00:00")

    _seed(db)
    asyncio.run(seed_secret())
    with TestClient(_app(db)) as c:
        data = c.get("/api/export/package").content
    for name in zip_names(data):
        assert secret not in read_member(data, name), f"secret leaked into {name}"
    # The audit entry is present (by key name), proving we didn't just drop the data.
    assert "access.web_token" in read_member(data, "audit_log.csv")
    # The new EV members specifically: real (non-secret) config flows through, ev_sessions.csv is
    # clean, and the secret never rides along in the manifest's new "ev" block.
    manifest = json.loads(read_member(data, "manifest.json"))
    assert manifest["ev"]["car_id"] == "my-tesla"
    assert manifest["ev"]["soc_anchor"] == {"pct": 60.0, "ts": "2026-06-28T08:00:00+00:00"}
    assert secret not in json.dumps(manifest["ev"])
    assert secret not in read_member(data, "ev_sessions.csv")


def test_recorder_wiring_persists_a_plan_history_row_on_startup(tmp_path):
    # End-to-end: create_app wires the `_plan_snapshot` closure onto the recorder
    # (observability-data), so the very first startup tick (recorder.record_now(), called from
    # the app lifespan) should already record one plan_history row.
    from ems.freshness import FreshnessTracker
    from ems.sense import Recorder

    db = str(tmp_path / "ems.sqlite")
    store = HistoryStore(db)
    freshness = FreshnessTracker()
    freshness.register("grid", "solar", "ev", "battery", "soc")
    recorder = Recorder(MockSource(), store, freshness, price_source=MockPriceSource(AMS))
    app = create_app(
        MockSource(), dry_run=True, dev_mode="mock", tz=AMS, store=store,
        settings_store=SettingsStore(db), audit_store=AuditStore(db),
        price_source=MockPriceSource(AMS), recorder=recorder,
    )
    assert recorder.plan_provider is not None  # wired synchronously inside create_app

    with TestClient(app):
        pass  # triggers lifespan startup, which calls recorder.record_now() once

    rows = asyncio.run(store.plan_history_between(
        "2020-01-01T00:00:00+00:00", "2030-01-01T00:00:00+00:00"))
    assert len(rows) == 1
    assert rows[0]["strategy"] in ("summer", "winter")
    assert rows[0]["soc_pct"] == 55.0  # MockSource's steady-state SoC


def test_create_app_sets_plan_provider_when_recorder_passed(tmp_path):
    # Minimal wiring check (in case a full recorder tick is ever awkward to exercise): passing a
    # recorder must leave it with a callable plan_provider, and omitting one must not error.
    from ems.freshness import FreshnessTracker
    from ems.sense import Recorder

    db = str(tmp_path / "ems.sqlite")
    recorder = Recorder(MockSource(), HistoryStore(db), FreshnessTracker())
    assert recorder.plan_provider is None
    create_app(MockSource(), dry_run=True, dev_mode="mock", tz=AMS, recorder=recorder)
    assert recorder.plan_provider is not None
    assert callable(recorder.plan_provider)
    # No recorder passed at all → create_app must not require one.
    create_app(MockSource(), dry_run=True, dev_mode="mock", tz=AMS)


# ---- endpoint: GET /api/incidents — the same rollup, without downloading the export ----

def test_incidents_endpoint_rolls_up_the_audit_log(tmp_path):
    db = str(tmp_path / "ems.sqlite")

    async def seed():
        audit = AuditStore(db)
        await audit.init()
        await audit.append("2026-06-28T09:00:00+00:00", "battery_decision",
                            "Battery cluster MISMATCH — 1 tower(s) NOT following the commanded "
                            "mode", {})
        await audit.append("2026-06-28T10:00:00+00:00", "battery_decision",
                            "Would set battery to charge — cheap window", {})
    asyncio.run(seed())

    with TestClient(_app(db)) as c:
        body = c.get("/api/incidents").json()
    assert body["incidents"]["total"] == 1
    assert body["incidents"]["by_type"] == {"cluster_mismatch": 1}
    assert body["incidents"]["by_type_last_7_days"] == {"cluster_mismatch": 1}


def test_incidents_endpoint_empty_without_audit_store():
    app = create_app(MockSource(), dry_run=True, dev_mode="mock", tz=AMS)
    with TestClient(app) as c:
        body = c.get("/api/incidents").json()
    assert body["incidents"] == {
        "total": 0, "by_type": {}, "by_type_last_7_days": {}, "by_day": {},
        "most_recent": None, "last_7_days": 0,
    }


def test_incident_rollup_matches_hyphenated_failsafe():
    # Runtime text is "fail-safe" (hyphenated); the classifier must still catch it as a fallback.
    from ems.export_package import incident_rollup
    rows = [
        {"ts": "2026-07-06T20:00:00+00:00",
         "summary": "Fell back to AUTO (fail-safe)", "detail": {}},
        {"ts": "2026-07-06T21:00:00+00:00", "summary": "set auto", "detail": {}},  # benign
    ]
    r = incident_rollup(rows)
    assert r["total"] == 1 and r["by_type"].get("fallback") == 1


# ---- endpoint: GET /api/advisor/solar-confidence — advisory only, never applied automatically ----

def _seed_solar_evidence(db: str) -> None:
    # 48 matched daytime (expected=1000W >= 200W floor) slots inside the last 14 days, 12 each of
    # ratio 0.7/0.8/0.9/1.0 (interleaved — recommend_solar_confidence sorts internally, so order
    # doesn't matter): p25 -> 70%, median -> 80%, recommended -> 70% (matches test_analysis.py's
    # known-ratio case, against the default current_pct=80.0 -> delta -10.0).
    # Seeded as CANONICAL (canonical=1) prediction-ledger rows — the advisor now scores
    # `ledger_canonical_between('solar', ...)`, the single scoring source (design §3.3).
    async def go():
        store = HistoryStore(db)
        await store.init()
        anchor = datetime.now(UTC).replace(minute=0, second=0, microsecond=0) - timedelta(hours=1)
        ratios = [0.7, 0.8, 0.9, 1.0]
        for i in range(48):
            ts = (anchor - timedelta(minutes=15 * i)).isoformat()
            solar_w = 1000.0 * ratios[i % 4]
            raw = RawSample(grid_power_w=100.0, solar_power_w=solar_w, battery_power_w=0.0,
                            ev_power_w=0.0, soc_pct=50.0)
            await store.record(ts, raw, reconstruct(raw))
            await store.ledger_append(
                [(ts, "solar", ts, 500.0, 1000.0, 1500.0, "test", None, None, 1)])
    asyncio.run(go())


def test_advisor_endpoint_returns_recommendation_with_enough_evidence(tmp_path):
    db = str(tmp_path / "ems.sqlite")
    _seed_solar_evidence(db)
    with TestClient(_app(db)) as c:
        body = c.get("/api/advisor/solar-confidence").json()
    advice = body["advice"]
    assert advice is not None
    assert advice["n_slots"] == 48
    assert advice["p25_ratio_pct"] == 70.0
    assert advice["median_ratio_pct"] == 80.0
    assert advice["recommended_pct"] == 70.0
    assert advice["current_pct"] == 80.0  # planner.solar_confidence default
    assert advice["delta_pct"] == -10.0


def test_advisor_endpoint_returns_null_advice_without_enough_evidence(tmp_path):
    db = str(tmp_path / "ems.sqlite")
    with TestClient(_app(db)) as c:  # fresh store — zero matched slots
        body = c.get("/api/advisor/solar-confidence").json()
    assert body == {"advice": None}


def test_advisor_endpoint_returns_null_advice_without_a_store():
    app = create_app(MockSource(), dry_run=True, dev_mode="mock", tz=AMS)
    with TestClient(app) as c:
        body = c.get("/api/advisor/solar-confidence").json()
    assert body == {"advice": None}
