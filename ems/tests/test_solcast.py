"""Unit tests for Solcast Hobbyist adapter (B-14) — canned payloads only, no live API."""
from __future__ import annotations

import json
import threading
import time
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from ems.sources.forecast import MockSolarForecastSource
from ems.sources.solcast import (
    SolcastBudgetLedger,
    SolcastSource,
    parse_forecasts,
    refresh_due,
)

AMS = ZoneInfo("Europe/Amsterdam")
# 12:00 local on a sunny midsummer day (= 10:00 UTC in CEST).
NOON_LOCAL = datetime(2026, 6, 28, 12, 0, tzinfo=AMS)
NOON_UTC = NOON_LOCAL.astimezone(UTC)

# Canned Solcast rooftop forecast: 30-min periods, real P10/P50/P90 in kW.
SAMPLE = {
    "forecasts": [
        {
            "pv_estimate": 0.0,
            "pv_estimate10": 0.0,
            "pv_estimate90": 0.0,
            "period_end": "2026-06-28T06:30:00.0000000Z",
            "period": "PT30M",
        },
        {
            "pv_estimate": 1.2,
            "pv_estimate10": 0.7,
            "pv_estimate90": 1.6,
            "period_end": "2026-06-28T10:30:00.0000000Z",
            "period": "PT30M",
        },
        {
            "pv_estimate": 2.8,
            "pv_estimate10": 1.9,
            "pv_estimate90": 3.4,
            "period_end": "2026-06-28T12:00:00.0000000Z",
            "period": "PT30M",
        },
        {
            "pv_estimate": 0.4,
            "pv_estimate10": 0.2,
            "pv_estimate90": 0.55,
            "period_end": "2026-06-28T18:00:00.0000000Z",
            "period": "PT30M",
        },
    ]
}


class _FakeCache:
    def __init__(self, preset: dict | None = None, age: float = 0.0) -> None:
        self.data = dict(preset or {})
        self.age = age
        self.sets: list[tuple] = []

    def get(self, key):
        return self.data.get(key)

    def get_with_age(self, key):
        return (self.data[key], self.age) if key in self.data else None

    def set(self, key, value, ttl_seconds):
        self.data[key] = value
        self.sets.append((key, value, ttl_seconds))


def _src(**kw):
    base = dict(
        tz=AMS,
        api_key="tok-test",
        resource_id="res-abc",
        clock=lambda: NOON_UTC,
        refresh_times=("07:00", "09:00", "11:00", "13:00", "15:00", "17:00", "19:00"),
        daily_budget=10,
        horizon_slots=96,
        fallback=MockSolarForecastSource(AMS, clock=lambda: NOON_UTC, horizon_slots=96),
    )
    base.update(kw)
    return SolcastSource(**base)


def test_parse_forecasts_resamples_30min_to_15min_with_real_percentiles():
    midnight = datetime(2026, 6, 28, 0, 0, tzinfo=AMS)
    slots = parse_forecasts(SAMPLE, AMS, midnight, 96)
    assert len(slots) == 96
    # period_end 10:30Z = 12:30 CEST; period is 12:00–12:30 local → covers 12:00 + 12:15.
    s12 = next(s for s in slots if s.start.hour == 12 and s.start.minute == 0)
    assert s12.p50_w == 1200  # 1.2 kW from the 10:30Z sample
    assert s12.p10_w == 700
    assert s12.p90_w == 1600
    assert s12.p10_w < s12.p50_w < s12.p90_w
    # Not the Forecast.Solar derived 0.6× / 1.15× bands.
    assert s12.p10_w != 0.6 * s12.p50_w
    assert s12.p90_w != 1.15 * s12.p50_w
    # period_end 12:00Z = 14:00 CEST → 13:30–14:00 local carries 2.8 kW.
    s1330 = next(s for s in slots if s.start.hour == 13 and s.start.minute == 30)
    assert s1330.p50_w == 2800
    assert s1330.p10_w == 1900
    assert s1330.p90_w == 3400
    # Before sunrise samples → 0.
    assert slots[0].p50_w == 0


def test_url_and_auth_header():
    calls: list[tuple[str, dict]] = []

    def fake(url, headers):
        calls.append((url, headers))
        return SAMPLE

    src = _src(http_get=fake)
    src.slots()
    assert calls[0][0] == (
        "https://api.solcast.com.au/rooftop_sites/res-abc/forecasts?format=json"
    )
    assert calls[0][1]["Authorization"] == "Bearer tok-test"
    assert src.source_label == "solcast"
    assert src.provider == "solcast"


