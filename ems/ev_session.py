"""EV charging-session detection + car-SoC estimation — PURE, on-demand from recorded history.

Design decision (feat/ev-charging): there is **no recorder state machine and no new periodic
writes**. Sessions and the SoC estimate are *computed when asked* from the `raw_samples` already
stored by the recorder (each carries `ev_power_w` at the ~15 s–5 min cycle cadence). Same shape as
`retrospect.py`: the API hands in stored rows; everything here is deterministic and unit-tested.

The car exposes **no API**, so its state of charge cannot be read. Instead the user occasionally
sets a manual SoC *anchor* (a percent at a timestamp), and we add the charging energy the
HomeWizard car meter measured *after* that anchor:

    soc = anchor_pct + (measured_kWh_added × charge_efficiency) / battery_net_kwh × 100

┌─────────────────────────────────────────────────────────────────────────────────────────────┐
│ LIMITATION — driving is NOT modelled. The estimate only ever RISES (with measured charging);  │
│ it has no idea how much the car was driven, so it cannot fall. After a drive the estimate is   │
│ stale/too-high until the user RE-ANCHORS. `estimate_soc` returns an `age_hours` + `stale` flag │
│ (>72 h) precisely so the UI can nudge a re-anchor; treat the number as "at least this, if the  │
│ car hasn't moved", never as ground truth.                                                      │
└─────────────────────────────────────────────────────────────────────────────────────────────┘

Energy is a conservative zero-order-hold sum (like the retrospect resampler): each sample holds
its power forward to the next sample, with the interval **capped at 10 min** so a data gap can
never fabricate energy. The last sample of a session holds for one inferred cadence interval
(the median Δt, same cap) rather than to the next (post-gap) sample.
"""
from __future__ import annotations

import statistics
from datetime import UTC, datetime

_MAX_DT_MIN = 10.0  # cap on any single hold interval — a data gap must not fabricate energy


def _parse(ts: object) -> datetime | None:
    """ISO-8601 string → tz-aware UTC datetime, or None if unparsable (like retrospect._parse)."""
    if not isinstance(ts, str):
        return None
    try:
        dt = datetime.fromisoformat(ts)
    except ValueError:
        return None
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def _to_utc(dt: datetime) -> datetime:
    """Coerce a datetime to tz-aware UTC (a naive value is assumed to be UTC)."""
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _prepare(raw_rows: list[dict]) -> list[tuple[datetime, float]]:
    """(ts, ev_power_w) pairs, chronological, dropping rows with a bad ts / missing power."""
    out: list[tuple[datetime, float]] = []
    for r in raw_rows:
        dt = _parse(r.get("ts"))
        p = r.get("ev_power_w")
        if dt is None or p is None:
            continue
        try:
            out.append((dt, float(p)))
        except (TypeError, ValueError):
            continue
    out.sort(key=lambda s: s[0])
    return out


def _deltas_and_cadence(samples: list[tuple[datetime, float]]) -> tuple[list[float], float]:
    """Δt (minutes) between consecutive samples, plus the nominal cadence = median Δt (capped).

    Median is robust to the odd big data gap, so a run with one missing cycle still infers the
    true ~5 min cadence for its last-sample tail."""
    deltas = [
        (samples[i + 1][0] - samples[i][0]).total_seconds() / 60.0
        for i in range(len(samples) - 1)
    ]
    cadence = min(statistics.median(deltas), _MAX_DT_MIN) if deltas else 0.0
    return deltas, cadence


def _runs(samples: list[tuple[datetime, float]], threshold_w: float) -> list[tuple[int, int]]:
    """Maximal runs of consecutive at/above-threshold samples, as (first_idx, last_idx) pairs.

    Two active samples with no sample between them stay ONE run regardless of the time gap — a
    pure data gap does not split a session (only a real below-threshold stretch does)."""
    runs: list[tuple[int, int]] = []
    i, n = 0, len(samples)
    while i < n:
        if samples[i][1] >= threshold_w:
            j = i
            while j + 1 < n and samples[j + 1][1] >= threshold_w:
                j += 1
            runs.append((i, j))
            i = j + 1
        else:
            i += 1
    return runs


def _merge(
    runs: list[tuple[int, int]], samples: list[tuple[datetime, float]], gap_tolerance_min: float
) -> list[tuple[int, int]]:
    """Merge adjacent runs whose pause (last-active → next-active) is < gap_tolerance — a brief
    charging pause below threshold doesn't end the session. Merged sessions span the full index
    range, so the bridged sub-threshold samples are included (at their own low power)."""
    if not runs:
        return []
    merged = [runs[0]]
    for first, last in runs[1:]:
        prev_first, prev_last = merged[-1]
        pause_min = (samples[first][0] - samples[prev_last][0]).total_seconds() / 60.0
        if pause_min < gap_tolerance_min:
            merged[-1] = (prev_first, last)
        else:
            merged.append((first, last))
    return merged


