"""Insights report assembly: window resolution (day/week/month/year), pure build_report, and the
/api/report endpoint over a seeded history DB. No hardware."""
import asyncio
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from ems.domain import RawSample
from ems.load_model import DerivedSample
from ems.reporting import (
    _import_price_slots,
    build_report,
    gas_m3_consumed,
    gas_summary,
    resolve_window,
)
from ems.sources.mock import MockSource
from ems.sources.prices import MockPriceSource
from ems.storage.history import HistoryStore
from ems.storage.settings import SettingsStore
from ems.web.api import create_app

AMS = ZoneInfo("Europe/Amsterdam")
PAST = datetime(2026, 6, 28, 12, 0, tzinfo=AMS)  # a completed past day (deterministic)


class _P:
    """Minimal price slot (build_report only reads .start / .eur_per_kwh)."""

    def __init__(self, start: datetime, eur: float):
        self.start = start
        self.eur_per_kwh = eur


def test_resolve_window_all_periods():
    tz = AMS
    now = datetime(2026, 7, 1, 12, 0, tzinfo=tz)  # a Wednesday
    anchor = date(2026, 7, 1)
    s, e, label, partial = resolve_window("day", anchor, tz, now)
    assert label == "2026-07-01" and (e - s) == timedelta(days=1) and partial is True
    s, e, _, _ = resolve_window("week", anchor, tz, now)
    assert (e - s) == timedelta(days=7) and s.date() == date(2026, 6, 29)  # Monday
    s, e, label, _ = resolve_window("month", anchor, tz, now)
    assert s.date() == date(2026, 7, 1) and e.date() == date(2026, 8, 1) and label == "2026-07"
    s, e, label, _ = resolve_window("year", anchor, tz, now)
    assert s.date() == date(2026, 1, 1) and e.date() == date(2027, 1, 1) and label == "2026"


def test_resolve_window_december_month_rolls_the_year():
    _, e, _, _ = resolve_window("month", date(2026, 12, 15), AMS, datetime(2027, 1, 2, tzinfo=AMS))
    assert e.date() == date(2027, 1, 1)


def test_resolve_window_not_partial_for_a_past_period():
    now = datetime(2026, 7, 1, 12, 0, tzinfo=AMS)
    _, _, _, partial = resolve_window("day", date(2026, 6, 1), AMS, now)
    assert partial is False


def test_build_report_has_flows_and_three_scores():
    start = datetime(2026, 6, 28, tzinfo=UTC)
    end = start + timedelta(days=1)
    raw = [{"ts": (start + timedelta(hours=12)).isoformat(), "grid_power_w": -1000,
            "solar_power_w": 3000, "battery_power_w": -1000, "ev_power_w": 0.0, "soc_pct": 50.0}]
    der = [{"ts": (start + timedelta(hours=12)).isoformat(), "house_load_w": 1000,
            "non_ev_load_w": 1000}]
    prices = [_P(start + timedelta(hours=h), round(0.10 + 0.01 * h, 3)) for h in range(24)]
    r = build_report(raw, der, prices, period="day", start=start, end=end,
                     label="2026-06-28", partial=False, grid_factor=0.27)
    assert r.period == "day" and r.flows["has_data"] is True
    assert {s["key"] for s in r.scores} == {"self_consumption", "co2", "best_price"}
    # Every score has value/raw/unit/explanation fields.
    assert all({"value", "raw", "unit", "explanation"} <= set(s) for s in r.scores)


def test_import_price_slots_aligns_hourly_prices_and_ignores_export():
    start = datetime(2026, 6, 28, tzinfo=UTC)
    end = start + timedelta(hours=2)
    raw = [
        {"ts": start.isoformat(), "grid_power_w": 2000},  # import 0.5 kWh in the 00:00 slot
        # the 01:00 slot exports (negative grid) → counts as 0 import:
        {"ts": (start + timedelta(hours=1)).isoformat(), "grid_power_w": -1000},
    ]
    prices = [_P(start, 0.10), _P(start + timedelta(hours=1), 0.30)]  # hourly, coarser than 15-min
    slots = _import_price_slots(raw, prices, start, end)
    assert (round(slots[0][0], 3), slots[0][1]) == (0.5, 0.10)  # 00:00 slot: 0.5 kWh at €0.10
    assert slots[1][0] == 0.0 and slots[1][1] == 0.30           # 01:00 slot: export, priced €0.30


