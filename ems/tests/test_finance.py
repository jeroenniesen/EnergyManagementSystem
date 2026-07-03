"""Daily finance math (spec 2026-07-03): grid cost, battery wear, and money saved vs the
no-battery baseline — pure, from canned raw rows + price slots. No hardware, no I/O."""
from datetime import UTC, datetime, timedelta

from ems.finance import day_finance

DAY = datetime(2026, 6, 28, 0, 0, tzinfo=UTC)


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


def test_export_is_credited_at_spot():
    # One hour exporting 1 kW of surplus solar (battery idle) at €0.20 → negative grid cost.
    rows = _rows([(12, 1, -1000.0, 0.0)])
    f = day_finance(rows, _prices({12: 0.20}), day="2026-06-28")
    assert abs(f.grid_export_kwh - 1.0) < 1e-9
    assert abs(f.grid_cost_eur - (-0.20)) < 1e-9
    assert abs(f.saved_eur - 0.0) < 1e-9  # battery did nothing → nothing saved


def test_no_price_history_is_honest():
    rows = _rows([(1, 1, 1000.0, 0.0)])
    f = day_finance(rows, [], day="2026-06-28")
    assert f.has_data
    assert f.price_coverage == 0.0
    assert f.grid_cost_eur is None and f.saved_eur is None and f.baseline_cost_eur is None
    assert abs(f.grid_import_kwh - 1.0) < 1e-9  # energy is still reported


def test_partial_price_coverage_counts_only_priced_slots():
    rows = _rows([(1, 1, 2000.0, 0.0), (2, 1, 2000.0, 0.0)])  # two hours importing 2 kW
    f = day_finance(rows, _prices({1: 0.25}), day="2026-06-28")
    assert abs(f.price_coverage - 0.5) < 1e-9
    assert abs(f.grid_cost_eur - 0.50) < 1e-9  # only the priced hour is costed
    assert abs(f.grid_import_kwh - 4.0) < 1e-9  # energy covers both hours


def test_empty_day():
    f = day_finance([], [], day="2026-06-28")
    assert not f.has_data
    assert f.grid_cost_eur is None and f.saved_eur is None
    assert f.grid_import_kwh == 0.0
    d = f.to_dict()
    assert d["day"] == "2026-06-28" and d["has_data"] is False
