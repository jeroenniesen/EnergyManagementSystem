# Calm Dashboard and Combined Plan Chart Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Redesign the web Dashboard around one merged hero, four today-so-far outcome tiles, and one layered Next-24h chart while preserving technical evidence behind disclosure.

**Architecture:** Keep all domain calculations and API contracts unchanged. Add focused `OutcomeTiles` and `CombinedPlanChart` presentation components, compose them in `App.tsx`, and reuse the existing `EnergyStory` component as the expanded technical view. Extend the Playwright harness with explicit hierarchy and density assertions.

**Tech Stack:** React 18, TypeScript 5.6, SVG, Vite 5, Playwright, FastAPI mock-mode e2e server.

## Global Constraints

- `SPEC.md` remains the source of truth; update its B-32 implemented-reality note deliberately when the new hierarchy ships.
- This is a read-only frontend change: no planner, control-loop, battery-write, storage, or API-semantic changes.
- Missing or stale values render honestly and never silently become zero.
- Initial bundled assets remain at or below 300 KB gzipped.
- Meet WCAG 2.1 AA in light and dark themes and remain responsive to phone width.
- Plan windows require a non-color cue; hover cannot be the only path to slot detail.
- Do not commit unless the user explicitly authorizes it. Stage only named files if authorization is later given.

---

## File structure

- Create `ems/web/frontend/src/OutcomeTiles.tsx`: types, source mapping, and accessible rendering for the four outcome tiles.
- Create `ems/web/frontend/src/CombinedPlanChart.tsx`: layered SVG, chart geometry helpers, slot selection, tooltip/readout, and accessible summary.
- Modify `ems/web/frontend/src/HomeScores.tsx`: widen the shared `Report` type to include the existing report flow fields consumed by outcome tiles.
- Modify `ems/web/frontend/src/App.tsx`: merge live context into the hero; order hero, tiles, chart, and detail; move secondary cards under one disclosure.
- Modify `ems/web/frontend/src/styles.css`: tile grid, combined-chart layers, responsive behavior, focus, patterns, themes, and reduced motion.
- Modify `ems/web/frontend/e2e/ui.spec.ts`: hierarchy, data semantics, interaction, disclosure, and density coverage.
- Modify `SPEC.md`: replace the implemented B-32 dashboard hierarchy note with the approved B-86/B-87 reality.
- Modify `BACKLOG.md`: carry the already-authored E-10 entries into this isolated branch and update B-86/B-87 tracking only after verification.

---

### Task 1: Outcome tiles and today-so-far semantics

**Files:**
- Create: `ems/web/frontend/src/OutcomeTiles.tsx`
- Modify: `ems/web/frontend/src/HomeScores.tsx`
- Modify: `ems/web/frontend/src/styles.css`
- Test: `ems/web/frontend/e2e/ui.spec.ts`

**Interfaces:**
- Consumes: `Report | null`, `SavedToday | null`, `socPct: number | null`, `onOpenInsights(): void`, `onOpenFinance(): void`, and optional battery-detail action.
- Produces: `OutcomeTiles(props): JSX.Element`, with test IDs `outcome-tiles`, `outcome-solar-score`, `outcome-soc`, `outcome-savings`, and `outcome-grid-import`.

- [ ] **Step 1: Add a failing Playwright test for the four metrics**

Add a test that mocks `/api/report?period=day` with `scores: [{key: "self_consumption", label: "Solar score", value: 91, explanation: "..."}]` and `flows: {has_data: true, home_kwh: 5.1, grid_import_kwh: 3.2}`, mocks today’s finance response with `totals.saved_eur: 2.84`, then asserts:

```ts
await expect(page.getByTestId("outcome-tiles")).toBeVisible();
await expect(page.getByTestId("outcome-solar-score")).toContainText("91");
await expect(page.getByTestId("outcome-soc")).toContainText("60%");
await expect(page.getByTestId("outcome-savings")).toContainText("€2.84");
await expect(page.getByTestId("outcome-grid-import")).toContainText("3.2 kWh");
```

- [ ] **Step 2: Run the test and verify the missing component fails**

Run:

```bash
npm run build && npx playwright test e2e/ui.spec.ts --grep "four outcome tiles"
```

Expected: the test fails because `outcome-tiles` does not exist.

- [ ] **Step 3: Widen the shared report type and add the focused tile component**

Change the report flow type to:

```ts
export type Report = {
  partial: boolean;
  flows: {
    has_data: boolean;
    home_kwh?: number;
    grid_import_kwh?: number;
  };
  scores: Score[];
};
```

Implement a private `OutcomeTile` that renders a `<button>` only when it has an action and otherwise renders a `<div>`. Implement `OutcomeTiles` with these exact mappings:

```ts
const solarScore = report?.scores.find((score) => score.key === "self_consumption") ?? null;
const gridImport = report?.flows?.grid_import_kwh;
const savings = savedToday?.status === "measured" ? `€${savedToday.eur.toFixed(2)}` : "—";
```

Use `aria-label` or `title` text to distinguish “today so far,” “live,” “still measuring,” and “not available.” Do not render `0` for `undefined`, `null`, or `savedToday.status === "measuring"`.

- [ ] **Step 4: Add compact desktop and 2-by-2 phone styles**

Add `.outcome-tiles { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); }`, shared tile typography, focus-visible styling, and:

```css
@media (max-width: 640px) {
  .outcome-tiles { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
```

- [ ] **Step 5: Run the focused test and frontend build**

Run:

```bash
npm run build
npx playwright test e2e/ui.spec.ts --grep "four outcome tiles"
```

Expected: build succeeds and the focused test passes.

- [ ] **Step 6: Record the checkpoint without committing**

Run `git diff --check` and `git status --short`. Keep the changes uncommitted unless the user has explicitly authorized commits.

---

### Task 2: Layered combined Next-24h chart

**Files:**
- Create: `ems/web/frontend/src/CombinedPlanChart.tsx`
- Modify: `ems/web/frontend/src/styles.css`
- Test: `ems/web/frontend/e2e/ui.spec.ts`

**Interfaces:**
- Consumes: `story: EnergyStoryData | null` from `EnergyStory.tsx`.
- Produces: `CombinedPlanChart({story}): JSX.Element`, `describeCombinedPlan(story): string`, and test IDs `combined-plan-chart`, `combined-plan-slot`, `combined-plan-readout`, and `combined-plan-missing`.

- [ ] **Step 1: Add failing hierarchy and slot-readout tests**

Mock `/api/energy-story?window=next` with four slots that contain distinct `soc_pct`, `solar_w`, `eur_per_kwh`, and `action` values. Assert one SVG is present and the old separate Dashboard chart is absent. Focus the third `combined-plan-slot` control and assert the readout contains its time, `€0.42/kWh`, solar watts, state of charge, and “Power the house.”

- [ ] **Step 2: Run the focused chart tests and verify failure**

Run:

```bash
npm run build && npx playwright test e2e/ui.spec.ts --grep "combined 24-hour chart|combined slot readout"
```

Expected: failure because `combined-plan-chart` and slot controls do not exist.

- [ ] **Step 3: Implement chart geometry and the four visual layers**

Use the existing `StorySlot` and `EnergyStoryData` exports. Build a single SVG view box with one shared `x()` function. Clamp state of charge to 0–100. Scale price and solar independently, label both scales, and render in this back-to-front order:

```tsx
<g className="combined-plan-windows">{/* action rects + pattern/border */}</g>
<g className="combined-plan-solar">{/* translucent area */}</g>
<g className="combined-plan-prices">{/* price bars */}</g>
<g className="combined-plan-soc">{/* measured solid + forecast dashed */}</g>
<g className="combined-plan-axis">{/* shared time labels + now */}</g>
```

Collapse adjacent slots with the same action into a single background window. Retain separate actual and forecast state-of-charge line styles when `recent` is present. Missing numeric samples create gaps rather than zeros.

- [ ] **Step 4: Implement pointer, touch, and keyboard-equivalent slot selection**

Overlay transparent focusable SVG rectangles for meaningful slots:

```tsx
<rect
  tabIndex={0}
  role="button"
  data-testid="combined-plan-slot"
  aria-label={slotLabel(slot)}
  onFocus={() => setSelectedIndex(index)}
  onPointerEnter={() => setSelectedIndex(index)}
  onClick={() => setSelectedIndex(index)}
/>
```

Render one persistent HTML readout below the SVG for the selected slot. Provide previous/next arrow-key movement without trapping Tab. Do not rely on SVG `<title>` as the only detail surface.

- [ ] **Step 5: Add the accessible summary and missing-layer explanation**

`describeCombinedPlan()` must name the period, starting/ending or target state of charge, reserve relationship, maximum price, maximum solar forecast, and principal action windows when those facts exist. Add it through `aria-label` or a visually hidden description. If price, solar, state of charge, or actions are wholly absent, render the other layers and list the missing layer names in `combined-plan-missing`.

- [ ] **Step 6: Add theme, non-color, mobile, and reduced-motion styles**

Use existing CSS variables. Action windows combine fill with a boundary, text label, or SVG pattern. Ensure focus rectangles have a visible outline. At phone width reduce axis label frequency, not font size below the existing readable scale. Disable transitions under `@media (prefers-reduced-motion: reduce)`.

- [ ] **Step 7: Run focused chart tests and build**

Run:

```bash
npm run build
npx playwright test e2e/ui.spec.ts --grep "combined 24-hour chart|combined slot readout|missing chart layer"
```

Expected: build succeeds and all focused tests pass.

- [ ] **Step 8: Record the checkpoint without committing**

Run `git diff --check` and inspect only the named files with `git diff -- ems/web/frontend/src/CombinedPlanChart.tsx ems/web/frontend/src/styles.css ems/web/frontend/e2e/ui.spec.ts`.

---

### Task 3: Compose the approved Dashboard hierarchy

**Files:**
- Modify: `ems/web/frontend/src/App.tsx`
- Modify: `ems/web/frontend/src/styles.css`
- Test: `ems/web/frontend/e2e/ui.spec.ts`

**Interfaces:**
- Consumes: `OutcomeTiles`, `CombinedPlanChart`, existing Dashboard state, and existing `navigate()`.
- Produces: visible order `home-state`, `outcome-tiles`, `combined-plan-chart`, `home-more`; expanded `home-more-body` retains controls and technical evidence.

- [ ] **Step 1: Rewrite the existing calm-home test as a failing order/budget test**

Assert the three primary surfaces are visible and ordered by comparing their bounding boxes. Assert `home-scores`, `battery-plan`, `strategy-card`, `car-card`, and `advanced-body` are not visible before expansion. Assert `home-more` is visible and collapsed.

- [ ] **Step 2: Add a failing merged-hero test**

Keep the verdict assertion, then assert the synthesis describes the live state and there is no separate “Right now” card. Preserve the confidence and action-line tests.

- [ ] **Step 3: Run the focused Dashboard tests and verify failure**

Run:

```bash
npm run build && npx playwright test e2e/ui.spec.ts --grep "approved dashboard hierarchy|merged hero"
```

Expected: failure because old `home-scores` and `battery-plan` surfaces still precede the new composition.

- [ ] **Step 4: Compose hero, tiles, and chart in App**

Import the new components. Keep `home.headline` as the verdict. Build the merged synthesis from the existing current-state explanation plus on-track message, without inventing new domain claims. Render:

```tsx
<OutcomeTiles
  report={report}
  savedToday={savedToday}
  socPct={status?.soc_pct ?? null}
  onOpenInsights={() => navigate("insights")}
  onOpenFinance={() => navigate("insights")}
  onOpenBattery={batteryHasDetail ? () => setBatteryDetail("soc") : undefined}
/>
<CombinedPlanChart story={story?.window === "next" ? story : null} />
```

The primary chart always requests `window=next`; the expanded technical view may retain its past/next selector through separate technical-view state so selecting “past” cannot replace the Dashboard’s primary Next-24h chart.

- [ ] **Step 5: Replace competing disclosures with one “More from your home” disclosure**