def test_refresh_due_schedule_gate():
    times = ("07:00", "09:00", "11:00", "13:00")
    # Before first slot with overnight cache → not due.
    assert refresh_due(
        datetime(2026, 6, 28, 6, 30, tzinfo=AMS),
        datetime(2026, 6, 27, 19, 0, tzinfo=AMS),
        times,
    ) is False
    # At/after 07:00 with yesterday's fetch → due.
    assert refresh_due(
        datetime(2026, 6, 28, 7, 5, tzinfo=AMS),
        datetime(2026, 6, 27, 19, 0, tzinfo=AMS),
        times,
    ) is True
    # Already fetched at 07:00; next due at 09:00.
    assert refresh_due(
        datetime(2026, 6, 28, 8, 0, tzinfo=AMS),
        datetime(2026, 6, 28, 7, 0, tzinfo=AMS),
        times,
    ) is False
    assert refresh_due(
        datetime(2026, 6, 28, 9, 1, tzinfo=AMS),
        datetime(2026, 6, 28, 7, 0, tzinfo=AMS),
        times,
    ) is True
    # Cold cache always due.
    assert refresh_due(datetime(2026, 6, 28, 6, 0, tzinfo=AMS), None, times) is True


def test_caches_within_schedule_window():
    n = {"c": 0}

    def fake(_url, _headers):
        n["c"] += 1
        return SAMPLE

    src = _src(http_get=fake)
    src.slots()
    src.slots()  # same schedule window → no second call
    assert n["c"] == 1


def test_budget_ledger_refuses_past_cap_and_resets_at_local_midnight():
    cache = _FakeCache()
    clock = {"t": NOON_UTC}

    def now():
        return clock["t"]

    ledger = SolcastBudgetLedger(cache, tz=AMS, daily_budget=2, clock=now)
    assert ledger.can_call()
    assert ledger.record_call() == 1
    assert ledger.record_call() == 2
    assert not ledger.can_call()
    assert ledger.record_call() == 2  # capped

    # Local midnight rollover → budget resets.
    clock["t"] = NOON_UTC + timedelta(days=1)
    assert ledger.can_call()
    assert ledger.remaining() == 2


def test_budget_exhausted_serves_last_good_then_fallback():
    cache = _FakeCache()
    n = {"c": 0}

    def fake(_url, _headers):
        n["c"] += 1
        return SAMPLE

    # First call at 07:00 fills cache + spends 1.
    t0 = datetime(2026, 6, 28, 7, 0, tzinfo=AMS).astimezone(UTC)
    clock = {"t": t0}

    def now():
        return clock["t"]

    src = _src(
        http_get=fake,
        clock=now,
        daily_budget=1,
        cache_store=cache,
        budget_ledger=SolcastBudgetLedger(cache, tz=AMS, daily_budget=1, clock=now),
    )
    first = src.slots()
    assert n["c"] == 1
    assert any(s.p50_w == 2800 for s in first)

    # Next schedule window would refresh, but budget is gone → last-good Solcast.
    clock["t"] = datetime(2026, 6, 28, 9, 5, tzinfo=AMS).astimezone(UTC)
    second = src.slots()
    assert n["c"] == 1
    assert src.source_label == "solcast (budget held)"
    assert second == first


def test_fetch_error_falls_back_without_raising():
    def boom(_url, _headers):
        raise OSError("timeout")

    src = _src(http_get=boom)
    slots = src.slots()
    assert "fallback" in src.source_label
    assert any(s.p50_w > 0 for s in slots)  # model fallback still yields a curve


def test_empty_forecasts_falls_back():
    src = _src(http_get=lambda _u, _h: {"forecasts": []})
    src.slots()
    assert "fallback" in src.source_label


def test_single_flight_one_fetch_under_concurrent_miss():
    n = {"c": 0}
    barrier = threading.Barrier(8)

    def slow(_url, _headers):
        n["c"] += 1
        time.sleep(0.05)
        return SAMPLE

    src = _src(http_get=slow)
    out: list[int] = []

    def worker():
        barrier.wait()
        out.append(len(src.slots()))

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert n["c"] == 1
    assert all(x == 96 for x in out)


def test_warm_start_serves_snapshot_without_refetch():
    midnight = datetime(2026, 6, 28, 0, 0, tzinfo=AMS)
    slots = parse_forecasts(SAMPLE, AMS, midnight, 96)
    # Fetched at 11:00 local — noon call is still inside the same schedule window.
    fetched = datetime(2026, 6, 28, 11, 0, tzinfo=AMS)
    blob = json.dumps({
        "fetched_at": fetched.isoformat(),
        "slots": [{"s": s.start.isoformat(), "a": s.p10_w, "b": s.p50_w, "c": s.p90_w}
                  for s in slots],
    })
    cache = _FakeCache(preset={"solcast:slots": blob}, age=60.0)
    n = {"c": 0}

    def fake(_url, _headers):
        n["c"] += 1
        return SAMPLE

    src = _src(http_get=fake, cache_store=cache)
    assert len(src.slots()) == 96
    assert n["c"] == 0
    assert src.source_label == "solcast"


def test_missing_percentiles_collapse_to_p50():
    # period_end 10:00Z = 12:00 CEST → covers 11:30–12:00 local.
    data = {"forecasts": [{
        "pv_estimate": 1.5,
        "period_end": "2026-06-28T10:00:00Z",
        "period": "PT30M",
    }]}
    midnight = datetime(2026, 6, 28, 0, 0, tzinfo=AMS)
    slots = parse_forecasts(data, AMS, midnight, 96)
    s = next(s for s in slots if s.start.hour == 11 and s.start.minute == 45)
    assert s.p10_w == s.p50_w == s.p90_w == 1500.0
