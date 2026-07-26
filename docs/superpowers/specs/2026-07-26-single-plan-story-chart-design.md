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
- `on_track`, `recent_review`, `trust_markers` — narrative evidence, **not** chart
  geometry. **Correction to an earlier draft:** it asserted the hero already
  consumes all three. It does not — `App.tsx` consumes only `on_track.message`,
  and `recent_review.message` plus the `trust_markers` chips are rendered
  *solely* inside `EnergyStory`. Deleting `EnergyStory` from the dashboard would
  therefore silently destroy them: the "Last 3h: 1.4 kWh solar (94% of forecast)"
  line and the "Reserve respected" / "Battery covers the evening peak" chips would
  simply vanish. They are genuinely non-duplicated evidence, and this project's
  explainability-first rule means they must be **moved into the hero**, not
  dropped, before their old renderer is deleted.

  When moving `trust_markers`, carry the **B-31 filter** with it
  (`EnergyStory.tsx` ~line 150): the "No grid top-up needed" chip is suppressed
  while `on_track.status === "behind"`, because the caution banner already owns
  that fact. Dropping that filter would put a reassuring chip next to a
  contradicting warning — reintroducing exactly the self-contradiction this whole
  change exists to remove.

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

**The x-axis is a true time scale, not a slot index.** Index positioning is only
correct while the slot array is complete and evenly spaced; the moment history has
a missing sample, an irregular cadence or a forecast gap, an index axis compresses
the gap to nothing, misreports elapsed time, and places `now` at the wrong place.
A chart whose whole purpose is "when does this happen" cannot lie about *when*.

**Correction to an earlier draft of this spec:** it claimed both existing charts
position by index and that a time scale would be a new divergence. That is wrong.
`EnergyBehavior` is index-based, but `CombinedPlanChart` **already** derives
`t0`/`t1` from `Date.parse` and sizes marks as
`x(start + SLOT_MS) - x(start)` — the exact model specified here. So this is not a
new invention to be designed from scratch; it is an existing, working model to be
lifted from that file before it is deleted, and then completed. Its four remaining
defects are what the rules below add:

1. it keeps `recent` and `slots` as separate arrays instead of one merged series,
2. it has no boundary-collision handling,
3. it does not break the SoC path across a timestamp gap,
4. its x tick labels are still index-selected.

Treat the geometry work as "finish `CombinedPlanChart`'s model", not "replace it".

Build the model in three steps:

1. **Merge and normalise.** Concatenate `recent` and `slots`, parse every `start`
   to an epoch, sort ascending, and de-duplicate by `start`. The two arrays *can*
   collide at the boundary — `recent`'s newest slot and `slots`'s oldest can
   describe the same quarter — and on collision the **recorded** slot wins, since
   a measurement beats a projection for a quarter that has already happened.
   Concatenation order is therefore never load-bearing.
2. **Domain.** `t0` = first slot's start. `t1` = last slot's start + `SLOT_MS`
   (15 min), so the final slot occupies width rather than collapsing to a line.
   Derived from timestamps only, never from `array.length`.
3. **Scale.** `x(t) = PAD.l + ((t - t0) / (t1 - t0)) * PLOT_W`.

Consequences that must hold:

- **Per-slot marks** (price shading, action bands) span
  `[x(start), x(start + SLOT_MS)]`. Width comes from the slot's own nominal
  duration — **not** from `PLOT_W / n`, and **not** from the delta to the next
  slot, which would balloon a gap-adjacent slot to cover the whole gap.
- **Gaps render as gaps.** A missing quarter draws no band and no price mark, so
  absent data reads as absent instead of being silently interpolated or
  compressed away. This is the honest failure mode and it is intended.
- **The SoC path breaks across a gap** rather than drawing a straight line
  through missing data. Break when the delta between consecutive slots exceeds
  `1.5 × SLOT_MS`.
- **`now` is positioned as `x(Date.parse(story.now))`** — correct by construction,
  and no longer dependent on how many slots happen to precede it.
