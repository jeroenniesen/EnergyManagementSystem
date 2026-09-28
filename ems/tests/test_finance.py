"""Daily finance math (spec 2026-07-03): grid cost, battery wear, and money saved vs the
no-battery baseline — pure, from canned raw rows + price slots. No hardware, no I/O.

The tail of the file also covers the calc-version cache guard (an api-level test), which is how a
finance-math change — like the B-05 export re-pricing — reaches already-stored daily rows."""
import asyncio
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from ems.domain import RawSample
from ems.finance import day_finance, price_rows_by_local_day, raw_rows_by_local_day
from ems.load_model import reconstruct
from ems.sources.mock import MockSource
from ems.sources.prices import MockPriceSource
from ems.storage.history import HistoryStore
from ems.storage.settings import SettingsStore
from ems.web.api import _FINANCE_CALC_VERSION, create_app

DAY = datetime(2026, 6, 28, 0, 0, tzinfo=UTC)
AMS = ZoneInfo("Europe/Amsterdam")


def _rows(spans):
    """spans: [(start_hour, hours, grid_w, battery_w)] → raw rows on a 15-min grid."""
    out = []
    for start_h, hours, grid_w, battery_w in spans:
        t0 = DAY + timedelta(hours=start_h)
        for i in range(int(hours * 4)):
            ts = (t0 + timedelta(minutes=15 * i)).isoformat()
            out.append({"ts": ts, "grid_power_w": grid_w, "battery_power_w": battery_w})
    return out


def _prices(price_by_hour):
    out = []
    for hour, eur in price_by_hour.items():
        t0 = DAY + timedelta(hours=hour)
        for i in range(4):
            out.append({"start_ts": (t0 + timedelta(minutes=15 * i)).isoformat(),
                        "eur_per_kwh": eur})
    return out


def test_arbitrage_day_costs_and_savings():
    # 01:00-02:00 grid-charge 4 kW at €0.10; 19:00-21:00 battery serves 2 kW load at €0.40.
    rows = _rows([(1, 1, 4000.0, -4000.0), (19, 2, 0.0, 2000.0)])
    prices = _prices({1: 0.10, 19: 0.40, 20: 0.40})
    f = day_finance(rows, prices, day="2026-06-28", degradation_eur_per_kwh=0.05)
    assert f.has_data and f.price_coverage == 1.0
    assert abs(f.grid_import_kwh - 4.0) < 1e-9
    assert abs(f.grid_cost_eur - 0.40) < 1e-9  # 4 kWh × €0.10
    assert abs(f.battery_discharge_kwh - 4.0) < 1e-9
    assert abs(f.battery_cost_eur - 0.20) < 1e-9  # 4 kWh × €0.05 wear
    # Baseline (no battery): no charge import, but the 2 kW evening load imports 4 kWh × €0.40.
    assert abs(f.baseline_cost_eur - 1.60) < 1e-9
    assert abs(f.saved_eur - 1.00) < 1e-9  # 1.60 − 0.40 − 0.20


