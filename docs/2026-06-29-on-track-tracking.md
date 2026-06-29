# "Am I on track today?" — recent actuals on the planner timeline (spec)

## Problem

The dashboard shows the **plan** (next-24h Energy Story) but not whether reality is following it. The
operator wants, at a glance, to see the **last 3 hours of actuals** leading into the plan — real SoC,
real solar production, what the battery actually did — and an **on-track** read.

## Approach

Extend the existing "next" Energy Story so it carries a short **recent actuals** segment immediately
before "now", on the *same* SoC timeline. The chart then reads as one continuous line:
`[last 3h actual] → now → [next 24h planned]`. Add an **on-track** assessment to the header.

Backward-compatible and additive — production keeps working; old clients ignore the new fields.

## API (additive) — `GET /api/energy-story?window=next`

New fields (alongside the existing `slots`, `totals`, `headline`, `trust_markers`, …):

```jsonc
{
  "recent_hours": 3,
  "recent": [                       // last ~3h of RECORDED actuals, 15-min grid, oldest→now
    { "start": "…", "soc_pct": 58.4, "solar_w": 2140, "battery_w": -320,
      "load_w": 410, "eur_per_kwh": 0.21, "action": "charging" }
  ],
  "on_track": {
    "status": "ahead" | "on_track" | "behind" | "unknown",
    "actual_soc_pct": 61.0,
    "target_soc_pct": 77.0,
    "deficit_kwh": 0.0,
    "message": "On track — projected to reach 77% by sunset.",
    // iteration 3 adds: solar_actual_kwh / solar_forecast_kwh over the recent window,
    // and an executed-vs-planned note.
  }
}
```

- `recent` is built exactly like the "past" story (`build_past_story`) but scoped to the last
  `RECENT_HOURS` (=3); `action` comes from the **actual** battery power (charging/discharging/holding/
  self-use) — i.e. what the battery *executed* (in dry-run, what it did on its own).
- `on_track` reuses the night-target `ChargeNeed` (`on_track`, `deficit_kwh`, `target_soc_pct`): the
  honest, deterministic "will we hit tonight's target" signal — `ahead` if SoC already ≥ target.
- Empty/short history degrades gracefully: `recent: []` (no segment), `status: "unknown"`.

## Frontend (iteration 2)

On the next-24h tile, draw the `recent` slots **before a "now" divider** on the SoC track — actual
SoC solid, projected SoC after now visually distinct — with the actual solar and the executed action.
Header shows an **on-track badge** (green ahead/on-track, amber behind) + the message.

## Iteration 3 — "did we do right"

Enrich `on_track` with **forecast-vs-actual solar** over the recent window (was the sun as predicted?)
and an **executed-vs-planned** note, with calm, honest copy.

## Upgrade path (polish)

`scripts/upgrade.sh` + a one-line `curl … | bash` on GitHub: pull/download latest, rebuild the SPA,
`uv sync`, restart the LaunchAgent — preserving `ems/data`.
