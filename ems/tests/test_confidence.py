"""Plan confidence score (B-68): pure rule-matrix tests over ems.confidence.plan_confidence."""
from __future__ import annotations

from ems.confidence import plan_confidence

# A forecast_skill dict with plenty of evidence and a well-calibrated forecast — used as the
# "nothing wrong with the forecast" baseline in tests that aren't exercising the forecast rules.
_GOOD_SKILL = {
    "n_slots": 200, "bias_w": 10.0, "mae_w": 50.0, "band_coverage_pct": 92.0,
    "actual_solar_kwh": 40.0, "forecast_p50_kwh": 42.0,
}


def _confidence(**overrides):
    kwargs = {
        "data_quality": "complete",
        "forecast_skill": _GOOD_SKILL,
        "freshness_ok": True,
        "battery_reachable": True,
    }
    kwargs.update(overrides)
    return plan_confidence(**kwargs)


def test_everything_fine_is_high_confidence():
    out = _confidence()
    assert out["level"] == "high"
    assert out["reasons"]
    assert "fresh data" in out["reasons"][0].lower()


def test_unsafe_data_quality_is_low():
    out = _confidence(data_quality="unsafe")
    assert out["level"] == "low"
    assert "safety fallback" in out["reasons"][0].lower()


def test_unsafe_wins_even_with_good_forecast_and_reachable_battery():
    out = _confidence(data_quality="unsafe", forecast_skill=_GOOD_SKILL,
                      freshness_ok=True, battery_reachable=True)
    assert out["level"] == "low"
    assert out["reasons"] == [out["reasons"][0]]  # only the unsafe reason applies here
    assert "safety fallback" in out["reasons"][0].lower()


def test_battery_unreachable_is_low():
    out = _confidence(battery_reachable=False)
    assert out["level"] == "low"
    assert "battery isn't answering" in out["reasons"][0].lower() or \
        "battery" in out["reasons"][0].lower()


def test_stale_freshness_is_low():
    out = _confidence(freshness_ok=False)
    assert out["level"] == "low"
    assert "stale" in out["reasons"][0].lower()


def test_degraded_data_quality_caps_at_medium():
    out = _confidence(data_quality="degraded")
    assert out["level"] == "medium"
    assert "stale" in out["reasons"][0].lower()


def test_price_fallback_caps_at_medium_and_names_price():
    out = _confidence(data_quality="price_fallback")
    assert out["level"] == "medium"
    assert "price" in out["reasons"][0].lower()


def test_unmapped_data_quality_value_still_caps_at_medium_not_high():
    # Forward-compat: SPEC lists 'forecast_only' as a future badge state this module doesn't (yet)
    # special-case by name — any value other than 'complete'/'unsafe' must still cap at medium.
    out = _confidence(data_quality="something_new")
    assert out["level"] == "medium"
    assert "something_new" in out["reasons"][0]


def test_forecast_only_names_the_missing_price_signal():
    out = _confidence(data_quality="forecast_only")
    assert out["level"] == "medium"
    assert "forecast" in out["reasons"][0].lower()


def test_missing_forecast_skill_caps_at_medium_as_no_evidence_yet():
    # n_slots == 0 (or no skill dict at all) must NEVER render "0.0 days" (production finding) —
    # it reads as the honest "no evidence yet" sentence instead.
    out = _confidence(forecast_skill=None)
    assert out["level"] == "medium"
    assert "no forecast evidence yet" in out["reasons"][0].lower()
    assert "0.0" not in out["reasons"][0]


def test_zero_matched_slots_reads_as_no_evidence_yet():
    zero = {**_GOOD_SKILL, "n_slots": 0}
    out = _confidence(forecast_skill=zero)
    assert out["level"] == "medium"
    assert "no forecast evidence yet" in out["reasons"][0].lower()


def test_thin_forecast_evidence_caps_at_medium():
    # 20 matched daytime slots ≈ 20/48 = 0.42 of a sunny day → "about half a day".
    thin = {**_GOOD_SKILL, "n_slots": 20}
    out = _confidence(forecast_skill=thin)
    assert out["level"] == "medium"
    assert "still learning your roof" in out["reasons"][0].lower()
    assert "about half a day" in out["reasons"][0].lower()


def test_almost_enough_evidence_reads_about_one_day():
    # 47/48 = 0.98 → rounds to "about 1 day" (singular, no float).
    thin = {**_GOOD_SKILL, "n_slots": 47}
    out = _confidence(forecast_skill=thin)
    assert out["level"] == "medium"
    assert "about 1 day" in out["reasons"][0].lower()
    assert "days" not in out["reasons"][0].split("about 1 day")[1][:1]


def test_forecast_bias_beyond_threshold_caps_at_medium():
    # mean p50 = 42 kWh * 1000 / (200 * 0.25h) = 840 W; 25% of that is 210 W; bias 300 W exceeds it.
    hot = {**_GOOD_SKILL, "bias_w": 300.0}
    out = _confidence(forecast_skill=hot)
    assert out["level"] == "medium"
    assert "hot" in out["reasons"][0].lower() or "cold" in out["reasons"][0].lower()


def test_forecast_bias_within_threshold_does_not_cap():
    # 10 W bias on an 840 W mean p50 is well inside the 25% band — should not cap.
    out = _confidence(forecast_skill=_GOOD_SKILL)
    assert out["level"] == "high"


def test_thin_band_coverage_caps_at_medium_even_with_low_bias():
    thin_band = {**_GOOD_SKILL, "band_coverage_pct": 40.0}
    out = _confidence(forecast_skill=thin_band)
    assert out["level"] == "medium"
    assert "hot" in out["reasons"][0].lower() or "cold" in out["reasons"][0].lower()


def test_reasons_are_capped_at_two_and_capping_reason_is_first():
    out = _confidence(data_quality="degraded", forecast_skill=None)
    assert out["level"] == "medium"
    assert len(out["reasons"]) == 2
    assert "stale" in out["reasons"][0].lower()  # data_quality bullet ordered before forecast one
    assert "no forecast evidence yet" in out["reasons"][1].lower()


def test_low_level_reasons_are_capped_at_two_with_unsafe_first():
    out = _confidence(data_quality="unsafe", battery_reachable=False, freshness_ok=False)
    assert out["level"] == "low"
    assert len(out["reasons"]) == 2
    assert "safety fallback" in out["reasons"][0].lower()


def test_reasons_never_empty():
    for dq in ("complete", "degraded", "price_fallback", "unsafe", "forecast_only", "??"):
        for skill in (None, _GOOD_SKILL):
            for fresh_ok in (True, False):
                for reachable in (True, False):
                    out = plan_confidence(
                        data_quality=dq, forecast_skill=skill,
                        freshness_ok=fresh_ok, battery_reachable=reachable,
                    )
                    assert out["reasons"]
                    assert out["level"] in {"high", "medium", "low"}