def test_export_credited_per_model():
    # Export is valued via the configured feed-in model (B-05 / post-2027). The DEFAULT is
    # net_metering (today's saldering = full price), so the historical numbers are unchanged; the
    # other models re-price the same exported kWh.
    idle_export = _rows([(12, 1, -1000.0, 0.0)])  # 1 kWh exported, battery idle

    # (a) default / net_metering reproduces the OLD expectation exactly: 1 kWh × €0.20 → −€0.20.
    f = day_finance(idle_export, _prices({12: 0.20}), day="2026-06-28")
    assert abs(f.grid_export_kwh - 1.0) < 1e-9
    assert abs(f.grid_cost_eur - (-0.20)) < 1e-9
    assert abs(f.saved_eur - 0.0) < 1e-9  # battery did nothing → nothing saved
    assert abs(day_finance(idle_export, _prices({12: 0.20}), day="2026-06-28",
                           export_price_model="net_metering").grid_cost_eur - (-0.20)) < 1e-9

    # (b) spot_minus_tax credits export at price − energy tax: 0.20 − 0.13 = 0.07 → −€0.07.
    f = day_finance(idle_export, _prices({12: 0.20}), day="2026-06-28",
                    export_price_model="spot_minus_tax", energy_tax_eur_per_kwh=0.13)
    assert abs(f.grid_cost_eur - (-0.07)) < 1e-9
    assert abs(f.saved_eur - 0.0) < 1e-9  # battery still idle

    # (c) fixed credits export at the flat feed-in tariff, ignoring spot: 1 kWh × €0.01 → −€0.01.
    f = day_finance(idle_export, _prices({12: 0.20}), day="2026-06-28",
                    export_price_model="fixed", fixed_feed_in_eur_per_kwh=0.01)
    assert abs(f.grid_cost_eur - (-0.01)) < 1e-9

    # (d) negative-spot + spot_minus_tax → the export credit goes NEGATIVE (−0.02 − 0.13 = −0.15),
    # so exporting COSTS money. The battery discharges 1 kW into a zero-load house (grid −1 kW,
    # battery +1 kW) → the no-battery baseline has no grid flow at all. Actual: export 1 kWh at a
    # −€0.15 credit = +€0.15 cost, plus €0.05 wear → the battery LOSES money vs baseline.
    batt_export = _rows([(12, 1, -1000.0, 1000.0)])
    f = day_finance(batt_export, _prices({12: -0.02}), day="2026-06-28",
                    export_price_model="spot_minus_tax", energy_tax_eur_per_kwh=0.13,
                    degradation_eur_per_kwh=0.05)
    assert abs(f.grid_export_kwh - 1.0) < 1e-9
    assert abs(f.grid_cost_eur - 0.15) < 1e-9        # exporting at a negative credit is a COST
    assert abs(f.baseline_cost_eur - 0.0) < 1e-9     # no-battery meter is flat: 0 grid flow
    assert abs(f.saved_eur - (-0.20)) < 1e-9         # 0.00 − 0.15 export cost − 0.05 wear
    # …and that is strictly WORSE than the same day under today's saldering (export earns spot).
    f_net = day_finance(batt_export, _prices({12: -0.02}), day="2026-06-28",
                        degradation_eur_per_kwh=0.05)
    assert f.saved_eur < f_net.saved_eur


def test_no_price_history_is_honest():
    rows = _rows([(1, 1, 1000.0, 0.0)])
    f = day_finance(rows, [], day="2026-06-28")
    assert f.has_data
    assert f.price_coverage == 0.0
    assert f.grid_cost_eur is None and f.saved_eur is None and f.baseline_cost_eur is None
    assert abs(f.grid_import_kwh - 1.0) < 1e-9  # energy is still reported


def test_irregular_samples_integrate_observed_duration_not_full_quarter_slots():
    rows = [
        {"ts": DAY.isoformat(), "grid_power_w": 1000.0, "battery_power_w": 0.0},
        {"ts": (DAY + timedelta(minutes=5)).isoformat(),
         "grid_power_w": 2000.0, "battery_power_w": 0.0},
        {"ts": (DAY + timedelta(minutes=25)).isoformat(),
         "grid_power_w": 3000.0, "battery_power_w": 0.0},
    ]
    prices = [{"start_ts": DAY.isoformat(), "eur_per_kwh": 0.20}]

    f = day_finance(
        rows,
        prices,
        day="2026-06-28",
        window_start=DAY,
        window_end=DAY + timedelta(minutes=30),
        sample_interval_seconds=300,
        max_hold_seconds=600,
    )

    # 1 kW × 5 min + 2 kW × 10 min + 3 kW × 5 min = 2/3 kWh. The 10-minute
    # recorder outage is uncovered, not silently promoted into a full 15-minute sample.
    assert abs(f.grid_import_kwh - (2 / 3)) < 1e-9
    assert abs(f.grid_cost_eur - (5 / 60 * 1 + 10 / 60 * 2) * 0.20) < 1e-9
    # Of 20 observed minutes, 15 have a price; coverage is duration-weighted, not row-counted.
    assert abs(f.price_coverage - 0.75) < 1e-9
    assert abs(f.sample_coverage - (20 / 30)) < 1e-9


