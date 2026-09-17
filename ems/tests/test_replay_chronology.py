from datetime import timedelta

import pytest

import ems.replay as replay
from ems.tests.test_replay import DAY, _cfg, _fc_day, _price_day, _raw_day


def test_replans_without_future_load_and_filters_late_forecast(monkeypatch):
    calls = []
    original = replay.build_plan

    def observe(*args, **kwargs):
        calls.append(kwargs)
        return original(*args, **kwargs)

    monkeypatch.setattr(replay, "build_plan", observe)
    raw = _raw_day(9876, lambda _: 0)
    forecast = _fc_day(lambda _: 9000)
    for row in forecast:
        row["issued_at"] = (DAY + timedelta(days=1)).isoformat()
    replay.replay_day(raw, _price_day(lambda _: 0.25), forecast, cfg=_cfg())
    assert len(calls) >= 192
    first = calls[0]
    assert first["load_w_by"][DAY] != 9876
    assert not first["forecast"]


def test_wear_and_inventory_adjustment_do_not_count_depletion_as_free_saving():
    raw = _raw_day(500, lambda _: 0, soc=100)
    result = replay.replay_day(raw, _price_day(lambda _: 0.20), [], cfg=_cfg())
    auto = result.scenarios["auto_selfuse"]
    assert auto.initial_stored_kwh == pytest.approx(10)
    assert auto.final_stored_kwh == pytest.approx(0)
    assert auto.estimated_wear_eur == pytest.approx(auto.cycles_kwh * 0.05)
    assert auto.inventory_adjustment_eur == pytest.approx(2)
    assert auto.net_cost_eur == pytest.approx(auto.cost_eur + 2 + auto.estimated_wear_eur)


def test_scenario_state_continues_and_incomplete_day_resets_it():
    states = {}
    first = replay.replay_day(
        _raw_day(0, lambda _: 0, soc=80),
        _price_day(lambda _: 0.2),
        [],
        cfg=_cfg(),
        state_box=states,
    )
    next_day = DAY + timedelta(days=1)
    second = replay.replay_day(
        _raw_day(0, lambda _: 0, soc=10, day=next_day),
        _price_day(lambda _: 0.2, day=next_day),
        [],
        cfg=_cfg(),
        state_box=states,
    )
    assert first.scenarios["auto_selfuse"].final_stored_kwh == 8
    assert second.scenarios["auto_selfuse"].initial_stored_kwh == 8
    assert second.continuity == "continued"
    missing_day = next_day + timedelta(days=1)
    skipped = replay.replay_day(
        _raw_day(0, lambda _: 0, day=missing_day)[:-1],
        _price_day(lambda _: 0.2, day=missing_day),
        [],
        cfg=_cfg(),
        state_box=states,
    )
    assert not skipped.data_ok
    fourth_day = missing_day + timedelta(days=1)
    fourth = replay.replay_day(
        _raw_day(0, lambda _: 0, soc=10, day=fourth_day),
        _price_day(lambda _: 0.2, day=fourth_day),
        [],
        cfg=_cfg(),
        state_box=states,
    )
    assert fourth.scenarios["auto_selfuse"].initial_stored_kwh == 1
    assert fourth.continuity == "reset_after_gap"


def test_aggregate_and_api_label_grid_bill_and_net_economics_separately():
    from ems.web.routes.whatif import build_counterfactual, build_whatif

    result = replay.replay_day(
        _raw_day(500, lambda _: 0, soc=100), _price_day(lambda _: 0.2), [], cfg=_cfg()
    )
    aggregate = replay._aggregate([result], [result])
    auto = result.scenarios["auto_selfuse"]
    assert aggregate["auto_net_cost_eur"] == pytest.approx(auto.net_cost_eur)
    assert aggregate["cfg_b"]["net_delta_vs_a_eur"] == 0
    ranged = replay.RangeResult([result], aggregate, [result])
    payload = build_counterfactual(ranged, 1)
    assert payload["simulation"] is True
    assert "simulated" in payload["note"].lower()
    assert "measured day" not in payload["note"].lower()
    assert payload["scenarios"]["auto_selfuse"]["estimated_wear_eur"] > 0
    assert payload["deltas"]["planner_vs_auto_net_eur"] == aggregate["planner_vs_auto_net_eur"]
    whatif = build_whatif(ranged, {}, 1)
    assert whatif["net_delta_eur"] == 0
    assert "simulated" in whatif["note"].lower()


