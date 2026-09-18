from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from ems.bill_advice import appliance_window, consumption_advice, reserve_advice
from ems.calibration import battery_calibration, load_accuracy
from ems.planner.load_profile import LoadProfile
from ems.sources.forecast import ForecastSlot
from ems.sources.prices import PriceSlot

NOW = datetime(2026, 9, 14, tzinfo=UTC)
TZ = ZoneInfo("UTC")


def prices(values):
    return [PriceSlot(NOW + timedelta(minutes=15 * i), x) for i, x in enumerate(values)]


def test_appliance_uses_contiguous_cheapest_window_and_never_crosses_deadline():
    result = appliance_window(
        prices([0.1, 0.5, 0.2, 0.2]),
        [],
        {},
        now=NOW,
        deadline=NOW + timedelta(hours=1),
        duration_minutes=30,
        energy_kwh=1,
    )
    assert result["start"] == (NOW + timedelta(minutes=30)).isoformat()
    assert result["cost_eur"] == pytest.approx(0.2)
    assert result["saving_vs_now_eur"] == pytest.approx(0.1)


def test_appliance_values_surplus_at_forgone_export_and_rejects_gaps():
    p = prices([0.3] * 4)
    solar = [ForecastSlot(p[0].start, 2000, 2000, 2000)]
    result = appliance_window(
        p,
        solar,
        {},
        now=NOW,
        deadline=NOW + timedelta(hours=1),
        duration_minutes=15,
        energy_kwh=0.25,
        export_price=lambda _: 0.05,
    )
    assert result["start"] == NOW.isoformat()
    assert result["cost_eur"] == pytest.approx(0.0125)
    assert (
        appliance_window(
            [p[0], p[2]],
            [],
            {},
            now=NOW,
            deadline=NOW + timedelta(hours=1),
            duration_minutes=30,
            energy_kwh=1,
        )["available"]
        is False
    )


def test_reserve_is_advisory_honors_floor_and_returns_unknown_for_stale():
    p = prices([0.1, 0.1, 0.4, 0.4] + [0.1] * 92)
    profile = LoadProfile({0: 1000}, TZ)
    cfg = {
        "battery.usable_kwh": 10,
        "battery.min_reserve_soc": 20,
        "planner.round_trip_efficiency": 0.9,
    }
    result = reserve_advice(p, [], profile, now=NOW, settings=cfg, fresh=True)
    assert 20 <= result["target_soc_pct"] <= 100
    assert result["hard_floor_soc_pct"] == 20
    assert result["automatic"] is False
    assert reserve_advice(p, [], profile, now=NOW, settings=cfg, fresh=False)["available"] is False
    assert (
        reserve_advice(p[80:], [], profile, now=NOW, settings=cfg, fresh=True)["available"] is False
    )


def test_consumption_requires_comparable_days_and_detects_sustained_increase():
    rows = []
    for days in range(1, 22):
        for h in range(24):
            t = NOW - timedelta(days=days) + timedelta(hours=h)
            rows.append({"ts": t.isoformat(), "non_ev_load_w": 400 if days <= 7 else 200})
    result = consumption_advice(rows, now=NOW, tz=TZ, price_eur_per_kwh=0.25)
    assert {r["kind"] for r in result} == {"night_baseload", "household_use"}
    assert all(r["extra_kwh_per_day"] > 0 for r in result)
    assert consumption_advice(rows[:3], now=NOW, tz=TZ, price_eur_per_kwh=0.25) == []


def test_calibration_needs_repeated_complete_contiguous_segments():
    assert battery_calibration([], configured_kwh=10)["available"] is False
    rows = []
    for cycle in range(3):
        start = NOW - timedelta(days=cycle + 1)
        for i in range(13):
            # 2kWh stored across 20 percentage points = 10kWh, AC input 2.222kWh.
            rows.append(
                {
                    "ts": (start + timedelta(minutes=i * 5)).isoformat(),
                    "battery_power_w": -2222.222,
                    "soc_pct": 20 + i * 20 / 12,
                }
            )
        for i in range(13):
            rows.append(
                {
                    "ts": (start + timedelta(hours=2, minutes=i * 5)).isoformat(),
                    "battery_power_w": 1800,
                    "soc_pct": 40 - i * 20 / 12,
                }
            )
    result = battery_calibration(rows, configured_kwh=10)
    assert result["available"] is True
    assert result["usable_kwh"] == pytest.approx(10, rel=0.01)
    assert result["round_trip_efficiency"] == pytest.approx(0.81, rel=0.01)
    assert result["standby_w"] is None
    for row in rows:
        if row["battery_power_w"] > 0:
            row["soc_pct"] += 50
    assert battery_calibration(rows, configured_kwh=10)["available"] is False


def test_load_accuracy_uses_held_out_days():
    rows = []
    for days in range(1, 43):
        for hour in range(24):
            t = NOW - timedelta(days=days) + timedelta(hours=hour)
            rows.append({"ts": t.isoformat(), "non_ev_load_w": 900 if t.weekday() >= 5 else 300})
    result = load_accuracy(rows, now=NOW, tz=TZ)
    assert result["hours_scored"] >= 24
    assert result["enhanced_mae_w"] < result["baseline_mae_w"]
    assert load_accuracy([], now=NOW, tz=TZ)["available"] is False


@pytest.mark.parametrize("energy", [float("nan"), float("inf"), -1])
def test_appliance_rejects_invalid_energy(energy):
    with pytest.raises(ValueError):
        appliance_window(
            prices([0.1] * 4),
            [],
            {},
            now=NOW,
            deadline=NOW + timedelta(hours=1),
            duration_minutes=30,
            energy_kwh=energy,
        )