def test_build_report_threads_gas_into_co2_score():
    start = datetime(2026, 6, 28, tzinfo=UTC)
    end = start + timedelta(days=1)
    raw = [{"ts": (start + timedelta(hours=12)).isoformat(), "grid_power_w": 1000,
            "solar_power_w": 0, "battery_power_w": 0, "ev_power_w": 0.0, "soc_pct": 50.0}]
    der = [{"ts": (start + timedelta(hours=12)).isoformat(), "house_load_w": 1000,
            "non_ev_load_w": 1000}]
    r = build_report(raw, der, [], period="day", start=start, end=end, label="x", partial=False,
                     grid_factor=0.27, gas_factor=1.78, gas_m3=50.0)
    co2 = next(s for s in r.scores if s["key"] == "co2")
    assert "Gas heating" in co2["explanation"]  # gas folded into the footprint


def test_build_report_grid_factor_note_appended_to_co2_explanation():
    # Roadmap F3: the API appends a short note when it resolved grid_factor from a live window
    # average rather than the flat setting — build_report's job is just to thread it onto the
    # existing co2_score explanation, unchanged otherwise.
    start = datetime(2026, 6, 28, tzinfo=UTC)
    end = start + timedelta(days=1)
    raw = [{"ts": (start + timedelta(hours=12)).isoformat(), "grid_power_w": 1000,
            "solar_power_w": 0, "battery_power_w": 0, "ev_power_w": 0.0, "soc_pct": 50.0}]
    der = [{"ts": (start + timedelta(hours=12)).isoformat(), "house_load_w": 1000,
            "non_ev_load_w": 1000}]
    r = build_report(raw, der, [], period="day", start=start, end=end, label="x", partial=False,
                     grid_factor=0.19, grid_factor_note=" (live grid signal, avg 0.19 kg/kWh)")
    co2 = next(s for s in r.scores if s["key"] == "co2")
    assert co2["explanation"].endswith("(live grid signal, avg 0.19 kg/kWh)")


def test_build_report_no_note_when_grid_factor_note_omitted():
    start = datetime(2026, 6, 28, tzinfo=UTC)
    end = start + timedelta(days=1)
    raw = [{"ts": (start + timedelta(hours=12)).isoformat(), "grid_power_w": 1000,
            "solar_power_w": 0, "battery_power_w": 0, "ev_power_w": 0.0, "soc_pct": 50.0}]
    der = [{"ts": (start + timedelta(hours=12)).isoformat(), "house_load_w": 1000,
            "non_ev_load_w": 1000}]
    r = build_report(raw, der, [], period="day", start=start, end=end, label="x", partial=False,
                     grid_factor=0.27)
    co2 = next(s for s in r.scores if s["key"] == "co2")
    assert "live grid signal" not in co2["explanation"]


def test_gas_m3_consumed_is_last_minus_first():
    rows = [{"ts": "2026-06-28T00:00:00+00:00", "total_gas_m3": 1000.0},
            {"ts": "2026-06-28T12:00:00+00:00", "total_gas_m3": 1002.5},
            {"ts": "2026-06-28T23:00:00+00:00", "total_gas_m3": 1005.0}]
    assert gas_m3_consumed(rows) == 5.0


def test_gas_m3_consumed_fewer_than_two_rows_is_zero():
    assert gas_m3_consumed([]) == 0.0
    assert gas_m3_consumed([{"ts": "2026-06-28T00:00:00+00:00", "total_gas_m3": 1000.0}]) == 0.0


def test_gas_m3_consumed_never_negative():
    # A meter reset/rollover must never report negative use — floored at 0.
    rows = [{"ts": "2026-06-28T00:00:00+00:00", "total_gas_m3": 1000.0},
            {"ts": "2026-06-28T12:00:00+00:00", "total_gas_m3": 5.0}]
    assert gas_m3_consumed(rows) == 0.0


def test_gas_m3_consumed_is_monotonic_over_more_readings():
    rows = [{"ts": f"2026-06-28T{h:02d}:00:00+00:00", "total_gas_m3": 1000.0 + h * 0.1}
            for h in range(24)]
    assert abs(gas_m3_consumed(rows) - 2.3) < 1e-9