def test_partial_price_coverage_reports_partial_window_money():
    # With ≥1 priced slot the € figures ARE reported (over the priced window), with price_coverage
    # signalling how much of the day they cover. Here 1 of 2 import hours is priced.
    rows = _rows([(1, 1, 2000.0, 0.0), (2, 1, 2000.0, 0.0)])  # 2 h import, only 1 h priced → 0.5
    f = day_finance(rows, _prices({1: 0.25}), day="2026-06-28", degradation_eur_per_kwh=0.05)
    assert abs(f.price_coverage - 0.5) < 1e-9
    assert abs(f.grid_import_kwh - 4.0) < 1e-9        # full-day energy always reported
    # € cover only the priced hour: 2 kW × 1 h = 2 kWh × €0.25 = €0.50 import cost, no discharge.
    assert abs(f.grid_cost_eur - 0.50) < 1e-9
    assert abs(f.baseline_cost_eur - 0.50) < 1e-9
    assert abs(f.battery_cost_eur - 0.0) < 1e-9
    assert abs(f.saved_eur - 0.0) < 1e-9              # honest partial figure, not None


def test_partial_coverage_does_not_distort_savings():
    # The reviewed distortion: battery wear was charged over the WHOLE day while cost/benefit used
    # only priced slots. Battery discharges 2 kW for 2 h in the UNPRICED evening; a small midday
    # import is priced. `dis_priced` charges wear only on priced-slot discharge (0 here), so the
    # saving is a clean €0 for the priced window — NOT the old distorted negative number.
    rows = _rows([(12, 1, 500.0, 0.0), (19, 2, 0.0, 2000.0)])  # priced midday, unpriced discharge
    f = day_finance(rows, _prices({12: 0.20}), day="2026-06-28", degradation_eur_per_kwh=0.05)
    assert f.price_coverage < 0.9
    assert abs(f.battery_cost_eur - 0.0) < 1e-9       # discharge was unpriced → no wear charged
    assert abs(f.saved_eur - 0.0) < 1e-9              # not the old ≈ −€0.20 distortion
    assert abs(f.battery_discharge_kwh - 4.0) < 1e-9  # 4 kWh discharged, still reported in full


def test_wear_charged_only_on_priced_slot_discharge():
    # The heart of the fix: at partial coverage, wear counts ONLY the discharge in PRICED slots.
    # 1 h discharge priced + 1 h discharge unpriced → wear on 2 kWh, not the full 4 kWh.
    # (Reverting `battery_cost = dis_priced * deg` to `dis * deg` would make this €0.20 and fail.)
    rows = _rows([(19, 1, 0.0, 2000.0), (20, 1, 0.0, 2000.0)])  # discharge 19h priced, 20h unpriced
    f = day_finance(rows, _prices({19: 0.40}), day="2026-06-28", degradation_eur_per_kwh=0.05)
    assert abs(f.battery_discharge_kwh - 4.0) < 1e-9  # full discharge still reported
    assert abs(f.battery_cost_eur - 0.10) < 1e-9      # 2 kWh priced-slot discharge × €0.05
    # baseline in the priced hour imports the 2 kWh the battery covered: 2 kWh × €0.40 = €0.80.
    assert abs(f.baseline_cost_eur - 0.80) < 1e-9
    assert abs(f.saved_eur - 0.70) < 1e-9             # 0.80 − 0.00 cost − 0.10 wear


def test_empty_day():
    f = day_finance([], [], day="2026-06-28")
    assert not f.has_data
    assert f.grid_cost_eur is None and f.saved_eur is None
    assert f.grid_import_kwh == 0.0
    d = f.to_dict()
    assert d["day"] == "2026-06-28" and d["has_data"] is False


def _econ_app(db: str):
    from ems.tests.conftest import NO_HISTORY_PURGE

    return create_app(
        MockSource(), dry_run=True, dev_mode="mock", tz=AMS,
        store=HistoryStore(db), settings_store=SettingsStore(db),
        price_source=MockPriceSource(AMS),
        **NO_HISTORY_PURGE,
    )


