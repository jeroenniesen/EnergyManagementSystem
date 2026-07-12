"""EV charging-session detection + car-SoC estimation (ems.ev_session).

Pure, on-demand from recorded raw rows (dicts with `ts` + `ev_power_w`, like test_reporting's
fixtures). Every expected number is hand-computed in a comment so the math stays exact.
"""
from datetime import UTC, datetime, timedelta

from ems.ev_session import detect_sessions, estimate_soc

BASE = datetime(2026, 7, 12, 10, 0, tzinfo=UTC)  # deterministic anchor for all fixtures


def _row(base: datetime, minutes: float, ev_w: float) -> dict:
    """A minimal raw sample row — the detector reads only `ts` and `ev_power_w`."""
    return {"ts": (base + timedelta(minutes=minutes)).isoformat(), "ev_power_w": float(ev_w)}


# --- detect_sessions -----------------------------------------------------------------------------

def test_single_clean_session_11kwh():
    # 12 samples @ 11 kW, 5-min cadence (minutes 0..55). Energy = 11 holds of 5 min + the last
    # sample × the 5-min median cadence = 12 × 5 min = 60 min = 1 h. 11 kW × 1 h = 11.00 kWh.
    rows = [_row(BASE, 5 * i, 11000) for i in range(12)]
    sessions = detect_sessions(rows)
    assert len(sessions) == 1
    s = sessions[0]
    assert s["kwh"] == 11.0
    assert s["avg_kw"] == 11.0
    assert s["peak_kw"] == 11.0
    assert s["samples"] == 12
    assert s["start"] == BASE.isoformat()
    assert s["end"] == (BASE + timedelta(minutes=55)).isoformat()


def test_detect_ignores_other_sample_columns():
    # Real rows carry grid/solar/battery/soc too (test_reporting idiom); only ev_power_w matters.
    rows = [{"ts": (BASE + timedelta(minutes=5 * i)).isoformat(), "grid_power_w": 500.0,
             "solar_power_w": 0.0, "battery_power_w": 0.0, "ev_power_w": 11000.0, "soc_pct": 50.0}
            for i in range(12)]
    sessions = detect_sessions(rows)
    assert len(sessions) == 1 and sessions[0]["kwh"] == 11.0


def test_all_below_threshold_yields_no_sessions():
    # 800 W standby draw is below the 1500 W charging threshold → not a session.
    rows = [_row(BASE, 5 * i, 800) for i in range(12)]
    assert detect_sessions(rows) == []


def test_brief_dip_within_tolerance_stays_one_session():
    # 4 active samples, a single below-threshold dip at min 20, then 3 more active. The pause from
    # the last active (min 15) to the next active (min 25) = 10 min < gap_tolerance 15 → bridged.
    rows = (
        [_row(BASE, 5 * i, 11000) for i in range(4)]           # 0, 5, 10, 15
        + [_row(BASE, 20, 0)]                                  # brief dip
        + [_row(BASE, m, 11000) for m in (25, 30, 35)]
    )
    sessions = detect_sessions(rows, gap_tolerance_min=15.0)
    assert len(sessions) == 1
    assert sessions[0]["samples"] == 8  # spans the full range, incl. the bridged dip sample


def test_dip_longer_than_tolerance_splits_into_two():
    # Below-threshold pause from min 15 to min 35 = 20 min ≥ gap_tolerance 10 → the session splits.
    rows = (
        [_row(BASE, 5 * i, 11000) for i in range(4)]           # 0, 5, 10, 15
        + [_row(BASE, m, 0) for m in (20, 25, 30)]             # long dip
        + [_row(BASE, m, 11000) for m in (35, 40, 45, 50)]
    )
    sessions = detect_sessions(rows)
    assert len(sessions) == 2
    assert sessions[0]["start"] == BASE.isoformat()
    assert sessions[0]["end"] == (BASE + timedelta(minutes=15)).isoformat()
    assert sessions[1]["start"] == (BASE + timedelta(minutes=35)).isoformat()


def test_short_blip_below_min_duration_is_dropped():
    # A real 30-min session, plus a lone 1-sample spike far away. The spike has zero duration
    # (< min_duration 5 min) → dropped; only the real session survives.
    rows = (
        [_row(BASE, 5 * i, 11000) for i in range(7)]                       # 0..30 min: real session
        + [_row(BASE, 120, 0), _row(BASE, 125, 11000), _row(BASE, 130, 0)]  # isolated blip
    )
    sessions = detect_sessions(rows)
    assert len(sessions) == 1
    assert sessions[0]["start"] == BASE.isoformat()
    assert sessions[0]["samples"] == 7


def test_data_gap_over_ten_min_does_not_fabricate_energy():
    # 6 active samples @ 11 kW with a 30-min DATA gap (no samples) between min 10 and min 40. Holds:
    # 5 + 5 + min(30, 10)=10 + 5 + 5 + last-sample cadence 5 = 35 min. 11 kW × 35/60 h = 6.42 kWh.
    # Uncapped, the 30-min gap would fabricate energy → 55 min = 10.08 kWh.
    rows = [_row(BASE, m, 11000) for m in (0, 5, 10, 40, 45, 50)]
    sessions = detect_sessions(rows)
    assert len(sessions) == 1
    assert sessions[0]["kwh"] == 6.42
    assert sessions[0]["samples"] == 6


