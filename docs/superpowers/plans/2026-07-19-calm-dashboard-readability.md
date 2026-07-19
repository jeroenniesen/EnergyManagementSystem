# Calm Dashboard Readability Follow-up Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the combined Next-24h chart readable by adopting the existing battery-plan chart hierarchy, and prevent raw floating-point precision from leaking into the battery state-of-charge tile.

**Architecture:** Keep the existing `CombinedPlanChart` data mapping, signed-price support, accessibility summary, selection behavior, and one shared time axis. Change only its visual encoding: state of charge owns the plot, solar becomes a subtle background, prices occupy a shallow lower band, and plan actions become a thin ribbon. Round the outcome-tile state of charge at the rendering boundary.

**Tech Stack:** React 18, TypeScript 5.6, SVG, CSS, Vite 5, Playwright.

## Global Constraints

- Frontend/read-only change; do not change planner, control, battery-write, storage, or API semantics.
- Preserve signed negative-price rendering, target/reserve/deadline markers, missing-layer explanations, and pointer/keyboard/touch slot details.
- Preserve WCAG 2.1 AA, light/dark themes, phone responsiveness, and non-color action identification through the legend/readout.
- Initial bundled assets remain at or below 300 KB gzipped.
- Work only on `feat/ui-calm-dashboard` in `.worktrees/ui-calm-dashboard` and update PR #39.

---

### Task 1: Whole-number state-of-charge outcome tile

**Files:**
- Modify: `ems/web/frontend/src/OutcomeTiles.tsx`
- Test: `ems/web/frontend/e2e/ui.spec.ts`

**Interfaces:**
- Consumes: `socPct: number | null`.
- Produces: the existing `outcome-soc` tile, formatted as `${Math.round(socPct)}%` when available.

- [ ] **Step 1: Add the failing precision-regression test**

Mock `/api/status` with `soc_pct: 27.489981785063` and assert:

```ts
const tile = page.getByTestId("outcome-soc");
await expect(tile).toContainText("27%");
await expect(tile).not.toContainText("27.489");
```

- [ ] **Step 2: Run the test and verify RED**

Run:

```bash
npm run build && npx playwright test e2e/ui.spec.ts --grep "whole percentage"
```

Expected: failure because the tile currently interpolates the raw floating-point value.

- [ ] **Step 3: Round only at the presentation boundary**

Change the tile value mapping to:

```ts
value={socPct == null ? "—" : `${Math.round(socPct)}%`}
```

Do not round the underlying state or API value.

- [ ] **Step 4: Run focused GREEN verification**

Run the same focused command. Expected: build succeeds and the precision regression passes.

---

### Task 2: Hierarchical combined-chart rendering

**Files:**
- Modify: `ems/web/frontend/src/CombinedPlanChart.tsx`
- Modify: `ems/web/frontend/src/styles.css`
- Test: `ems/web/frontend/e2e/ui.spec.ts`

**Interfaces:**
- Consumes: the existing `EnergyStoryData` and `StorySlot` contracts.
- Produces: the existing `combined-plan-chart`, slot controls/readout, accessible summary, missing-layer message, and chart markers; no caller changes.

- [ ] **Step 1: Add failing structural hierarchy tests**

Assert the SVG no longer contains full-height action-window rectangles, contains one bottom action ribbon segment per collapsed action window, keeps price bars inside the configured lower band, and starts without a readout:

```ts
await expect(chart.locator(".combined-plan-window")).toHaveCount(0);
await expect(chart.locator(".combined-plan-action-ribbon")).not.toHaveCount(0);
await expect(page.getByTestId("combined-plan-readout")).toHaveCount(0);
```

For each price bar, compare its `y`/`height` against the lower-band bounds rather than asserting visual color. Keep existing signed-negative tests.

- [ ] **Step 2: Run hierarchy tests and verify RED**

Run:

```bash
npm run build && npx playwright test e2e/ui.spec.ts --grep "readable visual hierarchy|signed negative prices|slot readout"
```

Expected: the hierarchy test fails because action windows currently fill the plot, prices use the full plot height, and slot zero is selected by default.

- [ ] **Step 3: Make state of charge the dominant plot series**

Retain the full SoC scale and actual/forecast line geometry. Remove action-window background rectangles, pattern definitions, and plot abbreviations. Initialize `selectedIndex` to `null`.

- [ ] **Step 4: Subordinate solar, price, and plan**

Use constants that make the hierarchy explicit:

```ts
const PRICE_BAND_HEIGHT = 0.24 * plotH;
const ACTION_RIBBON_HEIGHT = 9;
```

Scale signed prices around a zero baseline inside the lower price band. Keep solar as one low-opacity area behind the SoC line. Render collapsed action windows as bottom ribbon rectangles with action-specific classes; put full action names in the legend and slot readout rather than the plot.

- [ ] **Step 5: Add a compact legend**

Render a legend below the SVG for actual SoC, forecast SoC, solar, price, target, reserve, and action ribbon meanings. Preserve non-color identification with text labels and existing accessible slot/readout content.

- [ ] **Step 6: Run focused GREEN tests**

Run the focused hierarchy command. Expected: all selected tests pass, including negative prices, target/deadline evidence, pointer/keyboard/touch details, and initial readout absence.

- [ ] **Step 7: Run full verification and visual inspection**

Run:

```bash
npm run build
npm run test:e2e -- --workers=1
git diff --check
```

Inspect desktop and phone screenshots in light and dark themes. Confirm the SoC line is visually dominant and no text or full-height plan layer obscures it. Record final gzip sizes.

- [ ] **Step 8: Review and update PR #39**

Request code review, fix verified findings test-first, rerun Step 7, then commit only the named follow-up files and push `feat/ui-calm-dashboard`.