def _energy_kwh(
    samples: list[tuple[datetime, float]],
    deltas: list[float],
    cadence_min: float,
    start_idx: int,
    end_idx: int,
    *,
    since: datetime | None = None,
) -> float:
    """Zero-order-hold energy (kWh) over samples [start_idx, end_idx]. Each sample holds its power
    forward for min(Δt-to-next, 10 min); the last sample holds for one nominal cadence interval.
    With `since`, only samples at/after that instant contribute (post-anchor accounting)."""
    total_wh = 0.0
    for i in range(start_idx, end_idx + 1):
        ts, power = samples[i]
        if since is not None and ts < since:
            continue
        dt_min = min(deltas[i], _MAX_DT_MIN) if i < end_idx else min(cadence_min, _MAX_DT_MIN)
        total_wh += power * (dt_min / 60.0)
    return total_wh / 1000.0


def _sessions(
    raw_rows: list[dict], *, threshold_w: float, min_duration_min: float, gap_tolerance_min: float
) -> tuple[list[tuple[datetime, float]], list[float], float, list[tuple[int, int]]]:
    """Shared core: prepared samples, deltas, cadence, and the detected session index ranges
    (runs merged over brief gaps, then dropping anything shorter than min_duration)."""
    samples = _prepare(raw_rows)
    deltas, cadence = _deltas_and_cadence(samples)
    merged = _merge(_runs(samples, threshold_w), samples, gap_tolerance_min)
    kept: list[tuple[int, int]] = []
    for start_idx, end_idx in merged:
        duration_min = (samples[end_idx][0] - samples[start_idx][0]).total_seconds() / 60.0
        if duration_min >= min_duration_min:
            kept.append((start_idx, end_idx))
    return samples, deltas, cadence, kept


def detect_sessions(
    raw_rows: list[dict],
    *,
    threshold_w: float = 1500.0,
    min_duration_min: float = 5.0,
    gap_tolerance_min: float = 10.0,
) -> list[dict]:
    """Detect EV charging sessions in recorded `raw_rows` (each a dict with `ts` + `ev_power_w`).

    A session is a run of samples with `ev_power_w >= threshold_w`; a below-threshold pause shorter
    than `gap_tolerance_min` bridges rather than splits it (a car briefly throttling to ~0);
    sessions shorter than `min_duration_min` are dropped. Returns chronological dicts:
    `{"start", "end" (ISO), "kwh" (AC-side, 2dp), "avg_kw", "peak_kw", "samples"}`.
    """
    samples, deltas, cadence, sessions = _sessions(
        raw_rows, threshold_w=threshold_w,
        min_duration_min=min_duration_min, gap_tolerance_min=gap_tolerance_min,
    )
    out: list[dict] = []
    for start_idx, end_idx in sessions:
        powers = [samples[i][1] for i in range(start_idx, end_idx + 1)]
        out.append({
            "start": samples[start_idx][0].isoformat(),
            "end": samples[end_idx][0].isoformat(),
            "kwh": round(_energy_kwh(samples, deltas, cadence, start_idx, end_idx), 2),
            "avg_kw": round(_mean(powers) / 1000.0, 2),
            "peak_kw": round(max(powers) / 1000.0, 2),
            "samples": end_idx - start_idx + 1,
        })
    return out


def estimate_soc(
    raw_rows: list[dict],
    *,
    anchor_pct: float,
    anchor_ts: str,
    battery_net_kwh: float,
    now: datetime,
    charge_efficiency: float = 0.90,
) -> dict | None:
    """Estimate the car's SoC from a manual anchor + the charging energy measured since it.

    Energy added since the anchor = the EV energy of every session portion AFTER `anchor_ts` (a
    session straddling the anchor counts ONLY its post-anchor part) × `charge_efficiency`. Then::

        soc_pct = min(100, anchor_pct + added_kwh_battery / battery_net_kwh × 100)

    ⚠️ Driving is NOT modelled — the estimate only ever RISES with measured charging and never
    falls, so it drifts high after any drive until the user RE-ANCHORS (see module docstring). The
    returned `age_hours`/`stale` (>72 h) flag exists to prompt that re-anchor.

    `now` is a REQUIRED parameter (this function is pure — it never reads a clock). Returns None
    when `anchor_ts` is unparsable or `battery_net_kwh <= 0`. Otherwise a dict:
    `{"soc_pct" (1dp), "anchor_pct", "anchor_ts", "added_kwh" (battery-side, 2dp),
    "sessions_since_anchor", "age_hours" (1dp), "stale"}`.
    """
    anchor = _parse(anchor_ts)
    if anchor is None or battery_net_kwh <= 0:
        return None

    samples, deltas, cadence, sessions = _sessions(
        raw_rows, threshold_w=1500.0, min_duration_min=5.0, gap_tolerance_min=10.0)

    added_kwh_ac = 0.0
    sessions_since = 0
    for start_idx, end_idx in sessions:
        portion = _energy_kwh(samples, deltas, cadence, start_idx, end_idx, since=anchor)
        if portion > 0.0:
            added_kwh_ac += portion
            sessions_since += 1

    added_kwh_battery = added_kwh_ac * charge_efficiency
    soc_pct = min(100.0, anchor_pct + added_kwh_battery / battery_net_kwh * 100.0)
    age_hours = (_to_utc(now) - anchor).total_seconds() / 3600.0
    return {
        "soc_pct": round(soc_pct, 1),
        "anchor_pct": anchor_pct,
        "anchor_ts": anchor_ts,
        "added_kwh": round(added_kwh_battery, 2),
        "sessions_since_anchor": sessions_since,
        "age_hours": round(age_hours, 1),
        "stale": age_hours > 72.0,
    }