def test_gas_summary_math():
    # 10 m³ consumed @ €1.40/m³, 1.78 kg CO₂/m³.
    rows = [{"ts": "2026-06-28T00:00:00+00:00", "total_gas_m3": 1000.0},
            {"ts": "2026-06-28T23:00:00+00:00", "total_gas_m3": 1010.0}]
    g = gas_summary(rows, price_eur_per_m3=1.40, co2_factor=1.78)
    assert g == {"m3": 10.0, "kwh_eq": 97.7, "eur": 14.0, "co2_kg": 17.8}


def test_gas_summary_none_with_fewer_than_two_rows():
    assert gas_summary([], price_eur_per_m3=1.40, co2_factor=1.78) is None
    one = [{"ts": "2026-06-28T00:00:00+00:00", "total_gas_m3": 1000.0}]
    assert gas_summary(one, price_eur_per_m3=1.40, co2_factor=1.78) is None


def _seed(db: str) -> None:
    async def go():
        store = HistoryStore(db)
        await store.init()
        await store.record(PAST.isoformat(), RawSample(-1000, 3000, -1000, 0.0, 50.0),
                           DerivedSample(1000, 1000))
        await store.record((PAST + timedelta(hours=6)).isoformat(),
                           RawSample(800, 0, 0, 0.0, 50.0), DerivedSample(800, 800))
    asyncio.run(go())


def _app(db: str):
    return create_app(
        MockSource(), dry_run=True, dev_mode="mock", tz=AMS,
        store=HistoryStore(db), settings_store=SettingsStore(db),
        price_source=MockPriceSource(AMS),
    )


def test_report_endpoint_returns_day_report(tmp_path):
    db = str(tmp_path / "ems.sqlite")
    _seed(db)
    with TestClient(_app(db)) as c:
        b = c.get("/api/report?period=day&date=2026-06-28").json()
    assert b["period"] == "day" and b["flows"]["has_data"] is True
    assert {s["key"] for s in b["scores"]} == {"self_consumption", "co2", "best_price"}
    assert b["label"] == "2026-06-28"


def test_report_endpoint_reflects_gas_in_co2_score(tmp_path):
    # B-02: /api/report reads the window's gas readings, computes the delta, and threads it into
    # the CO2 score — the step-down explanation text is the observable proof it's wired end to end.
    db = str(tmp_path / "ems.sqlite")
    _seed(db)

    async def seed_gas():
        store = HistoryStore(db)
        await store.init()
        await store.record_gas(PAST.astimezone(UTC).isoformat(), 1000.0)
        await store.record_gas((PAST + timedelta(hours=6)).astimezone(UTC).isoformat(), 1050.0)
    asyncio.run(seed_gas())

    with TestClient(_app(db)) as c:
        b = c.get("/api/report?period=day&date=2026-06-28").json()
    co2 = next(s for s in b["scores"] if s["key"] == "co2")
    assert "Gas heating" in co2["explanation"]  # co2_score's step-down text when gas > 0


def test_report_endpoint_no_gas_readings_omits_gas_from_co2_explanation(tmp_path):
    db = str(tmp_path / "ems.sqlite")
    _seed(db)
    with TestClient(_app(db)) as c:
        b = c.get("/api/report?period=day&date=2026-06-28").json()
    co2 = next(s for s in b["scores"] if s["key"] == "co2")
    assert "Gas heating" not in co2["explanation"]  # no gas meter/readings → untouched footprint


def test_report_endpoint_uses_live_carbon_window_average_when_present(tmp_path):
    # Roadmap F3: when the recorder has persisted carbon_intensity rows for this window, the CO2
    # score uses their plain average as grid_factor and the explanation notes it's live.
    db = str(tmp_path / "ems.sqlite")
    _seed(db)

    async def seed_carbon():
        store = HistoryStore(db)
        await store.init()
        await store.upsert_carbon([
            (PAST.astimezone(UTC).isoformat(), 0.10),
            ((PAST + timedelta(hours=6)).astimezone(UTC).isoformat(), 0.20),
        ])
    asyncio.run(seed_carbon())

    with TestClient(_app(db)) as c:
        b = c.get("/api/report?period=day&date=2026-06-28").json()
    co2 = next(s for s in b["scores"] if s["key"] == "co2")
    assert "live grid signal, avg 0.15 kg/kWh" in co2["explanation"]


