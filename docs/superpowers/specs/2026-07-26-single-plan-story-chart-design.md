# One plan graph that tells the story

**Date:** 2026-07-26
**Status:** approved design, ready for planning
**Scope:** frontend only (`ems/web/frontend`), no backend or API change

## Problem

The dashboard renders three charts that all describe the same plan in three
different visual languages, each with its own headline and its own warning:

| Component | Position today | What it showed |
| --- | --- | --- |
| `CombinedPlanChart` | top level, always visible | "Next 24 hours" sparkline: SoC, solar, price, plan strip |
| `BatteryPlan` | inside "More from your home" | "Paused safely" card: actual/forecast line, cheap window, target, reserve |
| `EnergyStory` | inside "More" → "See the full plan" | Last/Next toggle, four stacked lanes, totals tiles |

With both disclosures open, all three appear in sequence. They repeat the same
`on_track` warning verbatim three times ("Plan targets 68% by 21:00 but projects
only 25%"), disagree in tone (`Watching` vs `Paused safely` vs `Behind the 88%
target`), and use different colours for the same quantities. The reported
outcome is that the page does not communicate what is happening or what to
expect.

The fix is not deduplication for its own sake — it is to leave exactly one
artefact on the page whose job is to answer "what is my battery doing and what
happens next".

## Decisions

Confirmed with the user before design:

1. **Time axis** — one continuous timeline. Recorded past on the left, `now`
   marker, forecast on the right. No Last/Next toggle.
2. **Detail level** — the battery-level curve is the hero. Solar and price
   become background context; battery action becomes a band strip. Not four
   co-equal stacked lanes.
3. **Headline** — the graph inherits the hero card's headline and confidence
   chip. The chart card carries no headline, no status pill, no warning banner.
4. **Removals** — `CombinedPlanChart.tsx` and `BatteryPlan.tsx` are deleted, not
   parked.
5. **Addition** — hovering the graph reveals per-slot details, matching the
   Insights charts.

## The component

A new `ems/web/frontend/src/PlanStory.tsx` occupies the slot where
`CombinedPlanChart` sits today: top level in the dashboard view, directly below
`OutcomeTiles`, always visible, no disclosure wrapper.

`EnergyStory.tsx` is removed from the dashboard. It survives as the declaration
site for the `StorySlot`, `StoryTotals` and `EnergyStoryData` types, which
`PlanStory` imports. The nested `plan-disclosure` / "See the full plan" section
in `App.tsx` is deleted — re-nesting a second chart would recreate the problem
this change exists to solve. Deeper numbers remain reachable via the Insights
tab and the existing "All the details" expander.

### Data contract

No backend work. `GET /api/energy-story?window=next` already returns everything
required:

- `recent: StorySlot[]` — recorded actuals for the trailing window
- `recent_hours: number` — how far back `recent` reaches
- `slots: StorySlot[]` — the forward plan
- `now: string`, `current_soc_pct`, `reserve_soc_pct`, `target_soc_pct`,
  `target_deadline`, `current_price_eur_per_kwh`
- `on_track`, `recent_review`, `trust_markers` — consumed by the hero, **not**
  by the chart

Each `StorySlot` carries `start`, `soc_pct`, `grid_w`, `solar_w`, `battery_w`,
`load_w`, `eur_per_kwh`, `action`.

Because the `past` window is no longer rendered, the `storyWindow` state, the
`window`/`onWindow`/`hideHeadline` props and the second story fetch drop out of
`App.tsx`. The `technicalStory` state is removed; `story` alone feeds the chart.

### Geometry

- **x-axis:** uniform slot width over the concatenation `[...recent, ...slots]`,
  indexed like the existing charts. With a ~3 h `recent` window and a 24 h plan
  this puts `now` roughly 11 % from the left, matching how the current chart
  already lays out its axis. Tick labels every 4–6 h plus an emphasised `now`.
- **y-axis:** battery level, fixed 0–100 %.
- **Gridlines:** the night target and the minimum reserve, and nothing else.
  Each is labelled inline at the right edge of its own line, so reading the
  chart never requires a legend lookup.

### Layers, back to front

1. **Price** — faint per-slot background shading across the full plot height;
   more expensive reads darker/stronger. Drawn from `eur_per_kwh`.
2. **Solar** — soft filled area scaled to its own maximum, sitting low in the
   plot as context.
3. **Battery level** — the hero curve. `recent` draws solid, `slots` draws
   dashed, joined at `now`. Gaps in `soc_pct` break the path rather than
   interpolating across missing data.
4. **Target and reserve** reference lines with inline right-edge labels.
5. **`now`** vertical marker.
6. **Action band strip** below the plot: one band per slot coloured by `action`,
   using the five labels already in use — charge from solar, charge from grid,
   power the house, use solar first, hold.

Colours, opacities and the `action` → colour/label mapping are taken verbatim
from the existing `EnergyStory` implementation and the app's entity tokens
(`--house`, `--car`, `--summer`, `--winter`, `--line`, `--muted`). This change is
a consolidation, not a re-skin: nothing should acquire a new hue.

Captions retain the axis ranges the current chart already prints, e.g.
`Solar 0–1,642 W` and `Price €0.00–€0.35`, so the background layers stay
quantitatively readable.

### Footer

The chart card's footer keeps only what is not stated elsewhere on the page:
saved today, battery percentage, and the existing "see each battery →" link into
the per-tower detail. No headline, no status pill, no `on_track` banner — the
hero card above already carries all three.

## Hover interaction

Reuse the pattern already shipped in `EnergyBehavior.tsx` (the Insights chart)
rather than introducing a second one:

- `const [hover, setHover] = useState<number | null>(null)` holding a slot index
- `onMouseMove` maps `clientX` through `getBoundingClientRect()` into viewBox
  space and floors to a slot index; out-of-range or unsampled slots set `null`
- `onMouseLeave` clears
- a dashed vertical crosshair at the hovered slot, `stroke="var(--muted)"`,
  `strokeDasharray="2 3"`
- a `.chart-tip` div positioned with `left: ${(cx(hover) / W) * 100}%`, built
  from the existing shared classes `.chart-tip`, `.chart-tip-title`,
  `.chart-tip-row`, `.chart-tip-val`, `.legend-dot`

`.chart-tip*` are already generically named and live in `styles.css` around line
2281, so no new CSS is required for the tooltip itself.

Tip contents — title is the slot's time label plus whether the slot is recorded
or forecast, then at most five rows: battery level, price, solar, the action
label, and grid flow. Rows whose value is null are omitted rather than shown as
zero.

**Accessibility parity, stated deliberately:** the Insights chart is
mouse-driven, and `PlanStory` matches it rather than inventing a keyboard
interaction that exists nowhere else in the app. Non-mouse users are served by
the same mechanisms the current charts use — `role="img"` with a descriptive
`aria-label`, plus an `sr-only` sentence summarising the plan. The hover tooltip
is enrichment, never the only route to a fact.

## Migration traps

These will break the build or the hero card if missed:

1. `SavedToday`, `PlanConfidence` and `BatteryPlanData` are declared in
   `BatteryPlan.tsx` but imported by `App.tsx` and `OutcomeTiles.tsx:1`. They
   must move before the file is deleted — into `EnergyStory.tsx` alongside the
   other story types, or a small shared `types.ts`.
2. The `/api/battery-plan` fetch in `App.tsx:518` **stays**. The hero card's
   confidence chip ("MEDIUM CONFIDENCE") depends on it. Deleting the component
   must not delete its data source.
3. `describeCombinedPlan` is exported from `CombinedPlanChart.tsx`. Confirm no
   remaining consumer — including e2e specs — before removing it.
4. `e2e/ui.spec.ts` asserts against the removed components near lines 937 and
   1042, including comments that name `BatteryPlan` as the source of truth for
   "cheap window". Those specs need rewriting, not deleting.

## Testing

The frontend has no unit-test runner; Playwright is the harness. Update
`e2e/ui.spec.ts`:

- the dashboard renders exactly one plan chart (`plan-story` present;
  `combined-plan-chart`, `battery-plan`, `plan-disclosure` absent)
- the continuous axis renders both a recorded and a forecast segment, and a
  `now` marker
- target and reserve reference lines render with inline labels
- hovering the plot shows `plan-story-tip` with the hovered slot's time, and
  moving out of the plot removes it
- the action band strip renders and its legend labels match the bands present

New test ids: `plan-story`, `plan-story-tip`.

Per the project's e2e note, Playwright boots against the live SQLite database —
repoint `db_path` for the run, and use an isolated port so concurrent worktree
runs do not collide on 8099.

## Out of scope

- Any change to `/api/energy-story` or planner logic
- The hero card's own copy, confidence chip or warning text
- The Insights tab
- Keyboard-driven chart navigation (no precedent in the app; see parity note)
- Re-theming colours or introducing new palette tokens
