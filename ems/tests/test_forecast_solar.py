from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from ems.sources.forecast_solar import ForecastSolarSource, parse_watts

AMS = ZoneInfo("Europe/Amsterdam")
NOON_UTC = datetime(2026, 6, 28, 12, 0, tzinfo=UTC)
SAMPLE = {"result": {"watts": {
    "2026-06-28 06:00:00": 0,
    "2026-06-28 09:00:00": 1200,
    "2026-06-28 13:00:00": 2800,
    "2026-06-28 18:00:00": 400,
    "2026-06-28 21:00:00": 0,
}}}


def _src(**kw):
    base = dict(tz=AMS, lat=52.13, lon=5.29, tilt=35.0, azimuth=0.0, kwp=3.0,
                clock=lambda: NOON_UTC)
    base.update(kw)
    return ForecastSolarSource(**base)


def test_parse_watts_resamples_to_15min_grid():
    midnight = datetime(2026, 6, 28, 0, 0, tzinfo=AMS)
    slots = parse_watts(SAMPLE, AMS, midnight, 96)
    assert len(slots) == 96
    assert slots[0].p50_w == 0  # 00:00, before sunrise
    s10 = next(s for s in slots if s.start.hour == 10 and s.start.minute == 0)
    assert s10.p50_w == 1200  # stepwise hold from the 09:00 sample
    s13 = next(s for s in slots if s.start.hour == 13 and s.start.minute == 0)
    assert s13.p50_w == 2800
    assert s13.p10_w == 0.6 * 2800 and s13.p90_w == 1.15 * 2800  # derived bands


def test_url_format_matches_forecast_solar():
    assert _src(http_get=lambda u: SAMPLE).url == (
        "https://api.forecast.solar/estimate/52.13/5.29/35/0/3"
    )


def test_uses_injected_get_and_caches():
    calls = {"n": 0}

    def fake(_url):
        calls["n"] += 1
        return SAMPLE

    src = _src(http_get=fake)
    src.slots()
    src.slots()  # within TTL -> served from cache, no second fetch (respects rate limit)
    assert calls["n"] == 1
    assert src.source_label == "forecast.solar"


def test_falls_back_to_model_on_error():
    def boom(_url):
        raise OSError("rate limited")

    src = _src(http_get=boom)
    slots = src.slots()
    assert src.source_label == "model (fallback)"
    assert any(s.p50_w > 0 for s in slots)  # the model still yields a daytime curve


def test_falls_back_when_watts_empty():
    src = _src(http_get=lambda _u: {"result": {"watts": {}}})  # empty = API gave nothing
    src.slots()
    assert src.source_label == "model (fallback)"


def test_zero_production_response_is_kept_not_fallback():
    # A non-empty response that honestly maps to 0 W (night/winter) is a VALID live answer —
    # it must NOT be replaced by the model (which would shadow-ban the live source).
    src = _src(http_get=lambda _u: {"result": {"watts": {
        "2026-06-28 03:00:00": 0, "2026-06-28 12:00:00": 0,
    }}})
    slots = src.slots()
    assert src.source_label == "forecast.solar"
    assert all(s.p50_w == 0 for s in slots)
