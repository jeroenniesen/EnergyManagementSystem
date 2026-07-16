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
    return create_app(
        MockSource(), dry_run=True, dev_mode="mock", tz=AMS,
        store=HistoryStore(db), settings_store=SettingsStore(db),
        price_source=MockPriceSource(AMS),
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
        # backup disabled (keep=0) so the maintenance loop doesn't clutter tmp with snapshots.
        return create_app(
            MockSource(), dry_run=True, dev_mode="mock", tz=AMS,
            store=store, settings_store=SettingsStore(db),
            price_source=MockPriceSource(AMS), history_backup_keep=0)

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