- With complete data this yields the same picture an index axis would, `now`
  landing ~11 % from the left. The difference only shows up when the data is
  imperfect, which is exactly when it matters.

Remaining axes:

- **y-axis:** battery level, fixed 0–100 %.
- **Gridlines:** the night target and the minimum reserve, and nothing else.
  Each is labelled inline at the right edge of its own line, so reading the
  chart never requires a legend lookup.
- **x tick labels:** derived by formatting slot timestamps in the local zone, at
  roughly 4–6 h spacing, plus an emphasised `now`. Never by multiplying a slot
  index by 15 minutes — that reintroduces the bug through the back door, and
  breaks outright on a DST day.

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
   using the **six** labels `EnergyStory.tsx` already declares — charge from
   solar, charge from grid, power the house, use solar first, hold, **idle**.

**`idle` is a distinct label and must never be folded into `hold`.** An earlier
draft of this spec said "five labels", which is wrong and actively dangerous to
implement literally. `_action_from_battery` in `ems/web/api.py` classifies
*recorded* slots and returns exactly one of `solar_charge`, `grid_charge`,
`discharge`, or `idle` — where `idle` is the ±50 W dead-band. It never returns
`hold` or `self_consume`; those arise on the planned side. So:

- `idle` is not a rare edge case. It is the **dominant recorded action** for every
  quarter in which the battery is simply quiet, which overnight is most of them.
- `hold` is a deliberate planned intent; `idle` is a measured "nothing much
  happened". Collapsing them would relabel a large share of the recorded timeline
  as an intentional hold the EMS never decided on — inventing intent from a dead
  band, in a chart whose entire job is explaining intent.

Note the existing code is itself inconsistent here — `CombinedPlanChart` carries
`idle` in its pattern map but omits it from its label map, so `idle` slots
currently render a band with no legend entry. The new component fixes that by
labelling all six. Reuse the existing `.seg-<action>` CSS classes from
`styles.css` rather than duplicating their hex values in TSX, so band colours
cannot drift from the stylesheet.

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

- `const [hover, setHover] = useState<number | null>(null)` holding an index into
  the merged, sorted slot array
- `onMouseMove` maps `clientX` through `getBoundingClientRect()` into viewBox
  space, then **inverts the time scale** rather than flooring a slot index:
  `t = t0 + ((px - PAD.l) / PLOT_W) * (t1 - t0)`, and selects the slot whose
  `[start, start + SLOT_MS)` contains `t`. Hovering a gap matches no slot and
  sets `null`, so no tooltip appears over missing data — the same guard
  `EnergyBehavior` expresses as `buckets[i].samples > 0`.
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
interaction that exists nowhere else in the app. The hover tooltip is enrichment,
never the only route to a fact.

That places a hard requirement on the text alternative. Because the action
segments are `aria-hidden` (above), **the accessible summary must convey the
action sequence itself, not merely the chart's title or its totals.** A label
like "Battery plan for the next 24 hours" is not acceptable — it names the chart
without telling anyone what the plan does, which would make the `aria-hidden`
decision a net loss rather than a de-noising.

The summary is generated, not hardcoded: run-length-encode consecutive equal
`action` values into windows, then render each as an action phrase with its local
start and end time and the battery level at the boundaries. `CombinedPlanChart`
already contains an `actionWindows(slots)` helper that performs exactly this
grouping, and `describeCombinedPlan` already builds prose from it — **salvage
both into `PlanStory` before deleting the file** rather than writing a third
implementation.

Target shape, in the ballpark of:

> Battery at 52% now. Powers the house until 02:00, then holds near the 10%
> reserve until 09:15. Charges from solar 09:15 to 13:00, reaching 68%. Powers
> the house through the expensive 17:00–21:00 peak, ending near 25%. Night
> target 88%.