def test_maintenance_caches_yesterday_finance_and_second_tick_no_ops(tmp_path):
    # F5 (finance year undercount): daily_finance used to be written only on a view/export, so an
    # unviewed day beyond the 90-day raw purge became permanently uncomputable. The nightly
    # maintenance step must ALSO materialize YESTERDAY's finance row (mirroring daily_energy), and
    # be idempotent — a second maintenance tick finds the cached row and does NOT re-upsert.
    db = str(tmp_path / "ems.sqlite")
    now_local = datetime.now(UTC).astimezone(AMS)
    yesterday = now_local.date() - timedelta(days=1)

    class _CountingStore(HistoryStore):
        finance_upserts = 0

        async def upsert_daily_finance(self, day, data):
            type(self).finance_upserts += 1
            await super().upsert_daily_finance(day, data)

    store = _CountingStore(db)

    async def seed():
        await store.init()
        ts = datetime(yesterday.year, yesterday.month, yesterday.day, 12, tzinfo=AMS)
        iso = ts.astimezone(UTC).isoformat()
        raw = RawSample(grid_power_w=2000.0, solar_power_w=0.0, battery_power_w=0.0,
                        ev_power_w=0.0, soc_pct=50.0)
        await store.record(iso, raw, reconstruct(raw))
        await store.upsert_price_slots([(iso, 0.30)])
        await store.close()

    asyncio.run(seed())

    def _app():
        from ems.tests.conftest import NO_HISTORY_PURGE

        # backup disabled (keep=0) so the maintenance loop doesn't clutter tmp with snapshots.
        return create_app(
            MockSource(), dry_run=True, dev_mode="mock", tz=AMS,
            store=store, settings_store=SettingsStore(db),
            price_source=MockPriceSource(AMS), **NO_HISTORY_PURGE)

    # Boot 1: the lifespan's maintenance loop runs its first tick to completion on context exit.
    _CountingStore.finance_upserts = 0
    with TestClient(_app()):
        pass
    boot1_upserts = _CountingStore.finance_upserts

    async def stored():
        s = HistoryStore(db)
        return await s.daily_finance_between(
            yesterday.isoformat(), (yesterday + timedelta(days=1)).isoformat())

    cached = asyncio.run(stored())

    # Boot 2: yesterday is already cached under the current calc_v → the second tick is a no-op.
    _CountingStore.finance_upserts = 0
    with TestClient(_app()):
        pass
    boot2_upserts = _CountingStore.finance_upserts

    assert len(cached) == 1  # the first maintenance tick materialized yesterday's finance row
    assert cached[0]["data"]["calc_v"] == _FINANCE_CALC_VERSION
    assert boot1_upserts >= 1  # first tick wrote it
    assert boot2_upserts == 0  # idempotent — the cached row was skipped, not re-written


def test_calc_version_bump_invalidates_stored_finance_row(tmp_path):
    # calc_v guards the daily_finance cache: a row cached under an OLDER finance formula must be
    # RECOMPUTED (so a math fix — like this B-05 export re-pricing — reaches history), while a row
    # at the CURRENT version is trusted as-is. Proving both directions proves the version gates it.
    db = str(tmp_path / "ems.sqlite")

    async def seed(stored_calc_v: int) -> None:
        store = HistoryStore(db)
        await store.init()
        ts = "2026-06-28T12:00:00+00:00"  # 14:00 local AMS → inside the completed local day
        raw = RawSample(grid_power_w=2000.0, solar_power_w=0.0, battery_power_w=0.0,
                        ev_power_w=0.0, soc_pct=50.0)
        await store.record(ts, raw, reconstruct(raw))
        await store.upsert_price_slots([(ts, 0.30)])
        # A completed day pre-cached with a SENTINEL saving no honest recompute would ever produce.
        await store.upsert_daily_finance("2026-06-28", {
            "day": "2026-06-28", "has_data": True, "saved_eur": 999.0,
            "price_coverage": 1.0, "calc_v": stored_calc_v,
        })

    def fetch() -> dict:
        with TestClient(_econ_app(db)) as c:
            return c.get("/api/finance?period=day&date=2026-06-28").json()["days"][0]

    async def stored_row() -> dict:
        store = HistoryStore(db)
        return (await store.daily_finance_between("2026-06-28", "2026-06-29"))[0]["data"]

    # Stored under an OLD version → invalidated + recomputed (sentinel gone, re-stamped current).
    asyncio.run(seed(_FINANCE_CALC_VERSION - 1))
    d = fetch()
    assert d["saved_eur"] != 999.0
    assert abs(d["saved_eur"] - 0.0) < 1e-9  # import cost == baseline, no battery → nothing saved
    assert asyncio.run(stored_row())["calc_v"] == _FINANCE_CALC_VERSION

    # Stored under the CURRENT version → trusted verbatim (the sentinel survives).
    asyncio.run(seed(_FINANCE_CALC_VERSION))
    assert fetch()["saved_eur"] == 999.0


