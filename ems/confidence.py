"""Plan confidence score (BACKLOG B-68): one high/medium/low label + plain-language reason(s) for
the CURRENT plan, synthesised ONLY from signals other pure functions in this codebase already
compute — no new measurement is taken here:

- `data_quality`: the per-plan data-quality badge (SPEC §8.11, `ems.alerts.data_quality`) —
  complete | degraded | forecast_only | price_fallback | unsafe.
- `forecast_skill`: the 14-day solar forecast skill (`ems.analysis.forecast_error`) — n_slots,
  bias_w, band_coverage_pct, forecast_p50_kwh — or None if there's no evidence gathered yet.
- `freshness_ok`: whether every currently-tracked signal is fresh (SPEC §4.7), independent of
  `data_quality` (which can read 'degraded' purely from a missing forecast SOURCE, with nothing
  actually stale).
- `battery_reachable`: whether the battery cluster answered this read window.

Deterministic, worst-component-wins: whichever input is least trustworthy sets the level, and its
reason leads the `reasons` list (capped at two, so the UI sub-line stays one or two short
sentences). Pure — no clock, no I/O — trivially unit-testable against a hand-built rule matrix.
"""
from __future__ import annotations

_SLOT_HOURS = 0.25  # 15-minute planner slots (CLAUDE.md "Planner granularity")
_SLOTS_PER_DAY = 24.0 / _SLOT_HOURS  # 96
_MIN_SKILL_SLOTS = 48  # mirrors ems.analysis._MIN_SLOTS — "a few days" of matched evidence
_MAX_BIAS_FRACTION = 0.25  # |bias_w| beyond 25% of mean forecast p50 reads as "running hot/cold"
_MIN_BAND_COVERAGE_PCT = 60.0

_LOW_UNSAFE = "Safety fallback active — EMS is holding, not planning."
_LOW_UNREACHABLE = "The battery isn't answering right now, so the plan can't be trusted."
_LOW_STALE = "Some live data is stale, so the plan can't be trusted right now."
_HIGH_REASON = (
    "Fresh data, calibrated forecast, battery responding — nothing is holding this plan back."
)

# Data-quality values that are not outright unsafe still cap confidence at medium — the reason
# names what's degraded. Any future/unmapped value (e.g. a data_quality label this module doesn't
# know about yet) falls back to a generic-but-honest reason rather than silently reading as high.
_DQ_REASONS: dict[str, str] = {
    "price_fallback": "Live prices are unavailable, so the plan is running on a fallback price "
                       "signal.",
    "degraded": "Some sensor or forecast data is stale, so plan quality is reduced.",
    "forecast_only": "There's no live price signal right now, so the plan is running on the "
                      "forecast alone.",
}


def _dq_reason(quality: str) -> str:
    return _DQ_REASONS.get(
        quality, f"Data quality is currently '{quality}', which limits plan confidence."
    )


def _learning_reason(forecast_skill: dict | None) -> str:
    n_slots = (forecast_skill or {}).get("n_slots") or 0
    days = n_slots / _SLOTS_PER_DAY
    return f"Still learning your roof — under {days:.1f} days of forecast evidence so far."


def _forecast_bias_flag(skill: dict) -> bool:
    """True if the 14-day solar forecast has been running hot/cold (skill already has >= 48
    matched slots, checked by the caller before this is reached)."""
    n_slots = skill.get("n_slots") or 0
    if n_slots <= 0:
        return False
    band_pct = skill.get("band_coverage_pct")
    if band_pct is not None and band_pct < _MIN_BAND_COVERAGE_PCT:
        return True
    bias_w = skill.get("bias_w")
    forecast_kwh = skill.get("forecast_p50_kwh")
    if bias_w is None or not forecast_kwh:
        return False
    mean_p50_w = forecast_kwh * 1000.0 / (n_slots * _SLOT_HOURS)
    if mean_p50_w <= 0:
        return False
    return abs(bias_w) > _MAX_BIAS_FRACTION * mean_p50_w


def plan_confidence(
    *,
    data_quality: str,
    forecast_skill: dict | None,
    freshness_ok: bool,
    battery_reachable: bool,
) -> dict:
    """High/medium/low confidence for the current plan + 1-2 plain-language reasons.

    Rules (worst-component-wins, checked in this order):
      1. data_quality == "unsafe"                              -> low
      2. battery unreachable, or some live signal is stale      -> low
      3. data_quality not "complete" (degraded/forecast_only/
         price_fallback/anything else unmapped)                -> at most medium
      4. forecast_skill missing or under the evidence minimum   -> at most medium
      5. forecast_skill shows a hot/cold bias or thin band
         coverage                                               -> at most medium
      6. nothing above triggered                                -> high

    Returns {"level": "high"|"medium"|"low", "reasons": [str, ...]} — reasons is never empty and
    never longer than two entries; the reason for the deciding (worst) rule always comes first.
    """
    low_reasons: list[str] = []
    if data_quality == "unsafe":
        low_reasons.append(_LOW_UNSAFE)
    if not battery_reachable:
        low_reasons.append(_LOW_UNREACHABLE)
    if not freshness_ok:
        low_reasons.append(_LOW_STALE)
    if low_reasons:
        return {"level": "low", "reasons": low_reasons[:2]}

    medium_reasons: list[str] = []
    if data_quality != "complete":
        medium_reasons.append(_dq_reason(data_quality))
    if forecast_skill is None or (forecast_skill.get("n_slots") or 0) < _MIN_SKILL_SLOTS:
        medium_reasons.append(_learning_reason(forecast_skill))
    elif _forecast_bias_flag(forecast_skill):
        medium_reasons.append("Solar forecasts have been running hot/cold lately, so sizing is "
                               "less precise.")
    if medium_reasons:
        return {"level": "medium", "reasons": medium_reasons[:2]}

    return {"level": "high", "reasons": [_HIGH_REASON]}