Add `homeMoreOpen` state and persist it under `ems.dash.homeMoreOpen`. Move `StrategyCard`, `OverrideCard`, compact `CarCard`, remaining `HomeScores`, and `Advanced` inside `home-more-body`. Keep the technical `EnergyStory` reachable there, including exact totals, individual charts, provenance, and validation evidence.

- [ ] **Step 6: Preserve alerts and safe-state messaging above the primary hierarchy**

Do not bury critical/warning alerts, fallback state, dry-run/live state, or unreachable API errors. Verify these remain visible before `home-more` because they can change the required action.

- [ ] **Step 7: Run focused hierarchy tests**

Run:

```bash
npm run build
npx playwright test e2e/ui.spec.ts --grep "approved dashboard hierarchy|merged hero|confidence|technical evidence"
```

Expected: all focused tests pass.

- [ ] **Step 8: Record the checkpoint without committing**

Run `git diff --check` and inspect `App.tsx`, `styles.css`, and `ui.spec.ts` diffs for unrelated restructuring.

---

### Task 4: Density harness, responsive verification, and documentation

**Files:**
- Modify: `ems/web/frontend/e2e/ui.spec.ts`
- Modify: `ems/web/frontend/playwright.config.ts` only if named desktop/phone projects are needed by existing conventions
- Modify: `SPEC.md`
- Modify: `BACKLOG.md`

**Interfaces:**
- Consumes: stable `data-density-kind="hero|tile|chart|badge|number|disclosure"` markers on Dashboard primitives.
- Produces: `assertDensityBudget(page, budget)` helper and B-86 desktop/phone assertions with actionable failure output.

- [ ] **Step 1: Add the density helper and failing budget test**

Use a serializable budget:

```ts
const DASHBOARD_BUDGET = {
  hero: 1,
  tile: 4,
  chart: 1,
  badge: 1,
  disclosure: 1,
};
```

Count only visible elements before disclosure expansion. Report each actual count in assertion messages. Add one desktop viewport and one 390-by-844 phone viewport test. Also assert the phone tile grid has two columns.

- [ ] **Step 2: Run density tests and add missing markers/styles until they pass**

Run:

```bash
npm run build
npx playwright test e2e/ui.spec.ts --grep "density budget"
```

Expected: desktop and phone budget tests pass with exactly one hero, four tiles, one primary chart, one confidence badge when supplied, and one home-detail disclosure.

- [ ] **Step 3: Add light/dark and stale/missing-data coverage**

Mock missing report flows, `saved_eur: null`, and missing solar/price chart data. Assert em dashes and explanations are present. Run the same primary hierarchy in light and dark themes. Verify keyboard focus reaches each interactive tile, each chart slot, and the disclosure in reading order.

- [ ] **Step 4: Update SPEC and backlog truthfully**

In `SPEC.md` §9.1, replace the B-32 implemented-reality paragraph with B-86/B-87’s merged hero, four today-so-far outcome tiles, combined chart, and retained technical disclosure. Bring E-10/B-86/B-87 from the main checkout’s current `BACKLOG.md` edit into this branch without overwriting unrelated backlog changes. Mark items done only after the full verification step succeeds.

- [ ] **Step 5: Run full frontend and relevant repository verification**

Run:

```bash
npm run build
npm run test:e2e
git diff --check
```

Expected: TypeScript/Vite build succeeds; all Playwright tests pass; JavaScript and CSS gzip sizes remain below the 300 KB gate; `git diff --check` emits no output.

- [ ] **Step 6: Inspect final scope and working tree**

Run:

```bash
git status --short
git diff --stat
git diff -- SPEC.md BACKLOG.md ems/web/frontend/src ems/web/frontend/e2e/ui.spec.ts
```

Confirm no backend, planner, control, battery, storage, generated browser-companion, `.e2e-data`, or dependency-lock artifacts entered the change.

- [ ] **Step 7: Request code review before completion**

Invoke `superpowers:requesting-code-review`, adversarially verify every candidate finding, fix confirmed issues with a failing test first, then rerun Step 5.

- [ ] **Step 8: Apply verification-before-completion**

Invoke `superpowers:verification-before-completion` and cite the fresh build, e2e, bundle-size, and diff-check output. Leave the branch uncommitted unless the user explicitly authorizes a commit.