# --- raw_rows_by_local_day / price_rows_by_local_day (BACKLOG B-49: finance N+1 batching) -------

def _row_at(y, m, d, h, grid_w) -> dict:
    ts = datetime(y, m, d, h, tzinfo=AMS).astimezone(UTC).isoformat()
    return {"ts": ts, "grid_power_w": grid_w}


def test_raw_rows_by_local_day_groups_a_multi_day_window():
    start = datetime(2026, 6, 28, tzinfo=AMS)
    end = start + timedelta(days=3)
    rows = [
        _row_at(2026, 6, 28, 10, 1),
        _row_at(2026, 6, 28, 11, 2),
        _row_at(2026, 6, 29, 10, 3),
    ]
    by_day = raw_rows_by_local_day(rows, start, end, AMS)
    # Every local day in the window is present, even the one with zero rows.
    assert set(by_day) == {"2026-06-28", "2026-06-29", "2026-06-30"}
    assert len(by_day["2026-06-28"]) == 2
    assert len(by_day["2026-06-29"]) == 1
    assert by_day["2026-06-30"] == []


def test_raw_rows_by_local_day_respects_local_midnight_not_utc():
    # 23:30 LOCAL on the 28th = 21:30 UTC — must land in the 28th's bucket, not spill into the 29th
    # (mirrors build_series' week-bucket DST/local-midnight test).
    start = datetime(2026, 6, 28, tzinfo=AMS)
    end = start + timedelta(days=2)
    late = datetime(2026, 6, 28, 23, 30, tzinfo=AMS).astimezone(UTC)
    rows = [{"ts": late.isoformat(), "grid_power_w": 1}]
    by_day = raw_rows_by_local_day(rows, start, end, AMS)
    assert len(by_day["2026-06-28"]) == 1
    assert by_day["2026-06-29"] == []


def test_raw_rows_by_local_day_drops_rows_outside_the_window():
    start = datetime(2026, 6, 28, tzinfo=AMS)
    end = start + timedelta(days=1)
    rows = [
        _row_at(2026, 6, 27, 23, 1),
        _row_at(2026, 6, 28, 12, 2),
        _row_at(2026, 6, 29, 1, 3),
    ]
    by_day = raw_rows_by_local_day(rows, start, end, AMS)
    assert set(by_day) == {"2026-06-28"}
    assert len(by_day["2026-06-28"]) == 1


def test_raw_rows_by_local_day_drops_unparseable_ts():
    start = datetime(2026, 6, 28, tzinfo=AMS)
    end = start + timedelta(days=1)
    rows = [{"ts": "not-a-timestamp", "grid_power_w": 1}]
    by_day = raw_rows_by_local_day(rows, start, end, AMS)
    assert by_day["2026-06-28"] == []


def test_price_rows_by_local_day_uses_start_ts_field():
    start = datetime(2026, 6, 28, tzinfo=AMS)
    end = start + timedelta(days=1)
    rows = [{"start_ts": datetime(2026, 6, 28, 10, tzinfo=AMS).astimezone(UTC).isoformat(),
            "eur_per_kwh": 0.20}]
    by_day = price_rows_by_local_day(rows, start, end, AMS)
    assert by_day["2026-06-28"] == rows


def test_grouped_rows_feed_day_finance_identically_to_the_unbatched_per_day_fetch():
    # The whole point of batching: day_finance() fed the GROUPED slice must produce the exact same
    # result as day_finance() fed rows that were fetched ONE DAY AT A TIME.
    day0 = datetime(2026, 6, 28, tzinfo=AMS)
    raw = _rows([(1, 1, 4000.0, -4000.0), (19, 2, 0.0, 2000.0)])  # spans one day only
    prices = _prices({1: 0.10, 19: 0.40, 20: 0.40})
    unbatched = day_finance(raw, prices, day="2026-06-28", degradation_eur_per_kwh=0.05)

    raw_by_day = raw_rows_by_local_day(raw, day0, day0 + timedelta(days=1), AMS)
    price_by_day = price_rows_by_local_day(prices, day0, day0 + timedelta(days=1), AMS)
    batched = day_finance(raw_by_day["2026-06-28"], price_by_day["2026-06-28"],
                          day="2026-06-28", degradation_eur_per_kwh=0.05)
    assert batched.to_dict() == unbatched.to_dict()


