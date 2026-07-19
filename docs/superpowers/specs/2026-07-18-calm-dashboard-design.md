# Calm Dashboard and Combined Plan Chart Design

**Date:** 2026-07-18
**Backlog:** B-86 and B-87
**Branch:** `feat/ui-calm-dashboard`

## Outcome

The web Dashboard will answer three questions in a calm, fixed order:

1. Is the home doing the right thing now?
2. What outcomes has it achieved today?
3. Does the next 24-hour plan make sense when price, solar, battery state, and planned actions are read together?

The redesign retains all existing technical evidence. It changes hierarchy and disclosure, not control behavior or API semantics.

## Scope

This slice completes the density audit and hierarchy budget in B-86, then applies it to the Dashboard and Next-24h plan surface in B-87. It does not redesign Insights, Manage, Car, or Chat; those screens receive documented budgets for the later B-88 pass.

There are no battery-control, planner, storage, or backend API changes. The frontend consumes existing live status, report, finance/savings, battery-plan, and energy-story data.

## Dashboard hierarchy

The first viewport follows this order:

1. **Merged hero.** One verdict, one live explanation, the plan-confidence badge, and one explicit action or calm no-action sentence. The live explanation replaces the separate “Right now” card. It may include two compact contextual values when they materially explain the verdict, but it must not become another stat row.
2. **Outcome tiles.** A single row of four tiles:
   - Solar Score for today so far, linked to its existing Insights explanation.
   - Current battery state of charge as a live percentage.
   - Measured savings attributable to the battery and EMS strategy for today so far, linked to finance detail.
   - Total grid-import energy for today so far.
3. **Combined Next-24h chart.** One layered plot containing electricity price, solar forecast, estimated state of charge, and planned battery-action windows.
4. **More from your home.** One disclosure containing strategy controls, manual override, car summary, additional scores, and raw live telemetry.

On desktop the tiles form one row. On phone widths they form a 2-by-2 grid. Missing, stale, or unavailable values render as an em dash with a plain-language explanation; they never silently render as zero.

## Combined Next-24h chart

The chart uses one shared time axis and one plotting area because the primary user question is whether all four signals tell a coherent energy story.

- Electricity price renders as vertical bars.
- Solar forecast renders as a translucent filled area.
- Estimated state of charge renders as the dominant line, with measured and forecast segments visually distinguished when both exist.
- Planned battery actions render as tinted background windows with labels or patterns that remain understandable without color.
- “Now,” target state of charge, reserve floor, and relevant deadline remain available when present, but use subdued styling so they do not compete with the four primary layers.

Pointer hover, keyboard focus, and touch reveal a time-slot readout containing the timestamp, price, solar forecast, estimated state of charge, and planned action. The accessible text alternative summarizes the start, target, minimum/reserve relationship, price peak, solar peak, and principal plan windows.

The old individual charts, exact totals, plan table, provenance, validation messages, and diagnostic evidence remain reachable under one technical-details disclosure. The redesign removes duplication from the initial viewport, not information from the product.

## Components and data flow

The frontend will introduce two reusable presentation boundaries:

- `OutcomeTile` renders a label, value, optional qualifier, unavailable/stale explanation, and optional navigation action.
- `CombinedPlanChart` maps existing Energy Story slots into the four visual layers and a unified interactive readout.

The Dashboard composition owns ordering and disclosure state. It does not recompute domain facts.

- Live state of charge comes from the existing status response.
- Solar Score and total imported grid energy come from today’s report data.
- Savings comes from the existing measured finance/savings data already used by the Dashboard. The tile must preserve the source’s honest semantics and must not relabel a projected value as measured savings.
- Price, solar forecast, estimated state of charge, and plan actions come from the existing Energy Story payload.

No missing series is fabricated. When one chart layer is unavailable, the remaining layers render with a concise explanation naming the missing evidence.

## Information budgets

Density is measured at fixed desktop and phone viewports. Counts apply before any disclosure is expanded.

| Surface | Target budget |
|---|---|
| Dashboard | One merged hero, four outcome tiles, one primary chart, one confidence badge, one action sentence, one detail disclosure |
| Next 24 hours | One verdict, one combined chart, no separate stat grid, one technical-details disclosure |
| Insights | One period summary; no more than three score cards or one detailed visualization per viewport |
| Manage | One selected settings section visible at a time; persistent navigation and save/restart state |
| Car | One plan verdict, up to three supporting facts, one schedule visualization before detail |
| Chat | Conversation is the only primary surface; prompts and diagnostics remain subordinate |

The density harness records visible cards, charts, badges, and numeric values. It reports actual and allowed counts by surface and viewport so a failure explains which budget was exceeded.

## Responsive and interaction behavior

Desktop and tablet layouts keep the four outcome tiles in a single row when space permits. Phone layouts use a 2-by-2 tile grid and preserve readable chart labels. The combined chart does not depend on horizontal page scrolling.

Touch interaction selects a slot and keeps its readout visible until the next selection or dismissal. Keyboard users can move between meaningful time slots and receive the same readout. Pointer hover is an enhancement, never the only access path.

Light and dark themes use the existing design tokens. Plan windows combine tint with labels, borders, or patterns. Reduced-motion preferences disable nonessential chart transitions.

## Error and stale-data behavior

- A missing tile value shows an em dash and a short reason.
- A stale value is labeled stale and retains its last-update context where available.
- A missing chart layer does not remove or rescale unrelated evidence without explanation.
- An absent plan renders the existing safe loading/no-plan explanation, not an empty chart that implies zero activity.
- API errors preserve the Dashboard’s existing unreachable/stale messaging.

These are presentation fallbacks only. They never influence the planner, control loop, battery writes, or safe fallback behavior.

## Verification

- Component tests cover tile source mapping, today-so-far semantics, stale/unavailable states, navigation, chart-layer mapping, and unified slot readouts.
- Playwright covers desktop and phone hierarchy, the 2-by-2 mobile tile layout, disclosure behavior, keyboard chart access, touch selection, and light/dark rendering.
- Accessibility checks cover chart text alternatives, visible focus, non-color-only plan encoding, reduced motion, labels, and WCAG 2.1 AA contrast.
- Density tests assert the documented budgets at fixed desktop and phone viewports.
- Visual-regression screenshots cover normal, missing-data, stale-data, long-label, light-theme, and dark-theme states.
- Existing Dashboard and Energy Story tests are updated rather than discarded, and exact technical evidence remains reachable.
- The production build must remain within the existing 300 KB gzipped bundle budget.

## Non-goals

- Redesigning Insights, Manage, Car, or Chat in this slice.
- Adding new backend aggregation or API endpoints.
- Changing score, savings, forecast, planning, or control calculations.
- Removing technical evidence that the current UI exposes.
- Adding a charting dependency when the existing SVG approach can implement the design within the bundle and accessibility budgets.
