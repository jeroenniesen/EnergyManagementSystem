# One plan graph that tells the story

**Date:** 2026-07-26
**Status:** approved design, ready for planning
**Scope:** two phases — phase 1 is frontend only (`ems/web/frontend`) with no
backend change; phase 2 adds one optional API parameter plus bounded store reads
behind it. Each phase ships and is reviewed independently.

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
6. **Past window length** — 3 h, whatever `recent_hours` already returns. A
   longer left-hand context was considered and rejected as not worth a backend
   change; `now` therefore sits roughly 11 % from the left edge.
7. **History lives on Insights** — the dashboard graph stays forward-looking.
   Browsing what actually happened is the Insights tab's job, and the same
   component serves it there (phase 2 below).

## Phase 1 — the dashboard graph

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

No backend work in this phase. `GET /api/energy-story?window=next` already
returns everything required:

- `recent: StorySlot[]` — recorded actuals for the trailing window
- `recent_hours: number` — how far back `recent` reaches
- `slots: StorySlot[]` — the forward plan
- `now: string`, `current_soc_pct`, `reserve_soc_pct`, `target_soc_pct`,
  `target_deadline`, `current_price_eur_per_kwh`
- `on_track`, `recent_review`, `trust_markers` — consumed by the hero, **not**
  by the chart

Each `StorySlot` carries `start`, `soc_pct`, `grid_w`, `solar_w`, `battery_w`,
`load_w`, `eur_per_kwh`, `action`.

The atomic `/api/dashboard` snapshot added in #54/#56 covers `status`,
`freshness` and `alerts` only, with a fan-out fallback for older servers. The
energy-story fetch sits outside that snapshot in both paths, so this change does
not interact with it.

Because the dashboard no longer renders the `past` window, the `storyWindow`
state, the `window`/`onWindow`/`hideHeadline` props and the disclosure-gated
second story fetch drop out of `App.tsx`. The `technicalStory` state is removed;
`story` alone feeds the chart. Note the pattern that gated fetch used — only
fetching the `past` window while the disclosure was open — because phase 2
reuses it on Insights.

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
3. **Battery level** — the hero curve. Recorded slots draw solid, forecast slots
   draw dashed, joined at `now`. **The solid/dashed split is decided by each
   slot's own timestamp against `now`, never by which array it arrived in.** That
   rule lets the identical component render a forecast-only window, an
   actuals-only window, or a mixed one, which is what makes phase 2 a reuse
   rather than a fork. Gaps in `soc_pct` break the path rather than interpolating
   across missing data.
4. **Target and reserve** reference lines with inline right-edge labels.
5. **`now`** vertical marker.
6. **Action band strip** below the plot: one band per slot coloured by `action`,
   using the five labels already in use — charge from solar, charge from grid,
   power the house, use solar first, hold.

Individual band segments carry `aria-hidden="true"` and no `aria-label`. This is
not an oversight: PR #57 ("Fix chart SVG accessibility semantics") deliberately
replaced per-segment `aria-label`s in `CombinedPlanChart` with `aria-hidden`,
because ~100 individually-labelled segments turn a screen-reader pass into noise.
The strip's meaning is carried by the container's `aria-label` and the `sr-only`
summary instead. Do not reintroduce per-segment labels.

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

These will break the build or the hero card if missed. Referenced by symbol, not
line number — several PRs merged during design already shifted the line numbers
once:

1. `SavedToday`, `PlanConfidence` and `BatteryPlanData` are declared in
   `BatteryPlan.tsx` but imported by `App.tsx` and `OutcomeTiles.tsx`. They must
   move before the file is deleted — into `EnergyStory.tsx` alongside the other
   story types, or a small shared `types.ts`.
2. The `/api/battery-plan` fetch **stays**. The hero card's confidence chip
   ("MEDIUM CONFIDENCE") depends on it. Deleting the component must not delete
   its data source.
3. `describeCombinedPlan` is exported from `CombinedPlanChart.tsx`. Confirm no
   remaining consumer — including e2e specs — before removing it.
4. `e2e/ui.spec.ts` asserts against the removed components, including comments
   that name `BatteryPlan` as the source of truth for "cheap window" and one that
   calls `CombinedPlanChart` "the visible primary plan". Those specs need
   rewriting, not deleting. Grep for the component names rather than trusting
   line numbers.

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

## Phase 2 — the same graph on Insights

Ships after phase 1 is reviewed. The dashboard answers "what happens next";
Insights answers "what actually happened". Both use `PlanStory`.

### Why this needs a backend change

Insights is not a trailing-window page. It is parameterised by `period` +
`anchor` and fetches `/api/report?period=day&date=<anchor>`, with prev/next day
stepping. Dropping a "last 24 h from now" graph into it would put a graph
labelled *now* beside a page labelled *Tuesday 21 July* — the two would
disagree whenever the user steps back a day. So the Insights graph follows the
existing day stepper, which means the story endpoint must accept a date.

Today `_past_story` in `ems/web/api.py` hardcodes its range:

```python
now = datetime.now(UTC)
cutoff = (now - timedelta(hours=24)).isoformat()
raw = await store.recent_raw_since(cutoff)
der = await store.recent_derived_since(cutoff)
```

The store helpers are `recent_*_since(cutoff)` — lower bound only.

### The change

1. `GET /api/energy-story` accepts an optional `date=YYYY-MM-DD`. Valid only with
   `window=past`; combining it with `window=next` is a 422 rather than a silent
   fallback.
2. Bounded store reads — add an upper bound alongside the existing
   `recent_raw_since` / `recent_derived_since` (either `*_between(start, end)`
   helpers or an optional `until` argument, whichever fits the store's existing
   shape). `_window_price_slots` already takes a start and an end.
3. `_past_story` derives `cutoff` and end from the requested local day's
   boundaries, and passes the day's end where it currently passes `now` — so
   `build_past_story` anchors to the day being viewed, not the wall clock. Local
   time zone, matching how Insights computes `anchor` and how `todayStr()`
   behaves; a day is a local calendar day, not a UTC one.
4. Omitting `date` preserves today's exact trailing-24 h behaviour, so nothing
   already calling the endpoint changes.
5. Absent history for the requested day returns the existing `_empty_story`
   shape, and the graph renders its empty state rather than an error.

### Frontend

`PlanStory` gains no new rendering modes — the timestamp-driven solid/dashed rule
already covers an actuals-only window. Insights mounts it inside its own section
(registered with `SectionNav` like the existing panels), fetching
`/api/energy-story?window=past&date=<anchor>` when its section is mounted, reusing
the lazy-fetch pattern the deleted `technicalStory` gate used. Target and reserve
lines still render — for a past day they are exactly the reference you want to
validate the day against, which is why `_past_story` already returns
`target_soc_pct`.

Stepping days re-fetches. Week/month/year periods do not render the graph: a
15-minute-slot chart over a year is meaningless, and `_past_story` is day-shaped.
Hide it for those periods rather than degrading it.

### Testing

Backend: `date` yields that day's slots and not today's; `date` with
`window=next` is rejected; a day with no history returns the empty shape;
omitting `date` is byte-identical to current behaviour.

Frontend e2e: the graph appears on Insights for `period=day`, follows the day
stepper, and is absent for week/month/year.

## Out of scope

- Any change to planner logic, or to `/api/energy-story`'s `next` window
- The hero card's own copy, confidence chip or warning text
- Making `period=week|month|year` render a slot chart
- Keyboard-driven chart navigation (no precedent in the app; see parity note)
- Re-theming colours or introducing new palette tokens