# --- B-36 / #80: savings breakdown + DST kWh sums -------------------------------------------------

def test_arbitrage_breakdown_is_avoided_expensive_and_sums_to_saved():
    # Cheap night charge + expensive evening discharge → "not bought at expensive hours".
    rows = _rows([(1, 1, 4000.0, -4000.0), (19, 2, 0.0, 2000.0)])
    prices = _prices({1: 0.10, 19: 0.40, 20: 0.40})
    f = day_finance(rows, prices, day="2026-06-28", degradation_eur_per_kwh=0.05)
    assert f.saved_eur is not None
    assert abs(f.saved_eur - 1.00) < 1e-9
    assert f.solar_self_use_eur is not None and f.avoided_expensive_eur is not None
    assert f.battery_contribution_eur is not None
    assert abs(f.solar_self_use_eur) < 1e-9
    assert abs(f.avoided_expensive_eur - 1.00) < 1e-6
    assert abs(
        f.solar_self_use_eur + f.avoided_expensive_eur + f.battery_contribution_eur - f.saved_eur
    ) < 1e-6


def test_solar_self_use_breakdown_when_battery_stores_surplus():
    # Midday: solar surplus charges the battery (grid ~0). Evening: battery covers load at a
    # higher import price while export credit at charge time was lower (spot_minus_tax) so the
    # solar-shift is worth real money — attributed to "your own solar used".
    rows = []
    # 12:00–13:00: solar 3000 W, load 1000 W, battery charge 2000 W, grid 0.
    t0 = DAY + timedelta(hours=12)
    for i in range(4):
        ts = (t0 + timedelta(minutes=15 * i)).isoformat()
        rows.append({"ts": ts, "grid_power_w": 0.0, "solar_power_w": 3000.0,
                     "battery_power_w": -2000.0})
    # 19:00–20:00: no solar, battery discharges 2000 W into the load, grid 0.
    t1 = DAY + timedelta(hours=19)
    for i in range(4):
        ts = (t1 + timedelta(minutes=15 * i)).isoformat()
        rows.append({"ts": ts, "grid_power_w": 0.0, "solar_power_w": 0.0,
                     "battery_power_w": 2000.0})
    prices = _prices({12: 0.20, 19: 0.40})
    f = day_finance(
        rows, prices, day="2026-06-28", degradation_eur_per_kwh=0.05,
        export_price_model="spot_minus_tax", energy_tax_eur_per_kwh=0.13,
    )
    assert f.saved_eur is not None and f.solar_self_use_eur is not None
    assert f.solar_self_use_eur > 0.0
    assert abs(
        f.solar_self_use_eur + f.avoided_expensive_eur + f.battery_contribution_eur - f.saved_eur
    ) < 1e-6


def _dst_day_rows(day, grid_w: float, price: float = 0.20):
    """One sample + price slot per local quarter of a Europe/Amsterdam calendar day (92/96/100)."""
    start = datetime(day.year, day.month, day.day, tzinfo=AMS)
    end = start + timedelta(days=1)
    utc0, utc1 = start.astimezone(UTC), end.astimezone(UTC)
    rows, prices = [], []
    t = utc0
    while t < utc1:
        rows.append({"ts": t.isoformat(), "grid_power_w": grid_w,
                     "battery_power_w": 0.0, "solar_power_w": 0.0})
        prices.append({"start_ts": t.isoformat(), "eur_per_kwh": price})
        t += timedelta(minutes=15)
    return rows, prices, start, end


def test_dst_spring_forward_day_kwh_sums_92_quarters():
    # 2026-03-29 Europe/Amsterdam: clocks jump 02→03 → 23 h = 92 quarters → 23 kWh at 1 kW.
    from datetime import date
    day = date(2026, 3, 29)
    rows, prices, start, end = _dst_day_rows(day, 1000.0)
    assert len(rows) == 92
    f = day_finance(
        rows, prices, day=day.isoformat(),
        window_start=start, window_end=end, sample_interval_seconds=900.0,
    )
    assert abs(f.grid_import_kwh - 23.0) < 1e-6
    assert abs(f.sample_coverage - 1.0) < 1e-9
    assert abs(f.price_coverage - 1.0) < 1e-9
    assert abs(f.grid_cost_eur - 23.0 * 0.20) < 1e-6