def test_report_endpoint_flat_factor_when_no_carbon_rows(tmp_path):
    db = str(tmp_path / "ems.sqlite")
    _seed(db)
    with TestClient(_app(db)) as c:
        b = c.get("/api/report?period=day&date=2026-06-28").json()
    co2 = next(s for s in b["scores"] if s["key"] == "co2")
    assert "live grid signal" not in co2["explanation"]


def test_report_endpoint_gas_panel_present_with_two_or_more_readings(tmp_path):
    db = str(tmp_path / "ems.sqlite")
    _seed(db)

    async def seed_gas():
        store = HistoryStore(db)
        await store.init()
        await store.record_gas(PAST.astimezone(UTC).isoformat(), 1000.0)
        await store.record_gas((PAST + timedelta(hours=6)).astimezone(UTC).isoformat(), 1010.0)
    asyncio.run(seed_gas())

    with TestClient(_app(db)) as c:
        b = c.get("/api/report?period=day&date=2026-06-28").json()
    assert b["gas"] == {"m3": 10.0, "kwh_eq": 97.7, "eur": 14.0, "co2_kg": 17.8}


def test_report_endpoint_gas_panel_none_without_two_readings(tmp_path):
    db = str(tmp_path / "ems.sqlite")
    _seed(db)
    with TestClient(_app(db)) as c:
        b = c.get("/api/report?period=day&date=2026-06-28").json()
    assert b["gas"] is None


def test_report_endpoint_gas_panel_none_for_future_window(tmp_path):
    db = str(tmp_path / "ems.sqlite")
    _seed(db)
    with TestClient(_app(db)) as c:
        b = c.get("/api/report?period=day&date=2099-01-01").json()
    assert b["gas"] is None


def test_report_endpoint_validation_and_future(tmp_path):
    db = str(tmp_path / "ems.sqlite")
    _seed(db)
    with TestClient(_app(db)) as c:
        assert c.get("/api/report?period=day&date=not-a-date").status_code == 422
        assert c.get("/api/report?period=decade").status_code == 422  # bad period → pattern reject
        fut = c.get("/api/report?period=day&date=2099-01-01").json()
        assert fut["flows"]["has_data"] is False  # future window → honest empty, not an error


def test_build_series_day_15min_buckets():
    # Spec 2026-07-03 (A): the day view buckets P1/house/car/solar per 15-min slot.
    from ems.reporting import build_series

    day = datetime(2026, 6, 28, 0, 0, tzinfo=AMS)
    ts0 = day + timedelta(hours=12)
    raw, der = [], []
    for i in range(4):  # one hour of samples: import 1 kW, car 500 W, solar 800 W
        ts = (ts0 + timedelta(minutes=15 * i)).astimezone(UTC).isoformat()
        raw.append({"ts": ts, "grid_power_w": 1000.0, "solar_power_w": 800.0,
                    "battery_power_w": 0.0, "ev_power_w": 500.0, "soc_pct": 50.0})
        der.append({"ts": ts, "house_load_w": 2300.0, "non_ev_load_w": 1800.0})
    buckets = build_series(raw, der, period="day", start=day, end=day + timedelta(days=1), tz=AMS)
    assert len(buckets) == 96  # a stable axis: every slot present, sampled or not
    noon = next(b for b in buckets if b["start"] == ts0.astimezone(UTC).isoformat())
    assert abs(noon["grid_import_kwh"] - 0.25) < 1e-9
    assert noon["grid_export_kwh"] == 0.0
    assert abs(noon["house_kwh"] - 0.45) < 1e-9  # non-EV house: 1800 W × 15 min
    assert abs(noon["car_kwh"] - 0.125) < 1e-9
    assert abs(noon["solar_kwh"] - 0.2) < 1e-9
    assert noon["samples"] == 1
    empty = buckets[0]
    assert empty["samples"] == 0 and empty["grid_import_kwh"] == 0.0