Rendered once as `sr-only` text, with the container's `aria-label` carrying a
one-line condensation of the same content. Gaps in the data are stated ("no
recorded data 04:00–05:30") rather than skipped silently, so the spoken version
has the same honesty about missing data as the drawn version.

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
3. `actionWindows` and `describeCombinedPlan` in `CombinedPlanChart.tsx` are
   **salvaged, not deleted** — they generate the accessible action-sequence
   summary (see Hover/accessibility above). Move them into `PlanStory.tsx` first,
   then check whether any other consumer of the exported `describeCombinedPlan`
   remains, e2e specs included.
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
- **the accessible summary names the actions in order with times**, not just the
  chart title — assert on action wording, since this is the sole route to the
  plan for non-mouse users
- the action band segments are `aria-hidden`, guarding PR #57's decision

The geometry rules carry the highest regression risk and are cheapest to pin
with fixtures rather than a live server. If a component-test runner is added,
cover them there; otherwise assert them via seeded e2e fixtures:

- a slot array with a **hole in the middle** breaks the SoC path, draws no band
  over the hole, and does **not** compress the hole away — the elapsed-time
  distance either side of it is preserved
- **`now` sits at its timestamp's position**, verified with a fixture whose
  recorded slot count deliberately disagrees with the elapsed time (the exact
  case an index axis gets wrong)
- **an overlapping boundary slot** present in both `recent` and `slots` renders
  once, with the recorded value winning
- hovering **over a hole** shows no tooltip

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

1. `GET /api/energy-story` accepts an optional `date`. Bounded store reads,
   DST-correct day boundaries, and the validation contract are specified in the
   three subsections below.
2. Bounded store reads — add an upper bound alongside the existing
   `recent_raw_since` / `recent_derived_since` (either `*_between(start, end)`
   helpers or an optional `until` argument, whichever fits the store's existing
   shape). `_window_price_slots` already takes a start and an end.
3. `_past_story` derives its range from the requested local day's boundaries and
   passes the day's end where it currently passes `now` — so `build_past_story`
   anchors to the day being viewed rather than the wall clock.
4. Omitting `date` preserves today's exact trailing-24 h behaviour, so nothing
   already calling the endpoint changes. This is the regression line that matters
   most: the dashboard from phase 1 keeps calling the endpoint without `date`.

### Day boundaries and DST

A day is a **local calendar day**, and in Europe/Amsterdam a local day is 23, 24
or 25 hours long. Computing a range as "midnight plus 24 hours" is wrong twice a
year: on the March transition it reaches an hour into the next day, and on the
October transition it stops an hour short, silently dropping or stealing slots
from a neighbouring day.

`api.py` already contains the correct idiom, with a comment marking it — reuse it
rather than reinventing:

```python
day_start = datetime(d.year, d.month, d.day, tzinfo=tz)
start_utc = day_start.astimezone(UTC)
end_utc = (day_start + timedelta(days=1)).astimezone(UTC)  # DST-correct next local midnight
```

`timedelta(days=1)` on a `ZoneInfo`-aware datetime advances the wall-clock date
and lets `astimezone` resolve the offset for the new date, which is what makes the
23/25-hour cases come out right. The store is queried in UTC; the local zone is
only ever used to *derive* those bounds. `tz: ZoneInfo` is already plumbed through
`api.py`; confirm it is in `_past_story`'s scope and thread it if not.

Two further consequences:

- **Slot labels come from formatting UTC timestamps into the local zone**, never
  from index arithmetic. On the 25-hour day the local hour 02:00–03:00 occurs
  twice, so two distinct slots legitimately carry the same wall-clock label.
  That is correct and must not be de-duplicated — the frontend's merge step
  de-duplicates on `start`, which is an instant, not a label.
- **Required tests, both transitions:** the March short day yields 23 hours of
  slots, the October long day yields 25, neither leaks a slot into the adjacent
  day, and the repeated local hour produces two slots rather than one.

### Validation contract

- **`date` is typed as `datetime.date`**, not a regex-checked string. FastAPI then
  rejects both malformed input (`"tomorrow"`, `"2026-7-4"`) and well-formed but
  impossible dates (`"2026-02-30"`) with its standard **422** and its standard
  validation-error body. A `pattern=` on a string would accept `2026-02-30` and
  fail later at parse time, which is why the typed parameter is required rather
  than merely preferred.
- **`window=next` with `date`** → explicit **422** via
  `HTTPException(status_code=422, detail=...)`, with a detail that names the
  conflict. Never a silent fallback to the trailing window: silently ignoring a
  parameter the caller supplied is how a caller ends up confidently reading the
  wrong day.
- **Note the shape difference for tests:** FastAPI's own validation failures
  return `detail` as a *list* of error objects, whereas a raised
  `HTTPException` returns `detail` as a *string*. Assert accordingly rather than
  assuming one shape covers both.
- **A valid date with no history** — before recording began, or a future date —
  is **200** with the existing `_empty_story` shape. Absence of data is not a
  client error, and the graph renders its empty state. No special-casing of
  future dates: "no data yet" and "no data any more" are the same answer.
- **`window=past` without `date`** keeps its current meaning, trailing 24 h from
  now.

### Frontend

`PlanStory` gains no new rendering modes — the timestamp-driven solid/dashed rule
already covers an actuals-only window. Insights mounts it inside its own section
(registered with `SectionNav` like the existing panels), fetching
`/api/energy-story?window=past&date=<anchor>` when its section is mounted, reusing
the lazy-fetch pattern the deleted `technicalStory` gate used. Target and reserve
lines still render — for a past day they are exactly the reference you want to
validate the day against, which is why `_past_story` already returns
`target_soc_pct`.

Week/month/year periods do not render the graph: a 15-minute-slot chart over a
year is meaningless, and `_past_story` is day-shaped. Hide it for those periods
rather than degrading it.

**Stepping days re-fetches, and every applied response must be proven current.**
Day stepping is fast and the responses are not uniformly sized, so a slow
response for the day the user just left can land *after* a fast response for the
day they are now on and overwrite it. The graph would then show one day while the
page labels another — the exact incoherence phase 2 exists to avoid, arriving by
a different route.

Insights already has the right idiom; this must follow it rather than inventing a
variant. Its report effect declares `let alive = true`, guards every `setState`
behind `if (alive)`, and returns `() => { alive = false }`, so a response from a
superseded effect run is discarded. Keying the effect on `[period, anchor]` means
changing the day tears down the old guard before the new fetch starts.

Two requirements, not one:

1. **Correctness — the `alive` guard is mandatory**, applied to the success *and*
   failure paths. A stale rejection must not clear a valid current graph.
2. **Hygiene — pass an `AbortController` signal** so the superseded request is
   actually cancelled rather than merely ignored, since rapid day-stepping can
   otherwise leave several full-day queries in flight. If `apiFetch` does not
   forward a `signal`, extend it; do not skip the `alive` guard on the grounds
   that aborting covers it, because an abort landing between response and
   `setState` still needs the guard.

A test must cover the ordering directly: resolve day A's request *after* day B's
and assert the rendered graph is B's.

### Testing

Backend (pytest, no hardware):

- `date` returns that day's slots and not today's
- omitting `date` is unchanged from current behaviour — the phase 1 regression
  guard
- **DST:** the March transition day yields 23 h of slots, the October transition
  day yields 25 h, neither leaks a slot into the adjacent local day, and the
  repeated local hour yields two distinct slots
- **Validation:** malformed `date` → 422; impossible-but-well-formed
  (`2026-02-30`) → 422; `window=next` with `date` → 422 with a string `detail`;
  a valid date with no history → 200 with the empty-story shape; a future date →
  200 empty, not an error

Frontend e2e: the graph appears on Insights for `period=day`, follows the day
stepper, is absent for week/month/year, and — resolving day A's request after
day B's — renders B's data.

## Out of scope

- Any change to planner logic, or to `/api/energy-story`'s `next` window
- The hero card's own copy, confidence chip or warning text
- Making `period=week|month|year` render a slot chart
- Keyboard-driven chart navigation (no precedent in the app; see parity note)
- Re-theming colours or introducing new palette tokens