def test_dst_fall_back_day_kwh_sums_100_quarters():
    # 2026-10-25 Europe/Amsterdam: clocks repeat 02→03 → 25 h = 100 quarters → 25 kWh at 1 kW.
    from datetime import date
    day = date(2026, 10, 25)
    rows, prices, start, end = _dst_day_rows(day, 1000.0)
    assert len(rows) == 100
    f = day_finance(
        rows, prices, day=day.isoformat(),
        window_start=start, window_end=end, sample_interval_seconds=900.0,
    )
    assert abs(f.grid_import_kwh - 25.0) < 1e-6
    assert abs(f.sample_coverage - 1.0) < 1e-9
    assert abs(f.grid_cost_eur - 25.0 * 0.20) < 1e-6


# --- #131: EMS vs battery AUTO (per-request; never bumps calc_v / never overwrites without raw) ---

from ems.finance import VS_AUTO_MODEL_NOTE, day_vs_auto, strip_vs_auto_ephemeral


def test_finance_calc_version_unchanged_by_vs_auto_slice():
    # AC #131: this slice must NOT bump `_FINANCE_CALC_VERSION` (simulation is per-request only).
    assert _FINANCE_CALC_VERSION == 6


def test_day_vs_auto_solar_day_auto_beats_idle_battery():
    # Midday surplus charges AUTO; evening load is served from the pack. Measured day left the
    # battery idle → EMS (idle) costs more than AUTO → saved_vs_auto is negative and stays visible.
    rows = []
    for h, grid, solar, batt in (
        (10, 0.0, 2000.0, 0.0),   # surplus exported (idle battery)
        (11, 0.0, 2000.0, 0.0),
        (12, 0.0, 2000.0, 0.0),
        (19, 1000.0, 0.0, 0.0),  # evening import
        (20, 1000.0, 0.0, 0.0),
    ):
        t0 = DAY + timedelta(hours=h)
        for i in range(4):
            rows.append({
                "ts": (t0 + timedelta(minutes=15 * i)).isoformat(),
                "grid_power_w": grid, "solar_power_w": solar, "battery_power_w": batt,
                "soc_pct": 50.0,
            })
    prices = _prices({10: 0.20, 11: 0.20, 12: 0.20, 19: 0.40, 20: 0.40})
    vs = day_vs_auto(
        rows, prices, day="2026-06-28",
        usable_kwh=10.0, max_charge_w=4000.0, max_discharge_w=4000.0,
        min_reserve_soc=0.0, degradation_eur_per_kwh=0.05,
    )
    assert vs.has_sim and vs.saved_vs_auto_eur is not None
    assert vs.saved_vs_auto_eur < 0.0  # AUTO would have been cheaper → EMS "saved" is negative
    assert "0.90" in VS_AUTO_MODEL_NOTE and "50 W" in VS_AUTO_MODEL_NOTE
    assert "EV" in VS_AUTO_MODEL_NOTE or "car" in VS_AUTO_MODEL_NOTE.lower()