def test_build_series_week_buckets_respect_local_days():
    from ems.reporting import build_series

    monday = datetime(2026, 6, 22, 0, 0, tzinfo=AMS)
    # 23:30 LOCAL on Tuesday = 21:30 UTC — must land in Tuesday's bucket, not Wednesday's.
    late = (monday + timedelta(days=1, hours=23, minutes=30)).astimezone(UTC)
    raw = [{"ts": late.isoformat(), "grid_power_w": -2000.0, "solar_power_w": 0.0,
            "battery_power_w": 0.0, "ev_power_w": 0.0, "soc_pct": 50.0}]
    der = [{"ts": late.isoformat(), "house_load_w": 0.0, "non_ev_load_w": 0.0}]
    buckets = build_series(raw, der, period="week", start=monday,
                           end=monday + timedelta(days=7), tz=AMS)
    assert len(buckets) == 7
    assert buckets[1]["start"].startswith("2026-06-23")
    assert abs(buckets[1]["grid_export_kwh"] - 0.5) < 1e-9  # 2 kW export × 15 min
    assert buckets[2]["grid_export_kwh"] == 0.0


def test_build_series_year_month_buckets():
    from ems.reporting import build_series

    jan1 = datetime(2026, 1, 1, 0, 0, tzinfo=AMS)
    ts = datetime(2026, 3, 10, 12, 0, tzinfo=AMS).astimezone(UTC)
    raw = [{"ts": ts.isoformat(), "grid_power_w": 4000.0, "solar_power_w": 0.0,
            "battery_power_w": 0.0, "ev_power_w": 4000.0, "soc_pct": 50.0}]
    der = [{"ts": ts.isoformat(), "house_load_w": 4000.0, "non_ev_load_w": 0.0}]
    buckets = build_series(raw, der, period="year", start=jan1,
                           end=datetime(2027, 1, 1, tzinfo=AMS), tz=AMS)
    assert len(buckets) == 12
    assert abs(buckets[2]["car_kwh"] - 1.0) < 1e-9  # March
    assert buckets[0]["samples"] == 0


def _seed_prices(db: str) -> None:
    async def go():
        store = HistoryStore(db)
        await store.init()
        await store.upsert_price_slots([
            (PAST.astimezone(UTC).isoformat(), 0.20),
            ((PAST + timedelta(hours=6)).astimezone(UTC).isoformat(), 0.40),
        ])
    asyncio.run(go())


def test_report_endpoint_includes_series(tmp_path):
    db = str(tmp_path / "ems.sqlite")
    _seed(db)
    with TestClient(_app(db)) as c:
        b = c.get("/api/report?period=day&date=2026-06-28").json()
    assert len(b["series"]) == 96
    noon = next(s for s in b["series"] if s["start"] == PAST.astimezone(UTC).isoformat())
    assert abs(noon["grid_export_kwh"] - 0.25) < 1e-9  # −1000 W for one 15-min slot
    assert abs(noon["house_kwh"] - 0.25) < 1e-9


def test_finance_endpoint_computes_and_persists_rollup(tmp_path):
    db = str(tmp_path / "ems.sqlite")
    _seed(db)
    _seed_prices(db)
    with TestClient(_app(db)) as c:
        b = c.get("/api/finance?period=day&date=2026-06-28").json()
    assert len(b["days"]) == 1
    d = b["days"][0]
    assert d["day"] == "2026-06-28" and d["has_data"] is True
    assert d["price_coverage"] == 1.0
    # export 0.25 kWh @ .20 → −0.05; import 0.2 kWh @ .40 → +0.08; battery charged (no wear).
    assert abs(d["grid_cost_eur"] - 0.03) < 1e-9
    assert d["battery_cost_eur"] == 0.0
    assert abs(d["saved_eur"] - (-0.05)) < 1e-9  # honest negative: stored solar it could export
    assert abs(b["totals"]["saved_eur"] - (-0.05)) < 1e-9

    async def rollup():
        store = HistoryStore(db)
        return await store.daily_finance_between("2026-06-28", "2026-06-29")
    rows = asyncio.run(rollup())
    assert len(rows) == 1 and rows[0]["data"]["saved_eur"] == -0.05  # completed day persisted


def test_finance_endpoint_week_totals_and_empty_days(tmp_path):
    db = str(tmp_path / "ems.sqlite")
    _seed(db)
    _seed_prices(db)
    with TestClient(_app(db)) as c:
        b = c.get("/api/finance?period=week&date=2026-06-28").json()
    assert len(b["days"]) == 7  # Mon..Sun of that week (all in the past)
    with_data = [d for d in b["days"] if d["has_data"]]
    assert [d["day"] for d in with_data] == ["2026-06-28"]
    assert abs(b["totals"]["saved_eur"] - (-0.05)) < 1e-9
    assert b["totals"]["days_with_prices"] == 1
