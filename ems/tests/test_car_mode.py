"""Pure-core tests for the three car-charging battery behaviours (feat/car-charge-modes).

`ems.control.car_mode` is pure — canned observation rows, no DB, no clock, no hardware. It carries
the whole decision: what the battery should do while the car charges (hold / static discharge /
match the house load) and — crucially — WHETHER to (re-)command it this cycle (the bounded
re-command rule that keeps match-home-load a mode-switch, not a power-tracking loop).
"""
from datetime import UTC, datetime

from ems.control.car_mode import (
    CarModeAction,
    decide_car_mode_action,
    predict_house_load_w,
)

# 2026-07-15 is a Wednesday (weekday() == 2). All timestamps below are UTC-aware, so `now` and the
# observation slots bucket in the same frame (UTC) — exact-math holds.
WED_14 = datetime(2026, 7, 15, 14, 0, tzinfo=UTC)


def _obs(iso: str, non_ev_w: float) -> dict:
    """An observations-table row as the store returns it (slot_start UTC-ISO + non-EV mean)."""
    return {"slot_start": iso, "mean_non_ev_load_w": non_ev_w}


# --------------------------------------------------------------------------------------------------
# predict_house_load_w — the expected non-EV house load over the next ~2h
# --------------------------------------------------------------------------------------------------

def test_prediction_uses_same_weekday_hour_observations_over_the_profile():
    # Two prior-Wednesday observations inside the 2h window (14:00 and 15:00 buckets) -> mean 800 W,
    # NOT the profile's 400 W. A Tuesday decoy in the same clock-hour is a DIFFERENT weekday bucket
    # and must be excluded.
    rows = [
        _obs("2026-07-08T14:15:00+00:00", 700.0),  # prior Wed, 14:00 bucket
        _obs("2026-07-08T15:15:00+00:00", 900.0),  # prior Wed, 15:00 bucket
        _obs("2026-07-14T14:15:00+00:00", 5000.0),  # Tuesday — different weekday, excluded
    ]
    assert predict_house_load_w(rows, 400.0, now=WED_14) == 800.0


def test_prediction_falls_back_to_the_profile_when_no_matching_rows():
    # No same-weekday-hour history at all -> use the load profile's expected value (rounded to 50).
    rows = [_obs("2026-07-13T14:15:00+00:00", 5000.0)]  # a Monday, never matched
    assert predict_house_load_w(rows, 400.0, now=WED_14) == 400.0


def test_prediction_floors_at_150w():
    # A house is never 0 — an implausibly low mean is floored to 150 W.
    rows = [_obs("2026-07-08T14:15:00+00:00", 80.0)]
    assert predict_house_load_w(rows, 400.0, now=WED_14) == 150.0
    # Floor also applies to the profile fallback.
    assert predict_house_load_w([], 60.0, now=WED_14) == 150.0


def test_prediction_caps_at_3000w():
    rows = [_obs("2026-07-08T14:15:00+00:00", 4200.0)]
    assert predict_house_load_w(rows, 400.0, now=WED_14) == 3000.0


def test_prediction_rounds_to_nearest_50w():
    # 774 -> 750 (nearest 50); 776 -> 800. Setpoint granularity, so sensor noise doesn't re-command.
    down = predict_house_load_w([_obs("2026-07-08T14:15:00+00:00", 774.0)], 400.0, now=WED_14)
    up = predict_house_load_w([_obs("2026-07-08T14:15:00+00:00", 776.0)], 400.0, now=WED_14)
    assert down == 750.0
    assert up == 800.0


# --------------------------------------------------------------------------------------------------
# decide_car_mode_action — the decision, plus the bounded re-command flag
# --------------------------------------------------------------------------------------------------

def _decide(mode, **kw):
    base = dict(
        car_charging=True, soc_pct=55.0, min_reserve_soc=10.0, max_discharge_w=4000.0,
        static_w=800.0, predicted_house_w=800.0,
    )
    base.update(kw)
    return decide_car_mode_action(mode, **base)


def test_returns_a_frozen_car_mode_action():
    act = _decide("hold")
    assert isinstance(act, CarModeAction)
    # frozen dataclass — mutation must raise.
    try:
        act.power_w = 1.0  # type: ignore[misc]
        raise AssertionError("CarModeAction should be frozen")
    except Exception as exc:  # FrozenInstanceError is a subclass of Exception
        assert "FrozenInstance" in type(exc).__name__ or isinstance(exc, AttributeError)


def test_car_not_charging_is_a_no_op():
    act = _decide("match_home_load", car_charging=False)
    assert act.action == "none"
    assert act.power_w is None
    assert act.recommand is False


def test_hold_mode_is_todays_car_guard_behaviour():
    act = _decide("hold")
    assert act.action == "hold"
    assert act.power_w is None
    # Today's car-guard wording, byte-for-byte in spirit.
    assert "car charging" in act.reason
    assert "won't discharge into the car" in act.reason


def test_static_discharge_clamps_both_ends():
    # Below the 100 W floor -> 100; above max_discharge_w -> the max.
    assert _decide("static_discharge", static_w=50.0).power_w == 100.0
    assert _decide("static_discharge", static_w=6000.0, max_discharge_w=4000.0).power_w == 4000.0
    # In range -> passes through.
    assert _decide("static_discharge", static_w=800.0).power_w == 800.0