def test_empty_rows_yield_no_sessions():
    assert detect_sessions([]) == []


# --- estimate_soc --------------------------------------------------------------------------------

def test_estimate_soc_anchor_math():
    # 57.5 kWh usable pack; anchor 40% just before a clean 11.5 kWh (AC) session.
    # 11.5 kWh × 0.90 = 10.35 kWh into the pack; 10.35 / 57.5 × 100 = 18.0 pp → 40 + 18 = 58.0%.
    anchor_ts = (BASE - timedelta(minutes=1)).isoformat()
    rows = [_row(BASE, 5 * i, 11500) for i in range(12)]  # 12 × 5 min = 1 h @ 11.5 kW = 11.5 kWh AC
    est = estimate_soc(rows, anchor_pct=40.0, anchor_ts=anchor_ts,
                       battery_net_kwh=57.5, now=BASE + timedelta(hours=2))
    assert est is not None
    assert est["added_kwh"] == 10.35   # battery-side (after 0.90 efficiency)
    assert est["soc_pct"] == 58.0
    assert est["sessions_since_anchor"] == 1
    assert est["stale"] is False
    assert est["anchor_pct"] == 40.0 and est["anchor_ts"] == anchor_ts


def test_estimate_soc_straddling_session_counts_only_post_anchor():
    # A 6 kW session runs from 15 min BEFORE the anchor to 10 min after it. Only the post-anchor
    # part counts: samples at 0/+5/+10 min hold 5 + 5 + 5 = 15 min → 6 kW × 0.25 h = 1.5 kWh (AC).
    # 1.5 × 0.90 = 1.35 kWh into a 54 kWh pack → +2.5 pp → 50 + 2.5 = 52.5%. (The full session is
    # 3.0 kWh AC — exactly double — so this proves the pre-anchor half is excluded.)
    anchor_ts = BASE.isoformat()
    rows = [_row(BASE, m, 6000) for m in (-15, -10, -5, 0, 5, 10)]
    est = estimate_soc(rows, anchor_pct=50.0, anchor_ts=anchor_ts,
                       battery_net_kwh=54.0, now=BASE + timedelta(hours=1))
    assert est["added_kwh"] == 1.35
    assert est["soc_pct"] == 52.5
    assert est["sessions_since_anchor"] == 1


def test_estimate_soc_clamps_at_100():
    # anchor 95% + 9.9 kWh (11 kWh AC × 0.90) into a tiny 10 kWh pack would be 194% → clamped 100.0.
    anchor_ts = (BASE - timedelta(minutes=1)).isoformat()
    rows = [_row(BASE, 5 * i, 11000) for i in range(12)]  # 11.0 kWh AC
    est = estimate_soc(rows, anchor_pct=95.0, anchor_ts=anchor_ts,
                       battery_net_kwh=10.0, now=BASE + timedelta(hours=1))
    assert est["soc_pct"] == 100.0


def test_estimate_soc_staleness_at_72h_boundary():
    anchor_ts = BASE.isoformat()
    # Exactly 72 h old → NOT stale (age_hours 72.0, and 72.0 > 72 is False).
    at_72h = estimate_soc([], anchor_pct=60.0, anchor_ts=anchor_ts,
                          battery_net_kwh=57.5, now=BASE + timedelta(hours=72))
    assert at_72h["age_hours"] == 72.0
    assert at_72h["stale"] is False
    # One minute past 72 h → stale.
    past = estimate_soc([], anchor_pct=60.0, anchor_ts=anchor_ts,
                        battery_net_kwh=57.5, now=BASE + timedelta(hours=72, minutes=1))
    assert past["stale"] is True


def test_estimate_soc_empty_rows_returns_anchor_soc():
    # No charging measured since the anchor → SoC stays at the anchor, nothing added.
    est = estimate_soc([], anchor_pct=40.0, anchor_ts=BASE.isoformat(),
                       battery_net_kwh=57.5, now=BASE + timedelta(hours=1))
    assert est["soc_pct"] == 40.0
    assert est["added_kwh"] == 0.0
    assert est["sessions_since_anchor"] == 0


def test_estimate_soc_none_on_bad_anchor_or_capacity():
    now = BASE + timedelta(hours=1)
    assert estimate_soc([], anchor_pct=40.0, anchor_ts="not-a-date",
                        battery_net_kwh=57.5, now=now) is None
    assert estimate_soc([], anchor_pct=40.0, anchor_ts=BASE.isoformat(),
                        battery_net_kwh=0.0, now=now) is None
    assert estimate_soc([], anchor_pct=40.0, anchor_ts=BASE.isoformat(),
                        battery_net_kwh=-5.0, now=now) is None
