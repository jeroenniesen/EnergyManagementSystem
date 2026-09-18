from datetime import UTC, datetime

import pytest

from ems import tariffs
from ems.finance import day_finance


def period(**changes):
    values = dict(
        start_date="2027-01-01",
        end_date="2028-01-01",
        raw_includes_import_components=True,
        import_tax_eur_per_kwh=0.13,
        import_surcharge_eur_per_kwh=0.02,
        export_tax_eur_per_kwh=0.0,
        export_surcharge_eur_per_kwh=0.0,
        export_fee_eur_per_kwh=0.01,
    )
    values.update(changes)
    return tariffs.TariffPeriod(**values)


def test_explicit_export_removes_tax_and_import_surcharge():
    p = period()
    value = p.normalize(0.25)
    assert value.import_eur_per_kwh == pytest.approx(0.25)
    assert value.export_eur_per_kwh == pytest.approx(0.09)
    assert p.normalize(-0.1).export_eur_per_kwh == pytest.approx(-0.26)


def test_period_validation_and_timezone_boundary():
    p = period()
    assert p.contains(datetime(2026, 12, 31, 23, tzinfo=UTC), "Europe/Amsterdam")
    assert not p.contains(datetime(2026, 12, 31, 22, 59, tzinfo=UTC), "Europe/Amsterdam")
    with pytest.raises(ValueError):
        tariffs.validate_periods([p, period()])
    for field in ("import_tax_eur_per_kwh", "export_surcharge_eur_per_kwh"):
        with pytest.raises(ValueError):
            period(**{field: float("nan")})
    with pytest.raises(ValueError):
        period(end_date="2026-12-31")


def test_finance_period_prices_and_missing_tariff_coverage():
    start = datetime(2027, 1, 1, tzinfo=UTC)
    rows = [{"ts": start.isoformat(), "grid_power_w": -1000, "battery_power_w": 0}]
    prices = [{"start_ts": start.isoformat(), "eur_per_kwh": 0.25}]
    result = day_finance(rows, prices, day="2027-01-01", tariff_periods=[period()])
    assert result.grid_cost_eur == pytest.approx(-0.0225)
    missing = day_finance(
        rows, prices, day="2027-01-01", tariff_periods=[period(start_date="2027-02-01")]
    )
    assert missing.grid_cost_eur is None
    assert missing.price_coverage == 0
    assert missing.grid_export_kwh == 0.25


def test_nonfinite_stored_prices_are_missing_not_nan():
    rows = [{"ts": "2027-01-01T00:00:00+00:00", "grid_power_w": 1000, "battery_power_w": 0}]
    result = day_finance(
        rows, [{"start_ts": rows[0]["ts"], "eur_per_kwh": float("nan")}], day="2027-01-01"
    )
    assert result.grid_cost_eur is None


def test_period_dates_must_be_canonical():
    with pytest.raises(ValueError):
        period(start_date="20270101")


def test_finance_crosses_local_boundary_and_reports_tariff_gap():
    rows = [
        {"ts": "2026-12-31T22:45:00+00:00", "grid_power_w": -1000, "battery_power_w": 0},
        {"ts": "2026-12-31T23:00:00+00:00", "grid_power_w": -1000, "battery_power_w": 0},
    ]
    prices = [{"start_ts": row["ts"], "eur_per_kwh": 0.25} for row in rows]
    result = day_finance(
        rows, prices, day="2027-01-01", tariff_periods=[period()], legacy_before="2027-01-01"
    )
    assert result.price_coverage == 1
    assert result.grid_cost_eur == pytest.approx(-0.25 * 0.25 - 0.25 * 0.09)
    partial = day_finance(rows, prices, day="2027-01-01", tariff_periods=[period()])
    assert partial.price_coverage == 0.5
    assert partial.grid_cost_eur == pytest.approx(-0.25 * 0.09)


def test_dst_invoice_coverage_uses_elapsed_time():
    from datetime import timedelta
    from zoneinfo import ZoneInfo

    from ems.finance import reconcile_invoice

    tz = ZoneInfo("Europe/Amsterdam")
    start = datetime(2027, 3, 28, tzinfo=tz).astimezone(UTC)
    end = datetime(2027, 3, 29, tzinfo=tz).astimezone(UTC)
    rows = [
        {
            "ts": (start + timedelta(minutes=i * 15)).isoformat(),
            "grid_power_w": 1000,
            "battery_power_w": 0,
        }
        for i in range(92)
    ]
    prices = [{"start_ts": row["ts"], "eur_per_kwh": 0.20} for row in rows]
    result = reconcile_invoice(
        rows,
        prices,
        start=start.astimezone(tz),
        end=end.astimezone(tz),
        invoice_eur=4.60,
        fixed_cost_eur=0,
        sample_interval_seconds=900,
    )
    assert result["complete"]
    assert result["grid_import_kwh"] == 23
    assert result["difference_eur"] == pytest.approx(0)


def test_shared_snapshot_resolves_frozen_legacy_and_explicit_gaps():
    from dataclasses import asdict

    from ems.tariff_history import economic_snapshot_at

    settings = {
        "tariffs.periods": [asdict(period())],
        "tariffs.legacy": {"export_price_model": "spot_minus_tax", "energy_tax_eur_per_kwh": 0.12},
    }
    current = economic_snapshot_at(settings, datetime(2027, 1, 1, tzinfo=UTC), 0.25)
    assert current.import_price_eur_per_kwh == pytest.approx(0.25)
    assert current.export_credit() == pytest.approx(0.09)
    legacy = economic_snapshot_at(settings, datetime(2026, 1, 1, tzinfo=UTC), 0.25)
    assert legacy.export_credit() == pytest.approx(0.13)
    assert economic_snapshot_at(settings, datetime(2028, 1, 1, tzinfo=UTC), 0.25) is None