def test_day_vs_auto_prices_import_fee_like_day_finance_not_replay_spot():
    # AC #131: same EconomicSnapshot / import-fee boundary as day_finance — a 0.05 €/kWh import
    # fee raises both legs equally when the meter is identical to AUTO (idle → AUTO idle too).
    rows = _rows([(12, 1, 1000.0, 0.0)])
    # Give SoC so AUTO starts full enough; with zero solar and flat load AUTO discharges → different.
    # Use zero load + zero solar + idle battery so AUTO also idles (net=0).
    idle = []
    t0 = DAY + timedelta(hours=12)
    for i in range(4):
        idle.append({
            "ts": (t0 + timedelta(minutes=15 * i)).isoformat(),
            "grid_power_w": 0.0, "solar_power_w": 0.0, "battery_power_w": 0.0, "soc_pct": 50.0,
        })
    prices = _prices({12: 0.20})
    vs_fee = day_vs_auto(
        idle, prices, day="2026-06-28", min_reserve_soc=0.0,
        tibber_total_includes_all=False, import_fee_eur_per_kwh=0.05,
    )
    vs_spot = day_vs_auto(
        idle, prices, day="2026-06-28", min_reserve_soc=0.0,
        tibber_total_includes_all=False, import_fee_eur_per_kwh=0.0,
    )
    assert vs_fee.has_sim and vs_spot.has_sim
    # Idle both worlds → saved ≈ 0 either way; fee path still builds snapshots (no crash).
    assert abs(vs_fee.saved_vs_auto_eur) < 1e-6
    assert abs(vs_spot.saved_vs_auto_eur) < 1e-6
    # Non-idle import day: fee increases actual AND auto grid cost (replay would miss this).
    vs_imp = day_vs_auto(
        rows, prices, day="2026-06-28", min_reserve_soc=100.0,  # reserve blocks AUTO discharge
        tibber_total_includes_all=False, import_fee_eur_per_kwh=0.05,
        degradation_eur_per_kwh=0.0,
    )
    vs_imp0 = day_vs_auto(
        rows, prices, day="2026-06-28", min_reserve_soc=100.0,
        tibber_total_includes_all=False, import_fee_eur_per_kwh=0.0,
        degradation_eur_per_kwh=0.0,
    )
    assert vs_imp.actual_cost_eur is not None and vs_imp0.actual_cost_eur is not None
    assert vs_imp.actual_cost_eur > vs_imp0.actual_cost_eur  # import fee visible on finance path


def test_strip_vs_auto_ephemeral_removes_simulation_keys_only():
    row = {
        "day": "2026-06-28", "has_data": True, "saved_eur": 1.0, "calc_v": 6,
        "saved_vs_auto_eur": 0.5, "auto_cost_eur": 2.0, "vs_auto_has_sim": True,
        "auto_grid_cost_eur": 1.8, "auto_battery_cost_eur": 0.2,
    }
    stripped = strip_vs_auto_ephemeral(row)
    assert stripped == {"day": "2026-06-28", "has_data": True, "saved_eur": 1.0, "calc_v": 6}
    assert "saved_vs_auto_eur" not in stripped


def test_cached_finance_without_raw_is_never_overwritten_with_empty(tmp_path):
    # AC #131: filled daily_finance + no raw_samples → after a finance window pass the stored row
    # is byte-for-byte equal and never becomes has_data=False / "geen data".
    db = str(tmp_path / "ems.sqlite")
    sentinel = {
        "day": "2026-06-28", "has_data": True, "saved_eur": 12.34,
        "grid_cost_eur": 5.0, "battery_cost_eur": 0.5, "baseline_cost_eur": 18.0,
        "price_coverage": 1.0, "sample_coverage": 1.0,
        "grid_import_kwh": 10.0, "grid_export_kwh": 2.0,
        "battery_charge_kwh": 4.0, "battery_discharge_kwh": 3.0,
        "solar_self_use_eur": 4.0, "avoided_expensive_eur": 7.0, "battery_contribution_eur": 1.34,
        "calc_v": _FINANCE_CALC_VERSION,
    }

    async def seed() -> None:
        store = HistoryStore(db)
        await store.init()
        # Prices alone (no raw) — enough for the window to exist; finance must trust the cache.
        await store.upsert_price_slots([("2026-06-28T12:00:00+00:00", 0.30)])
        await store.upsert_daily_finance("2026-06-28", dict(sentinel))
        await store.close()

    asyncio.run(seed())

    with TestClient(_econ_app(db)) as c:
        body = c.get("/api/finance?period=day&date=2026-06-28").json()
    assert body["days"][0]["saved_eur"] == 12.34
    assert body["days"][0]["has_data"] is True

    async def stored() -> dict:
        store = HistoryStore(db)
        rows = await store.daily_finance_between("2026-06-28", "2026-06-29")
        await store.close()
        return rows[0]["data"]

    after = asyncio.run(stored())
    assert after == sentinel  # byte-for-byte — no empty overwrite, no vs_auto keys persisted
    assert after.get("has_data") is True
    assert "saved_vs_auto_eur" not in after