def test_range_carries_each_scenario_and_reads_issue_time_ledger(tmp_path, monkeypatch):
    import asyncio

    from ems.domain import RawSample
    from ems.load_model import reconstruct
    from ems.storage.history import HistoryStore

    db = str(tmp_path / "replay.sqlite")

    async def seed():
        store = HistoryStore(db)
        await store.init()
        for d in range(2):
            day = DAY + timedelta(days=d)
            for row in _raw_day(0, lambda _: 0, soc=80 if d == 0 else 10, day=day):
                raw = RawSample(
                    grid_power_w=0,
                    solar_power_w=0,
                    battery_power_w=0,
                    ev_power_w=0,
                    soc_pct=row["soc_pct"],
                )
                await store.record(row["ts"], raw, reconstruct(raw))
            await store.upsert_price_slots(
                [(r["start_ts"], r["eur_per_kwh"]) for r in _price_day(lambda _: 0.2, day=day)]
            )

        await store.ledger_append(
            [
                (
                    (DAY - timedelta(hours=1)).isoformat(),
                    "solar",
                    (DAY + timedelta(hours=12)).isoformat(),
                    321.0,
                    321.0,
                    321.0,
                    "test",
                    "1",
                    "complete",
                    1,
                )
            ]
        )

    asyncio.run(seed())
    calls = []
    original = replay.replay_day

    def observe(*args, **kwargs):
        calls.append({**kwargs, "recorded_forecast": args[2]})
        return original(*args, **kwargs)

    monkeypatch.setattr(replay, "replay_day", observe)
    result = replay.replay_range(db, 2, _cfg())
    assert result.days[1].scenarios["auto_selfuse"].initial_stored_kwh == 8
    assert calls[1]["history_rows"]
    assert any(f.get("expected_w") == 321 for f in calls[0]["recorded_forecast"])
    assert result.aggregate["continuity_resets"] == 0


def test_forecast_latest_available_revision_and_legacy_dates_are_conservative():
    target = DAY + timedelta(hours=12)
    rows = [
        {
            "issued_at": (DAY - timedelta(hours=1)).isoformat(),
            "target_start": target.isoformat(),
            "low_w": 100,
            "expected_w": 100,
            "high_w": 100,
        },
        {
            "issued_at": (DAY + timedelta(hours=1)).isoformat(),
            "target_start": target.isoformat(),
            "low_w": 900,
            "expected_w": 900,
            "high_w": 900,
        },
        {
            "issued_at": DAY.isoformat(),
            "source": "legacy_snapshot",
            "target_start": target.isoformat(),
            "low_w": 700,
            "expected_w": 700,
            "high_w": 700,
        },
    ]
    assert replay._eligible_forecast(rows, DAY)[0].p50_w == 100
    assert replay._eligible_forecast(rows, DAY + timedelta(hours=2))[0].p50_w == 900


def test_replay_tariff_periods_and_gaps_are_not_silently_legacy_prices():
    period = {
        "start_date": "2026-01-15",
        "end_date": "2026-01-16",
        "import_tax_eur_per_kwh": 0.12,
        "import_surcharge_eur_per_kwh": 0.03,
        "raw_includes_import_components": False,
        "export_tax_eur_per_kwh": 0.0,
        "export_surcharge_eur_per_kwh": 0.0,
        "export_fee_eur_per_kwh": 0.0,
    }
    cfg = _cfg(**{"tariffs.periods": [period]})
    first = replay.replay_day(_raw_day(1000, lambda _: 0), _price_day(lambda _: 0.1), [], cfg=cfg)
    assert first.scenarios["no_battery"].cost_eur == pytest.approx(24 * 0.25)
    tomorrow = DAY + timedelta(days=1)
    second = replay.replay_day(
        _raw_day(1000, lambda _: 0, day=tomorrow),
        _price_day(lambda _: 0.1, day=tomorrow),
        [],
        cfg=cfg,
    )
    assert not second.data_ok and "tariff" in second.skip_reason


def test_initial_soc_uses_first_observation_not_future_quarter_hour_average():
    raw = _raw_day(0, lambda _: 0, soc=10)
    raw.append({**raw[0], "ts": (DAY + timedelta(minutes=10)).isoformat(), "soc_pct": 90})
    day = replay.replay_day(raw, _price_day(lambda _: 0.2), [], cfg=_cfg())
    assert day.scenarios["auto_selfuse"].initial_stored_kwh == 1


def test_continuing_scenario_needs_no_new_observed_soc():
    state = {}
    replay.replay_day(
        _raw_day(0, lambda _: 0, soc=80), _price_day(lambda _: 0.2), [], cfg=_cfg(), state_box=state
    )
    tomorrow = DAY + timedelta(days=1)
    raw = _raw_day(0, lambda _: 0, soc=None, day=tomorrow)
    day = replay.replay_day(
        raw, _price_day(lambda _: 0.2, day=tomorrow), [], cfg=_cfg(), state_box=state
    )
    assert day.data_ok
    assert day.scenarios["auto_selfuse"].initial_stored_kwh == 8


def test_raw_load_learning_uses_the_same_household_basis_as_derived_history():
    row = {
        "ts": DAY.isoformat(),
        "grid_power_w": 7000,
        "solar_power_w": 0,
        "battery_power_w": 0,
        "ev_power_w": 6500,
    }
    assert replay._load_history([row], DAY + timedelta(minutes=15))[0]["non_ev_load_w"] == 500


def test_missing_or_nonfinite_forecast_values_are_not_invented_as_zero():
    rows = [
        {
            "issued_at": DAY.isoformat(),
            "target_start": DAY.isoformat(),
            "expected_w": value,
            "low_w": 0,
            "high_w": 0,
        }
        for value in (None, float("nan"))
    ]
    assert replay._eligible_forecast(rows, DAY) == []