def test_static_reason_names_car_feeding_only_when_above_predicted():
    # static_w (1200) > predicted (800): the honest "part of this feeds the car" note appears.
    above = _decide("static_discharge", static_w=1200.0, predicted_house_w=800.0)
    assert above.action == "discharge"
    assert "1200" in above.reason
    assert "part of this feeds the car" in above.reason
    # static_w (500) <= predicted (800): no battery-feeds-the-car note (only the grid does).
    below = _decide("static_discharge", static_w=500.0, predicted_house_w=800.0)
    assert "part of this feeds the car" not in below.reason


def test_match_home_load_uses_the_prediction():
    act = _decide("match_home_load", predicted_house_w=850.0)
    assert act.action == "discharge"
    assert act.power_w == 850.0
    assert "850" in act.reason
    assert "grid feeds the car" in act.reason


def test_match_home_load_clamps_to_max_discharge():
    act = _decide("match_home_load", predicted_house_w=3000.0, max_discharge_w=2000.0)
    assert act.power_w == 2000.0


# ---- Reserve floor (inviolable, with a 1pp hysteresis band) --------------------------------------

def test_reserve_floor_holds_both_discharge_modes_within_the_1pp_band():
    for mode in ("static_discharge", "match_home_load"):
        # reserve + 0.5pp -> inside the band -> hold (grid covers everything).
        at = _decide(mode, soc_pct=10.5, min_reserve_soc=10.0)
        assert at.action == "hold", mode
        assert "reserve" in at.reason
        assert "grid covers the car and house" in at.reason
        # reserve + 1.5pp -> clear of the band -> discharge as configured.
        clear = _decide(mode, soc_pct=11.5, min_reserve_soc=10.0)
        assert clear.action == "discharge", mode


# ---- Reserve floor REAL hysteresis (F2: two thresholds + carried state) --------------------------

def test_reserve_hysteresis_enters_once_and_does_not_flap_on_noise():
    # F2 review finding: the old `soc <= reserve+1` was a SINGLE threshold, not the band the
    # docstring claimed. SoC noise around the floor (11.4/10.8/11.3/10.9, reserve 10) flapped the
    # battery hold on and off. With real hysteresis (enter <= reserve+1, resume >= reserve+3) it
    # enters the hold EXACTLY ONCE and never flaps back out (it never reaches 13.0).
    reserve = 10.0
    holding = False
    holds = resumes = 0
    for soc in (11.4, 10.8, 11.3, 10.9):
        act = decide_car_mode_action(
            "match_home_load", car_charging=True, soc_pct=soc, min_reserve_soc=reserve,
            max_discharge_w=4000.0, static_w=0.0, predicted_house_w=800.0, reserve_holding=holding)
        entered = act.reserve_hold and not holding
        resumed = holding and not act.reserve_hold
        holds += int(entered)
        resumes += int(resumed)
        holding = act.reserve_hold
    assert holds == 1     # entered the hold once (at 10.8)
    assert resumes == 0   # never flapped back to discharge on the noise


def test_reserve_hysteresis_resumes_only_at_plus_three_pp():
    reserve = 10.0
    common = dict(car_charging=True, min_reserve_soc=reserve, max_discharge_w=4000.0,
                  static_w=0.0, predicted_house_w=800.0)
    # Enter the hold at reserve+0.8pp.
    enter = decide_car_mode_action("match_home_load", soc_pct=10.8, reserve_holding=False, **common)
    assert enter.action == "hold" and enter.reserve_hold is True
    # Still holding at reserve+2.9pp (< the +3pp resume threshold) — no premature resume.
    still = decide_car_mode_action("match_home_load", soc_pct=12.9, reserve_holding=True, **common)
    assert still.action == "hold" and still.reserve_hold is True
    # Resume the discharge only once recovered to reserve+3pp.
    back = decide_car_mode_action("match_home_load", soc_pct=13.0, reserve_holding=True, **common)
    assert back.action == "discharge" and back.reserve_hold is False


def test_reserve_hold_flag_false_on_an_ordinary_discharge():
    act = _decide("match_home_load", soc_pct=55.0, min_reserve_soc=10.0)
    assert act.action == "discharge"
    assert act.reserve_hold is False


# ---- Bounded re-command (the anti-tracking rule) -------------------------------------------------

def test_recommand_true_at_session_start():
    # No current setpoint yet (session start) -> always (re-)command.
    act = _decide("static_discharge", static_w=800.0, current_setpoint_w=None)
    assert act.recommand is True


def test_recommand_false_for_a_small_change():
    # |1200 - 800| = 400, not greater than the 500 W rebond threshold -> hold the setpoint.
    act = _decide("static_discharge", static_w=1200.0, current_setpoint_w=800.0)
    assert act.recommand is False


def test_recommand_true_for_a_large_change():
    # |1400 - 800| = 600 > 500 -> re-command.
    act = _decide("static_discharge", static_w=1400.0, current_setpoint_w=800.0)
    assert act.recommand is True


def test_recommand_applies_to_match_mode_too():
    assert _decide("match_home_load", predicted_house_w=850.0,
                   current_setpoint_w=800.0).recommand is False  # 50 <= 500
    assert _decide("match_home_load", predicted_house_w=1500.0,
                   current_setpoint_w=800.0).recommand is True  # 700 > 500


def test_holds_recommand_so_the_caller_applies_them():
    # Both hold paths are applied by the caller every cycle (idempotent downstream), unlike the
    # power-setpoint gating on discharge — so they carry recommand True.
    assert _decide("hold").recommand is True
    assert _decide("static_discharge", soc_pct=10.5, min_reserve_soc=10.0).recommand is True
