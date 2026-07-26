# PlanStory Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the dashboard’s three competing plan charts with one always-visible, timestamp-accurate `PlanStory` chart for the recorded three-hour context and forward plan.

**Architecture:** Keep the API contract unchanged and move every pure normalization, time-scale, gap, action-window, hover-selection, and accessible-summary rule into a DOM-free `planStoryModel.ts` module covered by Vitest. `PlanStory.tsx` consumes that model and owns only React state plus SVG/HTML rendering, while `App.tsx` keeps the existing `next` story and battery-plan fetches and removes the obsolete dashboard render paths.

**Tech Stack:** React 18, Vite 5, TypeScript 5.6, Vitest 2, SVG, CSS custom properties, Playwright.

## Global Constraints

No backend work in this phase.

The x-axis is a true time scale, not a slot index.

Per-slot marks (price shading, action bands) span `[x(start), x(start + SLOT_MS)]`.

Width comes from the slot's own nominal duration — **not** from `PLOT_W / n`, and **not** from the delta to the next slot.

Gaps render as gaps.

The SoC path breaks across a gap rather than drawing a straight line through missing data.

The solid/dashed split is decided by each slot's own timestamp against `now`, never by which array it arrived in.

Individual band segments carry `aria-hidden="true"` and no `aria-label`.

Do not reintroduce per-segment labels.

The accessible summary must convey the action sequence itself, not merely the chart's title or its totals.

Colours, opacities and the `action` → colour/label mapping are taken verbatim from the existing `EnergyStory` implementation and the app's entity tokens (`--house`, `--car`, `--summer`, `--winter`, `--line`, `--muted`).

This change is a consolidation, not a re-skin: nothing should acquire a new hue.

`.chart-tip*` are already generically named and live in `styles.css` around line 2281, so no new CSS is required for the tooltip itself.

Rows whose value is null are omitted rather than shown as zero.

The hover tooltip is enrichment, never the only route to a fact.

The chart card carries no headline, no status pill, no warning banner.

The chart card's footer keeps only what is not stated elsewhere on the page: saved today, battery percentage, and the existing "see each battery →" link into the per-tower detail.

`GET /api/battery-plan` stays because the hero card's confidence chip depends on it.

History lives on Insights; the dashboard graph stays forward-looking.

---

## File Structure

- Create `ems/web/frontend/src/planStoryModel.ts` — the only home for slot normalization, timestamp geometry, gap detection, SoC/action runs, hover hit-testing, formatted tooltip rows, and accessible prose.
- Create `ems/web/frontend/src/planStoryModel.test.ts` — Vitest coverage for every pure model and geometry invariant.
- Create `ems/web/frontend/src/PlanStory.tsx` — React/SVG rendering, mouse hover state, tooltip markup, and the compact footer.
- Create `ems/web/frontend/src/PlanStory.test.ts` — server-rendered markup tests for layer order, reference labels, action accessibility, and footer contents.
- Modify `ems/web/frontend/package.json` — add the `test` script and Vitest development dependency.
- Modify `ems/web/frontend/package-lock.json` — lock the Vitest dependency tree produced by npm.
- Modify `ems/web/frontend/src/EnergyStory.tsx` — become the shared declaration site for story and battery-plan types, then drop the obsolete dashboard component body.
- Modify `ems/web/frontend/src/OutcomeTiles.tsx` — import `SavedToday` from the shared declaration site.
- Modify `ems/web/frontend/src/App.tsx` — mount `PlanStory`, remove the technical story/disclosure state and render paths, retain the `next` story and battery-plan fetches, and remove BatteryPlan-only car overlay state.
- Modify `ems/web/frontend/src/styles.css` — add `PlanStory` layout/layer styles using existing tokens, retain the shared tooltip/footer rules, and remove styles owned only by deleted components.
- Modify `ems/web/frontend/e2e/ui.spec.ts` — replace removed-component assertions with the phase-1 chart, hover, accessibility, footer, hierarchy, and absence contract.
- Modify `ems/web/frontend/e2e/manage.spec.ts` — update the unknown-hash dashboard assertion to `plan-story`.
- Modify `ems/web/frontend/playwright.config.ts` — update the timezone-determinism comment to name `PlanStory.tsx`.
- Delete `ems/web/frontend/src/CombinedPlanChart.tsx` — only after its `actionWindows` and `describeCombinedPlan` behavior exists in `planStoryModel.ts`.
- Delete `ems/web/frontend/src/BatteryPlan.tsx` — only after all externally consumed types exist in `EnergyStory.tsx`.

### Task 1: Add Vitest, move shared types, and normalize slots

**Files:**
- Create: `ems/web/frontend/src/planStoryModel.ts`
- Create: `ems/web/frontend/src/planStoryModel.test.ts`
- Modify: `ems/web/frontend/package.json`
- Modify: `ems/web/frontend/package-lock.json`
- Modify: `ems/web/frontend/src/EnergyStory.tsx`
- Modify: `ems/web/frontend/src/BatteryPlan.tsx`
- Modify: `ems/web/frontend/src/OutcomeTiles.tsx`
- Test: `ems/web/frontend/src/planStoryModel.test.ts`

**Interfaces:**
- Consumes: `StorySlot = { start: string; soc_pct: number | null; grid_w: number; solar_w: number; battery_w: number; load_w: number; eur_per_kwh: number | null; action: string }`; `StoryTotals = { import_kwh: number; export_kwh: number; solar_kwh: number; charge_kwh: number; discharge_kwh: number; load_kwh: number; grid_cost_eur: number | null; self_sufficiency_pct: number | null; soc_start_pct: number | null; soc_end_pct: number | null; soc_min_pct: number | null; soc_max_pct: number | null }`; `EnergyStoryData = { window: "past" | "next"; now: string; current_soc_pct: number | null; reserve_soc_pct: number; target_soc_pct: number | null; target_kwh: number | null; target_deadline: string | null; current_price_eur_per_kwh: number | null; slots: StorySlot[]; totals: StoryTotals; headline: string; trust_markers?: string[]; recent?: StorySlot[]; recent_hours?: number; on_track?: { status: "ahead" | "on_track" | "behind" | "unknown"; actual_soc_pct: number; target_soc_pct: number; deficit_kwh: number; message: string }; recent_review?: { message: string; solar_actual_kwh: number; solar_forecast_kwh: number | null; solar_pct_of_forecast: number | null } }`.
- Produces: `SLOT_MS: 900000`; `TimedStorySlot = StorySlot & { startMs: number }`; `normaliseSlots(recent: readonly StorySlot[], planned: readonly StorySlot[]): TimedStorySlot[]`; `PlanConfidence = { level: "high" | "medium" | "low"; reasons: string[] }`; `PlanProvenance = { forecast_source: string; solar_confidence_pct: number; planner: "rule_based" | "adaptive" | "summer"; intelligence: { state: string; last_evaluated_at: string | null; last_result: string | null; reason: string } }`; `SavedToday = { status: "measured"; eur: number } | { status: "measuring" }`; `BatteryPlanData = { status: "on_track" | "needs_topup" | "behind_target" | "paused_safely" | "data_stale"; summary: string; current_action: "grid_charge" | "solar_charge" | "hold" | "discharge" | "self_consume" | "paused"; current_reason: string; window_start: string; window_end: string; current_soc_pct: number | null; reserve_soc_pct: number; target_soc_pct: number | null; target_deadline: string | null; planned_grid_topup_kwh?: number; deviation: { status: "ok" | "behind_forecast" | "missing"; message: string }; warnings: string[]; graph: { forecast_soc: Array<{ ts: string; soc_pct: number | null }>; actual_soc: Array<{ ts: string; soc_pct: number | null }>; reserve_line: Array<{ ts: string; soc_pct: number | null }>; target_line: Array<{ ts: string; soc_pct: number | null }>; planned_actions: Array<{ start: string; end: string; action: string }>; price_windows: Array<{ start: string; end: string; min_eur_per_kwh: number; max_eur_per_kwh: number }>; solar: Array<{ ts: string; forecast_w: number; actual_w: number | null }> }; confidence?: PlanConfidence; provenance?: PlanProvenance }`, all exported from `./EnergyStory`.

- [ ] **Step 1: Install the test runner and add the test script**

Run:

```bash
cd ems/web/frontend
npm install --save-dev vitest@^2.1.4
npm pkg set scripts.test="vitest run"
```

The resulting `package.json` entries must be:

```json
{
  "scripts": {
    "dev": "vite",
    "build": "tsc -b && vite build",
    "preview": "vite preview",
    "test": "vitest run",
    "test:e2e": "playwright test"
  },
  "devDependencies": {
    "vitest": "^2.1.4"
  }
}
```

Expected: npm exits 0, `package-lock.json` changes, and no Vitest config file is created. Pure TypeScript tests use Vitest's default Node environment and explicit imports from `vitest`, which is the minimal configuration compatible with the existing Vite setup.

- [ ] **Step 2: Write the failing normalization tests**

Create `ems/web/frontend/src/planStoryModel.test.ts`:

```ts
import { describe, expect, test } from "vitest";

import type { EnergyStoryData, StorySlot, StoryTotals } from "./EnergyStory";
import { normaliseSlots, SLOT_MS } from "./planStoryModel";

export const BASE = Date.parse("2026-07-18T06:00:00Z");

export function storySlot(
  minute: number,
  overrides: Partial<StorySlot> = {},
): StorySlot {
  return {
    start: new Date(BASE + minute * 60_000).toISOString(),
    soc_pct: 50 + minute / 15,
    grid_w: 100,
    solar_w: 400,
    battery_w: 0,
    load_w: 500,
    eur_per_kwh: 0.2,
    action: "hold",
    ...overrides,
  };
}

export const totals: StoryTotals = {
  import_kwh: 1,
  export_kwh: 0,
  solar_kwh: 2,
  charge_kwh: 1,
  discharge_kwh: 1,
  load_kwh: 3,
  grid_cost_eur: 0.3,
  self_sufficiency_pct: 70,
  soc_start_pct: 50,
  soc_end_pct: 70,
  soc_min_pct: 48,
  soc_max_pct: 72,
};

export function storyData(
  recent: StorySlot[],
  slots: StorySlot[],
  nowMinute = 60,
): EnergyStoryData {
  return {
    window: "next",
    now: new Date(BASE + nowMinute * 60_000).toISOString(),
    current_soc_pct: 54,
    reserve_soc_pct: 10,
    target_soc_pct: 88,
    target_kwh: 9,
    target_deadline: new Date(BASE + 180 * 60_000).toISOString(),
    current_price_eur_per_kwh: 0.2,
    recent,
    recent_hours: 3,
    slots,
    totals,
    headline: "The approved hero owns this headline.",
  };
}

describe("normaliseSlots", () => {
  test("parses, merges, sorts, and de-duplicates timestamps", () => {
    const recent = [storySlot(30), storySlot(0)];
    const planned = [storySlot(45), storySlot(15), storySlot(30, { soc_pct: 99 })];

    const result = normaliseSlots(recent, planned);

    expect(result.map((slot) => slot.startMs)).toEqual(
      [0, 15, 30, 45].map((minute) => BASE + minute * 60_000),
    );
    expect(result).toHaveLength(4);
  });

  test("recorded slot wins an overlapping boundary timestamp", () => {
    const recorded = storySlot(60, { soc_pct: 58, action: "hold" });
    const projection = storySlot(60, { soc_pct: 91, action: "grid_charge" });

    const [result] = normaliseSlots([recorded], [projection]);

    expect(result.soc_pct).toBe(58);
    expect(result.action).toBe("hold");
    expect(result.startMs).toBe(BASE + 60 * 60_000);
    expect(SLOT_MS).toBe(15 * 60 * 1000);
  });
});
```

- [ ] **Step 3: Run the tests and observe the missing model**

Run:

```bash
cd ems/web/frontend
npm test -- src/planStoryModel.test.ts
```

Expected: FAIL with `Failed to load url ./planStoryModel` or `Cannot find module './planStoryModel'`.

- [ ] **Step 4: Move the shared battery-plan type declarations without breaking current imports**

Add these declarations after `EnergyStoryData` in `ems/web/frontend/src/EnergyStory.tsx`:

```ts
export type PlanConfidence = {
  level: "high" | "medium" | "low";
  reasons: string[];
};

export type PlanProvenance = {
  forecast_source: string;
  solar_confidence_pct: number;
  planner: "rule_based" | "adaptive" | "summer";
  intelligence: {
    state: string;
    last_evaluated_at: string | null;
    last_result: string | null;
    reason: string;
  };
};

export type SavedToday =
  | { status: "measured"; eur: number }
  | { status: "measuring" };

export type BatteryPlanData = {
  status: "on_track" | "needs_topup" | "behind_target" | "paused_safely" | "data_stale";
  summary: string;
  current_action:
    | "grid_charge"
    | "solar_charge"
    | "hold"
    | "discharge"
    | "self_consume"
    | "paused";
  current_reason: string;
  window_start: string;
  window_end: string;
  current_soc_pct: number | null;
  reserve_soc_pct: number;
  target_soc_pct: number | null;
  target_deadline: string | null;
  planned_grid_topup_kwh?: number;
  deviation: {
    status: "ok" | "behind_forecast" | "missing";
    message: string;
  };
  warnings: string[];
  graph: {
    forecast_soc: Array<{ ts: string; soc_pct: number | null }>;
    actual_soc: Array<{ ts: string; soc_pct: number | null }>;
    reserve_line: Array<{ ts: string; soc_pct: number | null }>;
    target_line: Array<{ ts: string; soc_pct: number | null }>;
    planned_actions: Array<{ start: string; end: string; action: string }>;
    price_windows: Array<{
      start: string;
      end: string;
      min_eur_per_kwh: number;
      max_eur_per_kwh: number;
    }>;
    solar: Array<{ ts: string; forecast_w: number; actual_w: number | null }>;
  };
  confidence?: PlanConfidence;
  provenance?: PlanProvenance;
};
```

Replace the declarations of `PlanConfidence`, `PlanProvenance`, `SavedToday`, and `BatteryPlanData` in `BatteryPlan.tsx` with compatibility imports and re-exports so this commit keeps every current consumer compiling:

```ts
import type {
  BatteryPlanData,
  SavedToday,
} from "./EnergyStory";

export type {
  BatteryPlanData,
  PlanConfidence,
  PlanProvenance,
  SavedToday,
} from "./EnergyStory";
```

Change the first line of `OutcomeTiles.tsx` to:

```ts
import type { SavedToday } from "./EnergyStory";
```

- [ ] **Step 5: Implement the minimal DOM-free normalization model**

Create `ems/web/frontend/src/planStoryModel.ts`:

```ts
import type { StorySlot } from "./EnergyStory";

export const SLOT_MS = 15 * 60 * 1000;

export type TimedStorySlot = StorySlot & { startMs: number };

export function normaliseSlots(
  recent: readonly StorySlot[],
  planned: readonly StorySlot[],
): TimedStorySlot[] {
  const byStart = new Map<number, TimedStorySlot>();

  for (const slot of planned) {
    const startMs = Date.parse(slot.start);
    if (Number.isFinite(startMs)) byStart.set(startMs, { ...slot, startMs });
  }
  for (const slot of recent) {
    const startMs = Date.parse(slot.start);
    if (Number.isFinite(startMs)) byStart.set(startMs, { ...slot, startMs });
  }

  return [...byStart.values()].sort((a, b) => a.startMs - b.startMs);
}
```

This order is explicit rather than load-bearing concatenation: planned entries populate the map first and recorded entries replace collisions second.

- [ ] **Step 6: Run unit tests and the TypeScript build**

Run:

```bash
cd ems/web/frontend
npm test -- src/planStoryModel.test.ts
npm run build
```

Expected: Vitest reports `2 passed`; TypeScript and Vite exit 0 with a generated production bundle.

- [ ] **Step 7: Commit the test runner, types, and normalization seam**

```bash
git add ems/web/frontend/package.json ems/web/frontend/package-lock.json \
  ems/web/frontend/src/EnergyStory.tsx ems/web/frontend/src/BatteryPlan.tsx \
  ems/web/frontend/src/OutcomeTiles.tsx ems/web/frontend/src/planStoryModel.ts \
  ems/web/frontend/src/planStoryModel.test.ts
git commit -m "test: add PlanStory model seam"
```

### Task 2: Encode the timestamp scale and every slot-geometry invariant

**Files:**
- Modify: `ems/web/frontend/src/planStoryModel.ts`
- Modify: `ems/web/frontend/src/planStoryModel.test.ts`
- Test: `ems/web/frontend/src/planStoryModel.test.ts`

**Interfaces:**
- Consumes: `StorySlot = { start: string; soc_pct: number | null; grid_w: number; solar_w: number; battery_w: number; load_w: number; eur_per_kwh: number | null; action: string }`; `SLOT_MS: 900000`; `TimedStorySlot = StorySlot & { startMs: number }`; `normaliseSlots(recent: readonly StorySlot[], planned: readonly StorySlot[]): TimedStorySlot[]`.
- Produces: `TimeScale = { t0: number; t1: number; padLeft: number; plotWidth: number; x: (timeMs: number) => number; invert: (px: number) => number }`; `SlotSpan = { index: number; startMs: number; endMs: number; x: number; width: number }`; `createTimeScale(slots: readonly TimedStorySlot[], padLeft: number, plotWidth: number): TimeScale | null`; `slotSpan(scale: TimeScale, slot: TimedStorySlot, index: number): SlotSpan`; `slotSpans(scale: TimeScale, slots: readonly TimedStorySlot[]): SlotSpan[]`; `findSlotAtTime(slots: readonly TimedStorySlot[], timeMs: number): number | null`; `hoverIndexAtX(scale: TimeScale, slots: readonly TimedStorySlot[], px: number): number | null`; `nowX(scale: TimeScale, now: string): number | null`; `tickTimes(slots: readonly TimedStorySlot[], minimumGapMs?: number): number[]`.

- [ ] **Step 1: Add one failing test per time-scale consequence**

Append inside `planStoryModel.test.ts`, and extend its model import with the named functions used here:

```ts
import {
  createTimeScale,
  findSlotAtTime,
  hoverIndexAtX,
  normaliseSlots,
  nowX,
  slotSpans,
  SLOT_MS,
  tickTimes,
} from "./planStoryModel";

describe("timestamp geometry", () => {
  const geometrySlots = () =>
    normaliseSlots([], [storySlot(0), storySlot(15), storySlot(60)]);

  test("domain starts at the first timestamp and ends one nominal slot after the last", () => {
    const scale = createTimeScale(geometrySlots(), 50, 750)!;
    expect(scale.t0).toBe(BASE);
    expect(scale.t1).toBe(BASE + 75 * 60_000);
  });

  test("domain width is derived from timestamps rather than array length", () => {
    const scale = createTimeScale(geometrySlots(), 50, 750)!;
    expect(scale.x(BASE + 15 * 60_000)).toBeCloseTo(200);
    expect(scale.x(BASE + 60 * 60_000)).toBeCloseTo(650);
  });

  test("every slot mark uses SLOT_MS instead of PLOT_W divided by count", () => {
    const scale = createTimeScale(geometrySlots(), 50, 750)!;
    const spans = slotSpans(scale, geometrySlots());
    expect(spans.map((span) => span.width)).toEqual([150, 150, 150]);
    expect(spans[0].width).not.toBe(750 / 3);
  });

  test("a gap-adjacent mark does not expand to the next timestamp", () => {
    const scale = createTimeScale(geometrySlots(), 50, 750)!;
    const spans = slotSpans(scale, geometrySlots());
    expect(spans[1].x + spans[1].width).toBeCloseTo(350);
    expect(spans[2].x).toBeCloseTo(650);
  });

  test("missing quarters have no price or action span", () => {
    const scale = createTimeScale(geometrySlots(), 50, 750)!;
    const spans = slotSpans(scale, geometrySlots());
    expect(spans).toHaveLength(3);
    expect(spans.some((span) => span.startMs === BASE + 30 * 60_000)).toBe(false);
    expect(spans.some((span) => span.startMs === BASE + 45 * 60_000)).toBe(false);
  });

  test("now uses its timestamp when slot count disagrees with elapsed time", () => {
    const slots = normaliseSlots(
      [storySlot(0), storySlot(15)],
      [storySlot(120), storySlot(135)],
    );
    const scale = createTimeScale(slots, 0, 1000)!;
    expect(nowX(scale, new Date(BASE + 60 * 60_000).toISOString())).toBeCloseTo(400);
    expect(nowX(scale, new Date(BASE + 60 * 60_000).toISOString())).not.toBeCloseTo(500);
  });

  test("time-scale inversion finds a containing slot and returns null over a hole", () => {
    const slots = geometrySlots();
    const scale = createTimeScale(slots, 50, 750)!;
    expect(findSlotAtTime(slots, BASE + 7 * 60_000)).toBe(0);
    expect(hoverIndexAtX(scale, slots, scale.x(BASE + 45 * 60_000))).toBeNull();
  });

  test("tick selection uses timestamp spacing rather than slot indices", () => {
    const slots = normaliseSlots([], [
      storySlot(0),
      storySlot(15),
      storySlot(240),
      storySlot(255),
      storySlot(480),
    ]);
    expect(tickTimes(slots)).toEqual([
      BASE,
      BASE + 240 * 60_000,
      BASE + 480 * 60_000,
    ]);
  });
});
```

- [ ] **Step 2: Run the geometry tests and see the first missing export**

Run:

```bash
cd ems/web/frontend
npm test -- src/planStoryModel.test.ts
```

Expected: FAIL with `createTimeScale is not exported by src/planStoryModel.ts`.

- [ ] **Step 3: Implement the true time scale and hit testing**

Append to `planStoryModel.ts`:

```ts
export type TimeScale = {
  t0: number;
  t1: number;
  padLeft: number;
  plotWidth: number;
  x: (timeMs: number) => number;
  invert: (px: number) => number;
};

export type SlotSpan = {
  index: number;
  startMs: number;
  endMs: number;
  x: number;
  width: number;
};

export function createTimeScale(
  slots: readonly TimedStorySlot[],
  padLeft: number,
  plotWidth: number,
): TimeScale | null {
  if (slots.length === 0) return null;
  const t0 = slots[0].startMs;
  const t1 = slots[slots.length - 1].startMs + SLOT_MS;
  const duration = t1 - t0;
  const x = (timeMs: number) =>
    padLeft + ((timeMs - t0) / duration) * plotWidth;
  const invert = (px: number) =>
    t0 + ((px - padLeft) / plotWidth) * duration;
  return { t0, t1, padLeft, plotWidth, x, invert };
}

export function slotSpan(
  scale: TimeScale,
  slot: TimedStorySlot,
  index: number,
): SlotSpan {
  const x = scale.x(slot.startMs);
  return {
    index,
    startMs: slot.startMs,
    endMs: slot.startMs + SLOT_MS,
    x,
    width: scale.x(slot.startMs + SLOT_MS) - x,
  };
}

export function slotSpans(
  scale: TimeScale,
  slots: readonly TimedStorySlot[],
): SlotSpan[] {
  return slots.map((slot, index) => slotSpan(scale, slot, index));
}

export function findSlotAtTime(
  slots: readonly TimedStorySlot[],
  timeMs: number,
): number | null {
  const index = slots.findIndex(
    (slot) => timeMs >= slot.startMs && timeMs < slot.startMs + SLOT_MS,
  );
  return index < 0 ? null : index;
}

export function hoverIndexAtX(
  scale: TimeScale,
  slots: readonly TimedStorySlot[],
  px: number,
): number | null {
  return findSlotAtTime(slots, scale.invert(px));
}

export function nowX(scale: TimeScale, now: string): number | null {
  const timeMs = Date.parse(now);
  if (!Number.isFinite(timeMs) || timeMs < scale.t0 || timeMs > scale.t1) return null;
  return scale.x(timeMs);
}

export function tickTimes(
  slots: readonly TimedStorySlot[],
  minimumGapMs = 4 * 60 * 60 * 1000,
): number[] {
  const ticks: number[] = [];
  for (const slot of slots) {
    if (ticks.length === 0 || slot.startMs - ticks[ticks.length - 1] >= minimumGapMs) {
      ticks.push(slot.startMs);
    }
  }
  return ticks;
}
```

- [ ] **Step 4: Run every unit test and the build**

Run:

```bash
cd ems/web/frontend
npm test -- src/planStoryModel.test.ts
npm run build
```

Expected: Vitest reports `10 passed`; the build exits 0. No test measures Playwright pixels: all time geometry is proven through pure numeric results.

- [ ] **Step 5: Commit the time-scale contract**

```bash
git add ems/web/frontend/src/planStoryModel.ts \
  ems/web/frontend/src/planStoryModel.test.ts
git commit -m "test: lock PlanStory time geometry"
```

### Task 3: Model gaps, SoC runs, action windows, tooltips, and accessible prose

**Files:**
- Modify: `ems/web/frontend/src/planStoryModel.ts`
- Modify: `ems/web/frontend/src/planStoryModel.test.ts`
- Test: `ems/web/frontend/src/planStoryModel.test.ts`

**Interfaces:**
- Consumes: `StorySlot = { start: string; soc_pct: number | null; grid_w: number; solar_w: number; battery_w: number; load_w: number; eur_per_kwh: number | null; action: string }`; `StoryTotals = { import_kwh: number; export_kwh: number; solar_kwh: number; charge_kwh: number; discharge_kwh: number; load_kwh: number; grid_cost_eur: number | null; self_sufficiency_pct: number | null; soc_start_pct: number | null; soc_end_pct: number | null; soc_min_pct: number | null; soc_max_pct: number | null }`; `EnergyStoryData = { window: "past" | "next"; now: string; current_soc_pct: number | null; reserve_soc_pct: number; target_soc_pct: number | null; target_kwh: number | null; target_deadline: string | null; current_price_eur_per_kwh: number | null; slots: StorySlot[]; totals: StoryTotals; headline: string; trust_markers?: string[]; recent?: StorySlot[]; recent_hours?: number; on_track?: { status: "ahead" | "on_track" | "behind" | "unknown"; actual_soc_pct: number; target_soc_pct: number; deficit_kwh: number; message: string }; recent_review?: { message: string; solar_actual_kwh: number; solar_forecast_kwh: number | null; solar_pct_of_forecast: number | null } }`; `TimedStorySlot = StorySlot & { startMs: number }`; `TimeScale = { t0: number; t1: number; padLeft: number; plotWidth: number; x: (timeMs: number) => number; invert: (px: number) => number }`; `SlotSpan = { index: number; startMs: number; endMs: number; x: number; width: number }`; `normaliseSlots(recent: readonly StorySlot[], planned: readonly StorySlot[]): TimedStorySlot[]`; `createTimeScale(slots: readonly TimedStorySlot[], padLeft: number, plotWidth: number): TimeScale | null`; `slotSpans(scale: TimeScale, slots: readonly TimedStorySlot[]): SlotSpan[]`; `nowX(scale: TimeScale, now: string): number | null`; `tickTimes(slots: readonly TimedStorySlot[], minimumGapMs?: number): number[]`.
- Produces: `PlanAction = "solar_charge" | "grid_charge" | "discharge" | "self_consume" | "hold"`; `ACTION_META: Record<PlanAction, { label: string; phrase: string; color: string }>`; `GapWindow = { start: number; end: number; kind: "recorded" | "forecast" }`; `SocPoint = { timeMs: number; socPct: number }`; `SocRun = { kind: "recorded" | "forecast"; points: SocPoint[] }`; `ActionWindow = { action: PlanAction; start: number; end: number; startSocPct: number | null; endSocPct: number | null }`; `SlotTipRow = { label: string; value: string; color: string }`; `PlanStoryModel = { slots: TimedStorySlot[]; scale: TimeScale; spans: SlotSpan[]; gaps: GapWindow[]; soc: SocRun[]; solar: TimedStorySlot[][]; actions: ActionWindow[]; ticks: number[]; nowMs: number; nowX: number | null; minPrice: number; maxPrice: number; maxSolar: number; summary: string; label: string }`; `canonicalAction(action: string): PlanAction`; `gapWindows(slots: readonly TimedStorySlot[], nowMs: number): GapWindow[]`; `socRuns(slots: readonly TimedStorySlot[], nowMs: number): SocRun[]`; `solarRuns(slots: readonly TimedStorySlot[]): TimedStorySlot[][]`; `actionWindows(slots: readonly TimedStorySlot[]): ActionWindow[]`; `slotTipRows(slot: StorySlot): SlotTipRow[]`; `describeCombinedPlan(story: EnergyStoryData | null): string`; `describeCombinedPlanLabel(story: EnergyStoryData | null): string`; `buildPlanStoryModel(story: EnergyStoryData | null, padLeft: number, plotWidth: number): PlanStoryModel | null`; `formatClock(timeMs: number): string`.

- [ ] **Step 1: Write failing tests for gaps, timestamp-based line styles, action runs, and spoken output**

Append to `planStoryModel.test.ts`, extending its import with the functions below:

```ts
import {
  actionWindows,
  buildPlanStoryModel,
  describeCombinedPlan,
  gapWindows,
  socRuns,
  solarRuns,
  slotTipRows,
} from "./planStoryModel";

describe("gaps and story semantics", () => {
  test("gap detection starts after one nominal slot and preserves the missing interval", () => {
    const slots = normaliseSlots([], [storySlot(0), storySlot(15), storySlot(60)]);
    expect(gapWindows(slots, BASE + 90 * 60_000)).toEqual([
      {
        start: BASE + 30 * 60_000,
        end: BASE + 60 * 60_000,
        kind: "recorded",
      },
    ]);
  });

  test("SoC runs break when timestamp delta exceeds one and a half slots", () => {
    const slots = normaliseSlots([], [storySlot(0), storySlot(15), storySlot(60)]);
    const runs = socRuns(slots, BASE + 90 * 60_000);
    expect(runs.map((run) => run.points.map((point) => point.timeMs))).toEqual([
      [BASE, BASE + 15 * 60_000],
      [BASE + 60 * 60_000],
    ]);
  });

  test("SoC runs also break at a null sample", () => {
    const slots = normaliseSlots([], [
      storySlot(0),
      storySlot(15, { soc_pct: null }),
      storySlot(30),
    ]);
    expect(socRuns(slots, BASE + 90 * 60_000).map((run) => run.points)).toHaveLength(2);
  });

  test("solid and dashed kinds come from timestamps, not source arrays", () => {
    const slots = normaliseSlots(
      [storySlot(45, { soc_pct: 63 })],
      [storySlot(0, { soc_pct: 50 }), storySlot(15, { soc_pct: 55 })],
    );
    expect(socRuns(slots, BASE + 30 * 60_000).map((run) => run.kind)).toEqual([
      "recorded",
      "forecast",
    ]);
  });

  test("contiguous recorded and forecast paths share a join point at now", () => {
    const slots = normaliseSlots([], [
      storySlot(0, { soc_pct: 50 }),
      storySlot(15, { soc_pct: 60 }),
    ]);
    const runs = socRuns(slots, BASE + 15 * 60_000);
    expect(runs).toHaveLength(2);
    expect(runs[0].points.at(-1)?.timeMs).toBe(BASE + 15 * 60_000);
    expect(runs[1].points[0].timeMs).toBe(BASE + 15 * 60_000);
    expect(runs[0].points.at(-1)?.socPct).toBe(runs[1].points[0].socPct);
  });

  test("solar context also splits instead of filling a missing interval", () => {
    const slots = normaliseSlots([], [storySlot(0), storySlot(15), storySlot(60)]);
    expect(solarRuns(slots).map((run) => run.length)).toEqual([2, 1]);
  });

  test("action windows run-length encode contiguous equal actions and stop at a gap", () => {
    const slots = normaliseSlots([], [
      storySlot(0, { action: "hold", soc_pct: 50 }),
      storySlot(15, { action: "idle", soc_pct: 51 }),
      storySlot(60, { action: "hold", soc_pct: 52 }),
      storySlot(75, { action: "solar_charge", soc_pct: 58 }),
    ]);
    expect(actionWindows(slots)).toEqual([
      {
        action: "hold",
        start: BASE,
        end: BASE + 30 * 60_000,
        startSocPct: 50,
        endSocPct: 51,
      },
      {
        action: "hold",
        start: BASE + 60 * 60_000,
        end: BASE + 75 * 60_000,
        startSocPct: 52,
        endSocPct: 58,
      },
      {
        action: "solar_charge",
        start: BASE + 75 * 60_000,
        end: BASE + 90 * 60_000,
        startSocPct: 58,
        endSocPct: 58,
      },
    ]);
  });

  test("accessible prose names ordered actions with times and states a gap aloud", () => {
    const story = storyData(
      [storySlot(0, { action: "discharge", soc_pct: 52 }), storySlot(15, { action: "hold" })],
      [storySlot(60, { action: "solar_charge", soc_pct: 68 })],
      45,
    );
    const summary = describeCombinedPlan(story);
    const dischargeStart = new Date(BASE).toLocaleTimeString([], {
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    });
    const holdStart = new Date(BASE + 15 * 60_000).toLocaleTimeString([], {
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    });

    expect(summary).toContain(`Powers the house ${dischargeStart}`);
    expect(summary).toContain("battery 52%–51%");
    expect(summary).toContain(`Holds ${holdStart}`);
    expect(summary).toContain("No recorded data");
    expect(summary).toContain("Charges from solar");
    expect(summary).toContain("Night target 88%");
  });

  test("tooltip rows omit null facts and never exceed five rows", () => {
    const rows = slotTipRows(
      storySlot(0, { soc_pct: null, eur_per_kwh: null, solar_w: 700, grid_w: -250 }),
    );
    expect(rows.map((row) => row.label)).toEqual(["Solar", "Action", "Grid flow"]);
    expect(rows).toHaveLength(3);
    expect(rows[2].value).toContain("export");
  });

  test("the complete model carries spans, now, ticks, gaps, and both summaries", () => {
    const story = storyData([storySlot(0), storySlot(15)], [storySlot(60)], 30);
    const model = buildPlanStoryModel(story, 58, 880)!;
    expect(model.spans).toHaveLength(3);
    expect(model.nowX).toBeCloseTo(model.scale.x(BASE + 30 * 60_000));
    expect(model.gaps).toHaveLength(1);
    expect(model.summary).toContain("No forecast data");
    expect(model.label).toContain("Hold");
  });
});
```

- [ ] **Step 2: Run the model tests and observe the missing gap export**

Run:

```bash
cd ems/web/frontend
npm test -- src/planStoryModel.test.ts
```

Expected: FAIL with `gapWindows is not exported by src/planStoryModel.ts`.

- [ ] **Step 3: Salvage and extend the action and summary logic in the pure model**

Append to `planStoryModel.ts`:

```ts
import type { EnergyStoryData } from "./EnergyStory";

const GAP_LIMIT_MS = 1.5 * SLOT_MS;

export type PlanAction =
  | "solar_charge"
  | "grid_charge"
  | "discharge"
  | "self_consume"
  | "hold";

export const ACTION_META: Record<
  PlanAction,
  { label: string; phrase: string; color: string }
> = {
  solar_charge: {
    label: "Charge from solar",
    phrase: "Charges from solar",
    color: "var(--accent)",
  },
  grid_charge: {
    label: "Charge from grid",
    phrase: "Charges from grid",
    color: "var(--winter)",
  },
  discharge: {
    label: "Power the house",
    phrase: "Powers the house",
    color: "var(--amber)",
  },
  self_consume: {
    label: "Use solar first",
    phrase: "Uses solar first",
    color: "#2a313c",
  },
  hold: {
    label: "Hold",
    phrase: "Holds",
    color: "#5b6473",
  },
};

export function canonicalAction(action: string): PlanAction {
  if (action === "idle") return "hold";
  if (action in ACTION_META) return action as PlanAction;
  return "hold";
}

export type GapWindow = {
  start: number;
  end: number;
  kind: "recorded" | "forecast";
};

export type SocPoint = { timeMs: number; socPct: number };
export type SocRun = {
  kind: "recorded" | "forecast";
  points: SocPoint[];
};

export type ActionWindow = {
  action: PlanAction;
  start: number;
  end: number;
  startSocPct: number | null;
  endSocPct: number | null;
};

export type SlotTipRow = {
  label: string;
  value: string;
  color: string;
};

export type PlanStoryModel = {
  slots: TimedStorySlot[];
  scale: TimeScale;
  spans: SlotSpan[];
  gaps: GapWindow[];
  soc: SocRun[];
  solar: TimedStorySlot[][];
  actions: ActionWindow[];
  ticks: number[];
  nowMs: number;
  nowX: number | null;
  minPrice: number;
  maxPrice: number;
  maxSolar: number;
  summary: string;
  label: string;
};

function finiteNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

export function formatClock(timeMs: number): string {
  return new Date(timeMs).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

export function gapWindows(
  slots: readonly TimedStorySlot[],
  nowMs: number,
): GapWindow[] {
  const gaps: GapWindow[] = [];
  for (let index = 1; index < slots.length; index += 1) {
    const previousEnd = slots[index - 1].startMs + SLOT_MS;
    if (slots[index].startMs - slots[index - 1].startMs > GAP_LIMIT_MS) {
      gaps.push({
        start: previousEnd,
        end: slots[index].startMs,
        kind: previousEnd < nowMs ? "recorded" : "forecast",
      });
    }
  }
  return gaps;
}

export function socRuns(
  slots: readonly TimedStorySlot[],
  nowMs: number,
): SocRun[] {
  const runs: SocRun[] = [];
  let current: SocRun | null = null;
  let previousStart: number | null = null;

  for (const slot of slots) {
    if (!finiteNumber(slot.soc_pct)) {
      current = null;
      previousStart = slot.startMs;
      continue;
    }
    const kind = slot.startMs < nowMs ? "recorded" : "forecast";
    const separated =
      previousStart != null && slot.startMs - previousStart > GAP_LIMIT_MS;
    const point = { timeMs: slot.startMs, socPct: slot.soc_pct };
    if (!current || separated) {
      current = { kind, points: [point] };
      runs.push(current);
    } else if (current.kind !== kind) {
      const previous = current.points[current.points.length - 1];
      const ratio = (nowMs - previous.timeMs) / (point.timeMs - previous.timeMs);
      const join = {
        timeMs: nowMs,
        socPct: previous.socPct + (point.socPct - previous.socPct) * ratio,
      };
      current.points.push(join);
      current = { kind, points: [join, point] };
      runs.push(current);
    } else {
      current.points.push(point);
    }
    previousStart = slot.startMs;
  }
  return runs;
}

export function solarRuns(
  slots: readonly TimedStorySlot[],
): TimedStorySlot[][] {
  const runs: TimedStorySlot[][] = [];
  for (const slot of slots) {
    if (!finiteNumber(slot.solar_w)) continue;
    const previous = runs.at(-1)?.at(-1);
    if (!previous || slot.startMs - previous.startMs > GAP_LIMIT_MS) runs.push([slot]);
    else runs[runs.length - 1].push(slot);
  }
  return runs;
}
```

- [ ] **Step 4: Add action run-length windows and bounded tooltip rows**

Append to `planStoryModel.ts`:

```ts

export function actionWindows(
  slots: readonly TimedStorySlot[],
): ActionWindow[] {
  const windows: ActionWindow[] = [];
  for (const slot of slots) {
    const action = canonicalAction(slot.action);
    const previous = windows.at(-1);
    if (
      previous?.end === slot.startMs &&
      finiteNumber(slot.soc_pct)
    ) {
      previous.endSocPct = slot.soc_pct;
    }
    if (previous?.action === action && previous.end === slot.startMs) {
      previous.end = slot.startMs + SLOT_MS;
      previous.endSocPct = finiteNumber(slot.soc_pct) ? slot.soc_pct : previous.endSocPct;
    } else {
      windows.push({
        action,
        start: slot.startMs,
        end: slot.startMs + SLOT_MS,
        startSocPct: finiteNumber(slot.soc_pct) ? slot.soc_pct : null,
        endSocPct: finiteNumber(slot.soc_pct) ? slot.soc_pct : null,
      });
    }
  }
  return windows;
}

function watts(value: number): string {
  return `${Math.round(Math.abs(value)).toLocaleString()} W`;
}

export function slotTipRows(slot: StorySlot): SlotTipRow[] {
  const rows: SlotTipRow[] = [];
  if (finiteNumber(slot.soc_pct)) {
    rows.push({ label: "Battery level", value: `${Math.round(slot.soc_pct)}%`, color: "var(--accent)" });
  }
  if (finiteNumber(slot.eur_per_kwh)) {
    rows.push({ label: "Price", value: `€${slot.eur_per_kwh.toFixed(2)}/kWh`, color: "var(--winter)" });
  }
  if (finiteNumber(slot.solar_w)) {
    rows.push({ label: "Solar", value: watts(slot.solar_w), color: "var(--summer)" });
  }
  const action = canonicalAction(slot.action);
  rows.push({ label: "Action", value: ACTION_META[action].label, color: ACTION_META[action].color });
  if (finiteNumber(slot.grid_w)) {
    rows.push({
      label: "Grid flow",
      value: `${watts(slot.grid_w)} ${slot.grid_w >= 0 ? "import" : "export"}`,
      color: "var(--winter)",
    });
  }
  return rows.slice(0, 5);
}
```

- [ ] **Step 5: Add the generated spoken summaries and aggregate model**

Append to `planStoryModel.ts`:

```ts

function actionSentence(window: ActionWindow): string {
  const boundary =
    window.startSocPct != null && window.endSocPct != null
      ? `, battery ${Math.round(window.startSocPct)}%–${Math.round(window.endSocPct)}%`
      : window.endSocPct == null
        ? ""
        : `, ending near ${Math.round(window.endSocPct)}%`;
  return `${ACTION_META[window.action].phrase} ${formatClock(window.start)}–${formatClock(window.end)}${boundary}.`;
}

function gapSentence(gap: GapWindow): string {
  const source = gap.kind === "recorded" ? "recorded" : "forecast";
  return `No ${source} data ${formatClock(gap.start)}–${formatClock(gap.end)}.`;
}

export function describeCombinedPlan(story: EnergyStoryData | null): string {
  if (!story) return "Battery plan is loading.";
  const slots = normaliseSlots(story.recent ?? [], story.slots);
  const nowMs = Date.parse(story.now);
  const current = story.current_soc_pct ?? slots.find((slot) => finiteNumber(slot.soc_pct))?.soc_pct;
  const events: Array<{ start: number; text: string }> = [
    ...actionWindows(slots).map((window) => ({ start: window.start, text: actionSentence(window) })),
    ...gapWindows(slots, nowMs).map((gap) => ({ start: gap.start, text: gapSentence(gap) })),
  ].sort((a, b) => a.start - b.start);
  const parts = [
    finiteNumber(current) ? `Battery at ${Math.round(current)}% now.` : "Battery level is unavailable.",
    ...events.map((event) => event.text),
  ];
  if (finiteNumber(story.target_soc_pct)) {
    const deadline = story.target_deadline ? Date.parse(story.target_deadline) : NaN;
    parts.push(
      `Night target ${Math.round(story.target_soc_pct)}%${
        Number.isFinite(deadline) ? ` by ${formatClock(deadline)}` : ""
      }.`,
    );
  }
  parts.push(`Minimum reserve ${Math.round(story.reserve_soc_pct)}%.`);
  return parts.join(" ");
}

export function describeCombinedPlanLabel(story: EnergyStoryData | null): string {
  if (!story) return "Battery plan is loading.";
  const slots = normaliseSlots(story.recent ?? [], story.slots);
  const nowMs = Date.parse(story.now);
  const events = [
    ...actionWindows(slots).map((window) => ({
      start: window.start,
      text: `${ACTION_META[window.action].label} ${formatClock(window.start)}–${formatClock(window.end)}`,
    })),
    ...gapWindows(slots, nowMs).map((gap) => ({
      start: gap.start,
      text: `no ${gap.kind} data ${formatClock(gap.start)}–${formatClock(gap.end)}`,
    })),
  ].sort((a, b) => a.start - b.start);
  return `Battery plan: ${events.map((event) => event.text).join("; ")}.`;
}

export function buildPlanStoryModel(
  story: EnergyStoryData | null,
  padLeft: number,
  plotWidth: number,
): PlanStoryModel | null {
  if (!story) return null;
  const slots = normaliseSlots(story.recent ?? [], story.slots);
  const scale = createTimeScale(slots, padLeft, plotWidth);
  if (!scale) return null;
  const nowMs = Date.parse(story.now);
  const prices = slots.map((slot) => slot.eur_per_kwh).filter(finiteNumber);
  const solar = slots.map((slot) => slot.solar_w).filter(finiteNumber);
  return {
    slots,
    scale,
    spans: slotSpans(scale, slots),
    gaps: gapWindows(slots, nowMs),
    soc: socRuns(slots, nowMs),
    solar: solarRuns(slots),
    actions: actionWindows(slots),
    ticks: tickTimes(slots),
    nowMs,
    nowX: nowX(scale, story.now),
    minPrice: prices.length ? Math.min(...prices) : 0,
    maxPrice: prices.length ? Math.max(...prices) : 0,
    maxSolar: solar.length ? Math.max(1, ...solar) : 1,
    summary: describeCombinedPlan(story),
    label: describeCombinedPlanLabel(story),
  };
}
```

The `idle` payload value is a compatibility alias for the phase-1 `hold` label, leaving exactly the five required labels while preserving the backend’s current unrestricted `action: string` type.

- [ ] **Step 6: Run the model suite and build**

Run:

```bash
cd ems/web/frontend
npm test -- src/planStoryModel.test.ts
npm run build
```

Expected: Vitest reports `20 passed`; the build exits 0.

- [ ] **Step 7: Commit the salvaged accessible model**

```bash
git add ems/web/frontend/src/planStoryModel.ts \
  ems/web/frontend/src/planStoryModel.test.ts
git commit -m "feat: model PlanStory gaps and actions"
```

### Task 4: Render the unified PlanStory chart and compact footer

**Files:**
- Create: `ems/web/frontend/src/PlanStory.tsx`
- Create: `ems/web/frontend/src/PlanStory.test.ts`
- Modify: `ems/web/frontend/src/styles.css`
- Test: `ems/web/frontend/src/PlanStory.test.ts`

**Interfaces:**
- Consumes: `StorySlot = { start: string; soc_pct: number | null; grid_w: number; solar_w: number; battery_w: number; load_w: number; eur_per_kwh: number | null; action: string }`; `TimedStorySlot = StorySlot & { startMs: number }`; `EnergyStoryData = { window: "past" | "next"; now: string; current_soc_pct: number | null; reserve_soc_pct: number; target_soc_pct: number | null; target_kwh: number | null; target_deadline: string | null; current_price_eur_per_kwh: number | null; slots: StorySlot[]; totals: StoryTotals; headline: string; trust_markers?: string[]; recent?: StorySlot[]; recent_hours?: number; on_track?: { status: "ahead" | "on_track" | "behind" | "unknown"; actual_soc_pct: number; target_soc_pct: number; deficit_kwh: number; message: string }; recent_review?: { message: string; solar_actual_kwh: number; solar_forecast_kwh: number | null; solar_pct_of_forecast: number | null } }`, where `StoryTotals = { import_kwh: number; export_kwh: number; solar_kwh: number; charge_kwh: number; discharge_kwh: number; load_kwh: number; grid_cost_eur: number | null; self_sufficiency_pct: number | null; soc_start_pct: number | null; soc_end_pct: number | null; soc_min_pct: number | null; soc_max_pct: number | null }`; `SavedToday = { status: "measured"; eur: number } | { status: "measuring" }`; `TimeScale = { t0: number; t1: number; padLeft: number; plotWidth: number; x: (timeMs: number) => number; invert: (px: number) => number }`; `SlotSpan = { index: number; startMs: number; endMs: number; x: number; width: number }`; `PlanAction = "solar_charge" | "grid_charge" | "discharge" | "self_consume" | "hold"`; `ACTION_META: Record<PlanAction, { label: string; phrase: string; color: string }>`; `GapWindow = { start: number; end: number; kind: "recorded" | "forecast" }`; `SocPoint = { timeMs: number; socPct: number }`; `SocRun = { kind: "recorded" | "forecast"; points: SocPoint[] }`; `ActionWindow = { action: PlanAction; start: number; end: number; startSocPct: number | null; endSocPct: number | null }`; `SlotTipRow = { label: string; value: string; color: string }`; `PlanStoryModel = { slots: TimedStorySlot[]; scale: TimeScale; spans: SlotSpan[]; gaps: GapWindow[]; soc: SocRun[]; solar: TimedStorySlot[][]; actions: ActionWindow[]; ticks: number[]; nowMs: number; nowX: number | null; minPrice: number; maxPrice: number; maxSolar: number; summary: string; label: string }`; `buildPlanStoryModel(story: EnergyStoryData | null, padLeft: number, plotWidth: number): PlanStoryModel | null`; `canonicalAction(action: string): PlanAction`; `formatClock(timeMs: number): string`; `hoverIndexAtX(scale: TimeScale, slots: readonly TimedStorySlot[], px: number): number | null`; `slotTipRows(slot: StorySlot): SlotTipRow[]`.
- Produces: `PlanStoryProps = { story: EnergyStoryData | null; savedToday?: SavedToday | null; socPct?: number | null; onBatteryClick?: () => void }`; `PlanStory(props: PlanStoryProps): JSX.Element`.

- [ ] **Step 1: Write the failing server-rendered component tests**

Create `ems/web/frontend/src/PlanStory.test.ts`:

```ts
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, test } from "vitest";

import type { EnergyStoryData, StorySlot, StoryTotals } from "./EnergyStory";
import { PlanStory } from "./PlanStory";

const BASE = Date.parse("2026-07-18T06:00:00Z");

function storySlot(minute: number, overrides: Partial<StorySlot> = {}): StorySlot {
  return {
    start: new Date(BASE + minute * 60_000).toISOString(),
    soc_pct: 50 + minute / 15,
    grid_w: 100,
    solar_w: 400,
    battery_w: 0,
    load_w: 500,
    eur_per_kwh: 0.2,
    action: "hold",
    ...overrides,
  };
}

const totals: StoryTotals = {
  import_kwh: 1,
  export_kwh: 0,
  solar_kwh: 2,
  charge_kwh: 1,
  discharge_kwh: 1,
  load_kwh: 3,
  grid_cost_eur: 0.3,
  self_sufficiency_pct: 70,
  soc_start_pct: 50,
  soc_end_pct: 70,
  soc_min_pct: 48,
  soc_max_pct: 72,
};

function storyData(
  recent: StorySlot[],
  slots: StorySlot[],
  nowMinute = 60,
): EnergyStoryData {
  return {
    window: "next",
    now: new Date(BASE + nowMinute * 60_000).toISOString(),
    current_soc_pct: 54,
    reserve_soc_pct: 10,
    target_soc_pct: 88,
    target_kwh: 9,
    target_deadline: new Date(BASE + 180 * 60_000).toISOString(),
    current_price_eur_per_kwh: 0.2,
    recent,
    recent_hours: 3,
    slots,
    totals,
    headline: "The approved hero owns this headline.",
  };
}

describe("PlanStory rendering", () => {
  test("renders the six ordered layers and the text alternative without a second headline", () => {
    const story = storyData(
      [
        storySlot(0, { action: "discharge", soc_pct: 52 }),
        storySlot(15, { action: "hold", soc_pct: 51 }),
      ],
      [
        storySlot(60, { action: "solar_charge", soc_pct: 62 }),
        storySlot(75, { action: "grid_charge", soc_pct: 68 }),
      ],
      45,
    );
    const html = renderToStaticMarkup(createElement(PlanStory, { story }));

    expect(html).toContain('data-testid="plan-story"');
    expect(html.indexOf("plan-story-prices")).toBeLessThan(html.indexOf("plan-story-solar"));
    expect(html.indexOf("plan-story-solar")).toBeLessThan(html.indexOf("plan-story-soc"));
    expect(html.indexOf("plan-story-soc")).toBeLessThan(html.indexOf("plan-story-references"));
    expect(html.indexOf("plan-story-references")).toBeLessThan(html.indexOf("plan-story-now"));
    expect(html.indexOf("plan-story-now")).toBeLessThan(html.indexOf("plan-story-actions"));
    expect(html).toContain('data-testid="plan-story-soc-recorded"');
    expect(html).toContain('data-testid="plan-story-soc-forecast"');
    expect(html).toContain('data-testid="plan-story-target-label"');
    expect(html).toContain('data-testid="plan-story-reserve-label"');
    expect(html).toContain('data-testid="plan-story-action-segment"');
    expect(html).toContain('aria-hidden="true"');
    expect(html).toContain("Powers the house");
    expect(html).toContain("No recorded data");
    expect(html).not.toContain("<h2");
    expect(html).not.toContain("warning");
  });

  test("footer keeps saved today, battery percentage, and the per-tower link only", () => {
    const html = renderToStaticMarkup(createElement(PlanStory, {
      story: storyData([], [storySlot(0)], 0),
      savedToday: { status: "measured", eur: 2.84 },
      socPct: 55,
      onBatteryClick: () => undefined,
    }));

    expect(html).toContain("Saved today");
    expect(html).toContain("€2.84 measured");
    expect(html).toContain("55%");
    expect(html).toContain("see each battery →");
    expect(html).not.toContain(">Mode<");
  });
});
```

- [ ] **Step 2: Run the component test and observe the missing rendering module**

Run:

```bash
cd ems/web/frontend
npm test -- src/PlanStory.test.ts
```

Expected: FAIL with `Failed to load url ./PlanStory` or `Cannot find module './PlanStory'`.

- [ ] **Step 3: Create the React/SVG rendering layer**

Create `ems/web/frontend/src/PlanStory.tsx`:

```tsx
import { useState } from "react";

import type { EnergyStoryData, SavedToday } from "./EnergyStory";
import {
  ACTION_META,
  buildPlanStoryModel,
  canonicalAction,
  formatClock,
  hoverIndexAtX,
  SLOT_MS,
  slotTipRows,
} from "./planStoryModel";

const W = 1000;
const H = 360;
const PAD = { l: 58, r: 62, t: 30, b: 56 };
const PLOT_W = W - PAD.l - PAD.r;
const PLOT_H = H - PAD.t - PAD.b;
const ACTION_Y = PAD.t + PLOT_H + 10;
const ACTION_H = 10;

export type PlanStoryProps = {
  story: EnergyStoryData | null;
  savedToday?: SavedToday | null;
  socPct?: number | null;
  onBatteryClick?: () => void;
};

function Footer({
  savedToday,
  socPct,
  onBatteryClick,
}: Omit<PlanStoryProps, "story">) {
  if (!savedToday && socPct == null) return null;
  return (
    <div className="battery-plan-footer" data-testid="story-footer">
      {savedToday && (
        <span className="bp-foot" data-testid="saved-today" title="Measured vs. a no-battery day.">
          <span className="bp-foot-label">Saved today</span>
          <span className="bp-foot-value">
            {savedToday.status === "measured"
              ? `€${savedToday.eur.toFixed(2)} measured`
              : "€— · measuring"}
          </span>
        </span>
      )}
      {socPct != null && (onBatteryClick ? (
        <button
          type="button"
          className="bp-foot bp-foot-btn"
          data-testid="battery-tile"
          onClick={onBatteryClick}
          title="How full the home battery is — click to see each battery."
        >
          <span className="bp-foot-label">Battery</span>
          <span className="bp-foot-value">{socPct.toFixed(0)}%</span>
          <span className="bp-foot-more">see each battery →</span>
        </button>
      ) : (
        <span className="bp-foot" data-testid="battery-tile" title="How full the home battery is right now.">
          <span className="bp-foot-label">Battery</span>
          <span className="bp-foot-value">{socPct.toFixed(0)}%</span>
        </span>
      ))}
    </div>
  );
}

export function PlanStory({
  story,
  savedToday = null,
  socPct = null,
  onBatteryClick,
}: PlanStoryProps) {
  const [hover, setHover] = useState<number | null>(null);
  const model = buildPlanStoryModel(story, PAD.l, PLOT_W);

  if (!story || !model) {
    return (
      <section className="plan-story" data-testid="plan-story" data-density-kind="chart">
        <p>Battery plan is unavailable.</p>
        <Footer savedToday={savedToday} socPct={socPct} onBatteryClick={onBatteryClick} />
      </section>
    );
  }

  const socY = (value: number) =>
    PAD.t + (1 - Math.max(0, Math.min(100, value)) / 100) * PLOT_H;
  const solarY = (value: number) =>
    PAD.t + PLOT_H - (Math.max(0, value) / model.maxSolar) * PLOT_H * 0.38;
  const priceRange = Math.max(0.01, model.maxPrice - model.minPrice);
  const priceOpacity = (value: number) =>
    0.08 + ((value - model.minPrice) / priceRange) * 0.2;
  const hovered = hover == null ? null : model.slots[hover] ?? null;
  const hoveredX = hovered
    ? model.scale.x(hovered.startMs + SLOT_MS / 2)
    : null;
  const presentActions = [...new Set(model.slots.map((slot) => canonicalAction(slot.action)))];

  const onMove = (event: React.MouseEvent<SVGSVGElement>) => {
    const rect = event.currentTarget.getBoundingClientRect();
    const px = ((event.clientX - rect.left) / rect.width) * W;
    setHover(hoverIndexAtX(model.scale, model.slots, px));
  };

  return (
    <section
      className="plan-story"
      data-testid="plan-story"
      data-density-kind="chart"
      aria-label={model.label}
    >
      <p className="sr-only" data-testid="plan-story-summary">{model.summary}</p>
      <div className="plan-story-chart-wrap">
        <svg
          className="plan-story-svg"
          data-testid="plan-story-plot"
          viewBox={`0 0 ${W} ${H}`}
          aria-hidden="true"
          onMouseMove={onMove}
          onMouseLeave={() => setHover(null)}
        >
          <g className="plan-story-prices" data-testid="plan-story-prices">
            {model.spans.map((span) => {
              const price = model.slots[span.index].eur_per_kwh;
              return typeof price === "number" && Number.isFinite(price) ? (
                <rect
                  key={span.startMs}
                  data-testid="plan-story-price"
                  x={span.x}
                  y={PAD.t}
                  width={span.width}
                  height={PLOT_H}
                  fill="var(--winter)"
                  opacity={priceOpacity(price)}
                />
              ) : null;
            })}
            <text x={W - PAD.r} y={18} textAnchor="end">
              Price €{model.minPrice.toFixed(2)}–€{model.maxPrice.toFixed(2)}
            </text>
          </g>

          <g className="plan-story-solar" data-testid="plan-story-solar">
            {model.solar.map((run, index) => {
              const points = run.map((slot) =>
                `${model.scale.x(slot.startMs + SLOT_MS / 2)},${solarY(slot.solar_w)}`,
              );
              if (points.length === 0) return null;
              const firstX = model.scale.x(run[0].startMs);
              const lastX = model.scale.x(run[run.length - 1].startMs + SLOT_MS);
              return (
                <polygon
                  key={index}
                  points={`${firstX},${PAD.t + PLOT_H} ${points.join(" ")} ${lastX},${PAD.t + PLOT_H}`}
                />
              );
            })}
            <text x={PAD.l} y={18}>Solar 0–{Math.round(model.maxSolar).toLocaleString()} W</text>
          </g>

          <g className="plan-story-soc" data-testid="plan-story-soc">
            {model.soc.map((run, index) => {
              const points = run.points.map((point) =>
                `${model.scale.x(point.timeMs)},${socY(point.socPct)}`,
              ).join(" ");
              return run.points.length > 1 ? (
                <polyline
                  key={`${run.kind}-${index}`}
                  data-testid={`plan-story-soc-${run.kind}`}
                  className={`plan-story-soc-line plan-story-soc-${run.kind}`}
                  points={points}
                />
              ) : (
                <circle
                  key={`${run.kind}-${index}`}
                  data-testid={`plan-story-soc-${run.kind}`}
                  className={`plan-story-soc-dot plan-story-soc-${run.kind}`}
                  cx={model.scale.x(run.points[0].timeMs)}
                  cy={socY(run.points[0].socPct)}
                  r={3}
                />
              );
            })}
          </g>

          <g className="plan-story-references" data-testid="plan-story-references">
            <line x1={PAD.l} x2={W - PAD.r} y1={socY(story.reserve_soc_pct)} y2={socY(story.reserve_soc_pct)} />
            <text
              data-testid="plan-story-reserve-label"
              x={W - PAD.r - 5}
              y={socY(story.reserve_soc_pct) - 5}
              textAnchor="end"
            >
              reserve {Math.round(story.reserve_soc_pct)}%
            </text>
            {story.target_soc_pct != null && (
              <>
                <line
                  className="plan-story-target"
                  x1={PAD.l}
                  x2={W - PAD.r}
                  y1={socY(story.target_soc_pct)}
                  y2={socY(story.target_soc_pct)}
                />
                <text
                  data-testid="plan-story-target-label"
                  x={W - PAD.r - 5}
                  y={socY(story.target_soc_pct) - 5}
                  textAnchor="end"
                >
                  target {Math.round(story.target_soc_pct)}%
                </text>
              </>
            )}
          </g>

          {model.nowX != null && (
            <g className="plan-story-now" data-testid="plan-story-now">
              <line x1={model.nowX} x2={model.nowX} y1={PAD.t} y2={PAD.t + PLOT_H} />
              <text x={model.nowX + 5} y={PAD.t + 14}>now</text>
            </g>
          )}

          <g
            className="plan-story-actions"
            data-testid="plan-story-actions"
          >
            {model.spans.map((span) => {
              const action = canonicalAction(model.slots[span.index].action);
              return (
                <rect
                  key={span.startMs}
                  data-testid="plan-story-action-segment"
                  data-action={action}
                  data-start-ms={span.startMs}
                  aria-hidden="true"
                  x={span.x}
                  y={ACTION_Y}
                  width={span.width}
                  height={ACTION_H}
                  fill={ACTION_META[action].color}
                />
              );
            })}
          </g>

          <g className="plan-story-axis" aria-hidden="true">
            {model.ticks.map((timeMs) => (
              <text key={timeMs} x={model.scale.x(timeMs)} y={H - 4} textAnchor="middle">
                {formatClock(timeMs)}
              </text>
            ))}
          </g>

          {hoveredX != null && (
            <line
              data-testid="plan-story-crosshair"
              x1={hoveredX}
              x2={hoveredX}
              y1={PAD.t}
              y2={PAD.t + PLOT_H}
              stroke="var(--muted)"
              strokeWidth={1}
              strokeDasharray="2 3"
            />
          )}
        </svg>

        {hovered && hoveredX != null && (
          <div
            className="chart-tip"
            style={{ left: `${(hoveredX / W) * 100}%` }}
            data-testid="plan-story-tip"
          >
            <div className="chart-tip-title">
              {formatClock(hovered.startMs)} · {hovered.startMs < model.nowMs ? "recorded" : "forecast"}
            </div>
            {slotTipRows(hovered).map((row) => (
              <div key={row.label} className="chart-tip-row">
                <span className="legend-dot" style={{ background: row.color }} />
                {row.label}
                <span className="chart-tip-val">{row.value}</span>
              </div>
            ))}
          </div>
        )}
      </div>

      <div className="chart-legend" data-testid="plan-story-legend">
        <span className="legend-item"><span className="legend-line plan-story-actual-key" />Recorded battery</span>
        <span className="legend-item"><span className="legend-line plan-story-forecast-key" />Forecast battery</span>
        {presentActions.map((action) => (
          <span className="legend-item" key={action}>
            <span className="legend-dot" style={{ background: ACTION_META[action].color }} />
            {ACTION_META[action].label}
          </span>
        ))}
      </div>

      <Footer savedToday={savedToday} socPct={socPct} onBatteryClick={onBatteryClick} />
    </section>
  );
}
```

- [ ] **Step 4: Add chart styles without duplicating the shared tooltip CSS**

Insert beside the existing combined-chart block in `styles.css`:

```css
/* One continuous recorded → now → forecast story. */
.plan-story {
  margin: 18px 0 0;
  padding: 18px;
  border: 1px solid var(--line);
  border-radius: var(--radius);
  background: var(--panel);
  box-shadow: var(--shadow);
}
.plan-story-chart-wrap { position: relative; }
.plan-story-svg { display: block; width: 100%; height: auto; }
.plan-story-prices text,
.plan-story-solar text,
.plan-story-references text,
.plan-story-now text,
.plan-story-axis text {
  fill: var(--muted);
  font-size: 12px;
}
.plan-story-solar polygon {
  fill: var(--summer);
  stroke: var(--summer);
  stroke-width: 1.5;
  opacity: 0.2;
}
.plan-story-soc-line {
  fill: none;
  stroke-width: 3.5;
  stroke-linecap: round;
  stroke-linejoin: round;
  vector-effect: non-scaling-stroke;
}
.plan-story-soc-recorded { stroke: var(--accent); fill: var(--accent); }
.plan-story-soc-forecast {
  stroke: var(--text);
  fill: var(--text);
  stroke-dasharray: 8 5;
}
.plan-story-references line {
  stroke: var(--muted);
  stroke-width: 1.4;
  stroke-dasharray: 3 5;
}
.plan-story-references .plan-story-target {
  stroke: var(--amber);
  stroke-dasharray: 6 4;
}
.plan-story-now line { stroke: var(--text); stroke-width: 1; opacity: 0.6; }
.plan-story-actions rect { stroke: var(--panel); stroke-width: 1; opacity: 0.9; }
.plan-story-actual-key { border-top-color: var(--accent); }
.plan-story-forecast-key { border-top-color: var(--text); border-top-style: dashed; }
@media (max-width: 520px) {
  .plan-story { padding: 12px; }
  .plan-story-prices text,
  .plan-story-solar text,
  .plan-story-references text,
  .plan-story-now text,
  .plan-story-axis text { font-size: 24px; }
}
@media (prefers-reduced-motion: reduce) {
  .plan-story * { transition: none !important; }
}
```

Do not add or change `.chart-tip`, `.chart-tip-title`, `.chart-tip-row`, `.chart-tip-val`, or `.legend-dot`.

- [ ] **Step 5: Run component tests, all model tests, and the build**

Run:

```bash
cd ems/web/frontend
npm test -- src/planStoryModel.test.ts src/PlanStory.test.ts
npm run build
```

Expected: Vitest reports `22 passed`; the build exits 0.

- [ ] **Step 6: Commit the unmounted rendering layer**

```bash
git add ems/web/frontend/src/PlanStory.tsx \
  ems/web/frontend/src/PlanStory.test.ts \
  ems/web/frontend/src/styles.css
git commit -m "feat: render unified PlanStory chart"
```

### Task 5: Migrate the dashboard atomically, delete old components, and rewrite E2E coverage

**Files:**
- Modify: `ems/web/frontend/src/App.tsx`
- Modify: `ems/web/frontend/src/EnergyStory.tsx`
- Modify: `ems/web/frontend/src/styles.css`
- Modify: `ems/web/frontend/e2e/ui.spec.ts`
- Modify: `ems/web/frontend/e2e/manage.spec.ts`
- Modify: `ems/web/frontend/playwright.config.ts`
- Delete: `ems/web/frontend/src/CombinedPlanChart.tsx`
- Delete: `ems/web/frontend/src/BatteryPlan.tsx`
- Test: `ems/web/frontend/src/planStoryModel.test.ts`
- Test: `ems/web/frontend/src/PlanStory.test.ts`
- Test: `ems/web/frontend/e2e/ui.spec.ts`
- Test: `ems/web/frontend/e2e/manage.spec.ts`

**Interfaces:**
- Consumes: `StorySlot = { start: string; soc_pct: number | null; grid_w: number; solar_w: number; battery_w: number; load_w: number; eur_per_kwh: number | null; action: string }`; `StoryTotals = { import_kwh: number; export_kwh: number; solar_kwh: number; charge_kwh: number; discharge_kwh: number; load_kwh: number; grid_cost_eur: number | null; self_sufficiency_pct: number | null; soc_start_pct: number | null; soc_end_pct: number | null; soc_min_pct: number | null; soc_max_pct: number | null }`; `EnergyStoryData = { window: "past" | "next"; now: string; current_soc_pct: number | null; reserve_soc_pct: number; target_soc_pct: number | null; target_kwh: number | null; target_deadline: string | null; current_price_eur_per_kwh: number | null; slots: StorySlot[]; totals: StoryTotals; headline: string; trust_markers?: string[]; recent?: StorySlot[]; recent_hours?: number; on_track?: { status: "ahead" | "on_track" | "behind" | "unknown"; actual_soc_pct: number; target_soc_pct: number; deficit_kwh: number; message: string }; recent_review?: { message: string; solar_actual_kwh: number; solar_forecast_kwh: number | null; solar_pct_of_forecast: number | null } }`; `PlanStory({ story, savedToday, socPct, onBatteryClick }: PlanStoryProps): JSX.Element`; `PlanStoryProps = { story: EnergyStoryData | null; savedToday?: SavedToday | null; socPct?: number | null; onBatteryClick?: () => void }`; `SavedToday = { status: "measured"; eur: number } | { status: "measuring" }`; `PlanConfidence = { level: "high" | "medium" | "low"; reasons: string[] }`; `PlanProvenance = { forecast_source: string; solar_confidence_pct: number; planner: "rule_based" | "adaptive" | "summer"; intelligence: { state: string; last_evaluated_at: string | null; last_result: string | null; reason: string } }`; `BatteryPlanData = { status: "on_track" | "needs_topup" | "behind_target" | "paused_safely" | "data_stale"; summary: string; current_action: "grid_charge" | "solar_charge" | "hold" | "discharge" | "self_consume" | "paused"; current_reason: string; window_start: string; window_end: string; current_soc_pct: number | null; reserve_soc_pct: number; target_soc_pct: number | null; target_deadline: string | null; planned_grid_topup_kwh?: number; deviation: { status: "ok" | "behind_forecast" | "missing"; message: string }; warnings: string[]; graph: { forecast_soc: Array<{ ts: string; soc_pct: number | null }>; actual_soc: Array<{ ts: string; soc_pct: number | null }>; reserve_line: Array<{ ts: string; soc_pct: number | null }>; target_line: Array<{ ts: string; soc_pct: number | null }>; planned_actions: Array<{ start: string; end: string; action: string }>; price_windows: Array<{ start: string; end: string; min_eur_per_kwh: number; max_eur_per_kwh: number }>; solar: Array<{ ts: string; forecast_w: number; actual_w: number | null }> }; confidence?: PlanConfidence; provenance?: PlanProvenance }`.
- Produces: dashboard order `OutcomeTiles` then `PlanStory` then `home-more`; exactly one visible `[data-density-kind="chart"]`; only `GET /api/energy-story?window=next`; retained `GET /api/battery-plan`; test IDs `plan-story`, `plan-story-plot`, `plan-story-tip`, `plan-story-now`, `plan-story-target-label`, `plan-story-reserve-label`, `plan-story-actions`, `plan-story-action-segment`, `plan-story-summary`, `story-footer`.

- [ ] **Step 1: Replace the old chart fixture and add the failing phase-1 E2E contract**

In `ui.spec.ts`, remove `openPlan`, `DEFAULT_PROVENANCE`, `carWindowFixture`, and `carPlanFixture`. Keep `batteryPlanFixture`, but remove its `provenance` parameter and `provenance` return property. Replace `combinedStory` and the current combined-chart test block with:

```ts
function batteryPlanFixture(confidence: { level: string; reasons: string[] }) {
  const now = new Date();
  return {
    status: "on_track",
    summary: "Next 24h — plan is on track.",
    current_action: "self_consume",
    current_reason: "Battery is following the current plan.",
    window_start: now.toISOString(),
    window_end: new Date(now.getTime() + 24 * 3600e3).toISOString(),
    current_soc_pct: 60,
    reserve_soc_pct: 10,
    target_soc_pct: 88,
    target_deadline: null,
    planned_grid_topup_kwh: 0,
    deviation: { status: "ok", message: "On track." },
    warnings: [],
    graph: {
      forecast_soc: [],
      actual_soc: [],
      reserve_line: [],
      target_line: [],
      planned_actions: [],
      price_windows: [],
      solar: [],
    },
    confidence,
  };
}

const planStoryFixture = () => {
  const base = Date.parse("2026-07-18T06:00:00Z");
  const slot = (
    minute: number,
    soc: number,
    action: string,
    overrides: Record<string, unknown> = {},
  ) => ({
    start: new Date(base + minute * 60_000).toISOString(),
    soc_pct: soc,
    grid_w: 100,
    solar_w: 400,
    battery_w: 0,
    load_w: 500,
    eur_per_kwh: 0.2,
    action,
    ...overrides,
  });
  return {
    window: "next",
    now: new Date(base + 60 * 60_000).toISOString(),
    current_soc_pct: 58,
    reserve_soc_pct: 10,
    target_soc_pct: 88,
    target_kwh: 9,
    target_deadline: new Date(base + 180 * 60_000).toISOString(),
    current_price_eur_per_kwh: 0.2,
    recent_hours: 3,
    recent: [
      slot(0, 52, "discharge"),
      slot(15, 51, "hold"),
      slot(45, 56, "hold"),
      slot(60, 58, "hold"),
    ],
    slots: [
      slot(60, 99, "grid_charge"),
      slot(75, 63, "solar_charge", { solar_w: 1642 }),
      slot(90, 68, "grid_charge", { eur_per_kwh: 0.35 }),
    ],
    totals: {
      import_kwh: 1,
      export_kwh: 0,
      solar_kwh: 2,
      charge_kwh: 1,
      discharge_kwh: 1,
      load_kwh: 3,
      grid_cost_eur: 0.3,
      self_sufficiency_pct: 70,
      soc_start_pct: 52,
      soc_end_pct: 68,
      soc_min_pct: 51,
      soc_max_pct: 68,
    },
    headline: "The hero owns the only headline.",
  };
};

async function routePlanStory(page: Page) {
  await page.route("**/api/energy-story?window=next", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify(planStoryFixture()),
  }));
}

async function moveToViewBoxX(page: Page, x: number) {
  const box = await page.getByTestId("plan-story-plot").boundingBox();
  expect(box).not.toBeNull();
  await page.mouse.move(box!.x + (x / 1000) * box!.width, box!.y + box!.height * 0.45);
}

test("phase 1 renders one PlanStory and removes all competing dashboard charts", async ({ page }) => {
  await routePlanStory(page);
  await page.goto("/");

  await expect(page.getByTestId("plan-story")).toBeVisible();
  await expect(page.getByTestId("combined-plan-chart")).toHaveCount(0);
  await expect(page.getByTestId("battery-plan")).toHaveCount(0);
  await expect(page.getByTestId("plan-disclosure")).toHaveCount(0);
  await expect(page.getByTestId("energy-story")).toHaveCount(0);
  await expect(page.locator('[data-density-kind="chart"]:visible')).toHaveCount(1);
});

test("PlanStory renders mixed time-based layers, references, actions, and honest prose", async ({ page }) => {
  await routePlanStory(page);
  await page.goto("/");
  const chart = page.getByTestId("plan-story");

  await expect(page.getByTestId("plan-story-soc-recorded")).toBeAttached();
  await expect(page.getByTestId("plan-story-soc-forecast")).toBeAttached();
  await expect(page.getByTestId("plan-story-now")).toContainText("now");
  await expect(page.getByTestId("plan-story-target-label")).toHaveText("target 88%");
  await expect(page.getByTestId("plan-story-reserve-label")).toHaveText("reserve 10%");
  await expect(page.getByTestId("plan-story-action-segment")).toHaveCount(6);
  await expect(page.getByTestId("plan-story-action-segment").first()).toHaveAttribute("aria-hidden", "true");
  await expect(page.getByTestId("plan-story-action-segment").first()).not.toHaveAttribute("aria-label");
  await expect(page.getByTestId("plan-story-legend")).toContainText("Power the house");
  await expect(page.getByTestId("plan-story-legend")).toContainText("Hold");
  await expect(page.getByTestId("plan-story-legend")).toContainText("Charge from solar");
  await expect(page.getByTestId("plan-story-legend")).toContainText("Charge from grid");
  await expect(chart).not.toContainText("The hero owns the only headline");

  const spoken = page.getByTestId("plan-story-summary");
  await expect(spoken).toContainText("Powers the house 08:00–08:15");
  await expect(spoken).toContainText("Holds 08:15–08:30");
  await expect(spoken).toContainText("No recorded data 08:30–08:45");
  await expect(spoken).toContainText("Charges from solar 09:15–09:30");
  await expect(spoken).toContainText("Night target 88%");
});

test("mouse hover uses time inversion and clears over a hole or on leave", async ({ page }) => {
  await routePlanStory(page);
  await page.goto("/");

  await moveToViewBoxX(page, 122);
  await expect(page.getByTestId("plan-story-tip")).toContainText("08:00 · recorded");
  await expect(page.getByTestId("plan-story-tip")).toContainText("52%");
  await expect(page.getByTestId("plan-story-crosshair")).toHaveAttribute("stroke-dasharray", "2 3");

  await moveToViewBoxX(page, 327);
  await expect(page.getByTestId("plan-story-tip")).toHaveCount(0);

  await moveToViewBoxX(page, 738);
  await expect(page.getByTestId("plan-story-tip")).toContainText("09:15 · forecast");
  await page.mouse.move(0, 0);
  await expect(page.getByTestId("plan-story-tip")).toHaveCount(0);
});

test("PlanStory footer keeps savings, battery percentage, and the tower detail link", async ({ page }) => {
  await routePlanStory(page);
  await page.route("**/api/finance**", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({ totals: { saved_eur: 2.84 } }),
  }));
  await page.goto("/");

  const footer = page.getByTestId("story-footer");
  await expect(footer).toContainText("Saved today");
  await expect(footer).toContainText("€2.84 measured");
  await expect(footer).toContainText(/\d+%/);
  await expect(footer).toContainText("see each battery →");
  await expect(footer).not.toContainText("Mode");
});
```

- [ ] **Step 2: Run the new E2E test and observe the missing PlanStory mount**

Run:

```bash
cd ems/web/frontend
EMS_E2E_APP_PORT=18117 EMS_E2E_AUTH_PORT=18118 \
  npm run test:e2e -- e2e/ui.spec.ts --project=app \
  -g "phase 1 renders one PlanStory"
```

Expected: FAIL with `getByTestId('plan-story')` not found and the existing `combined-plan-chart` still visible.

- [ ] **Step 3: Replace dashboard imports, state, polling, and rendering in App**

Use these imports in `App.tsx`:

```ts
import type {
  BatteryPlanData,
  EnergyStoryData,
  PlanConfidence,
  SavedToday,
} from "./EnergyStory";
import { PlanStory } from "./PlanStory";
```

Remove the imports of `EnergyStory`, `BatteryPlan`, and `CombinedPlanChart`. Remove `CarPlanWindow`, `CarPlanSummary`, `technicalStory`, `storyWindow`, `carPlan`, `planOpen`, the `ems.dash.planOpen` initializer and persistence effect, `batteryModeLabel`, the gated second story fetch, and the dashboard-level `/api/car/plan` fetch.

Keep these state declarations and fetches:

```ts
const [story, setStory] = useState<EnergyStoryData | null>(null);
const [batteryPlan, setBatteryPlan] = useState<BatteryPlanData | null>(null);
const [savedToday, setSavedToday] = useState<SavedToday | null>(null);

fill("/api/energy-story?window=next", setStory);
fill("/api/battery-plan", setBatteryPlan);
```

Keep the existing hero synthesis use of `story?.on_track?.message`, and move the remaining next-story evidence into that same hero instead of into `PlanStory`:

```tsx
const trustMarkers = (story?.trust_markers ?? []).filter(
  (marker) =>
    !(story?.on_track?.status === "behind" && marker === "No grid top-up needed"),
);

{story?.recent_review?.message && (
  <p className="story-review" data-testid="recent-review">
    {story.recent_review.message}
  </p>
)}
{trustMarkers.length > 0 && (
  <div className="trust-markers" data-testid="trust-markers">
    {trustMarkers.map((marker) => (
      <span key={marker} className="trust-marker">
        <Icon name="check" /> {marker}
      </span>
    ))}
  </div>
)}
```

Place these two blocks inside `data-testid="home-state"` immediately after `hero-synthesis`. This preserves the existing B-31 single-voice filter, makes `on_track`, `recent_review`, and `trust_markers` hero inputs as the data contract requires, and keeps all three out of the chart.

Reduce the dashboard polling effect dependencies to:

```ts
}, [view]);
```

Replace the two top-level dashboard blocks with:

```tsx
{view === "dashboard" && (
  <>
    <OutcomeTiles
      report={report}
      savedToday={savedToday}
      socPct={status?.soc_pct ?? null}
      onOpenInsights={() => navigate("insights")}
      onOpenFinance={() => navigate("insights")}
      onOpenBattery={batteryHasDetail ? () => setBatteryDetail("soc") : undefined}
      freshness={tileFreshness}
    />
    <PlanStory
      story={story?.window === "next" ? story : null}
      savedToday={savedToday}
      socPct={status?.soc_pct ?? null}
      onBatteryClick={batteryHasDetail ? () => setBatteryDetail("soc") : undefined}
    />
  </>
)}
```

Inside `home-more-body`, delete the complete `<BatteryPlan ... />` and `<section className="plan-disclosure" ...>` blocks. Keep `HomeScores`, `StrategyCard`, `OverrideCard`, `CarCard`, and `Advanced` in their existing order.

- [ ] **Step 4: Leave EnergyStory.tsx as the declaration site and delete the obsolete components**

In `EnergyStory.tsx`, keep the exact exported type declarations from Task 1 and remove the `Icon` import, constants, helpers, and `EnergyStory` component body. Then delete:

```bash
rm ems/web/frontend/src/CombinedPlanChart.tsx
rm ems/web/frontend/src/BatteryPlan.tsx
```

The deletes are safe at this point because:

```ts
// Already produced by planStoryModel.ts
export function actionWindows(slots: readonly TimedStorySlot[]): ActionWindow[];
export function describeCombinedPlan(story: EnergyStoryData | null): string;

// Already produced by EnergyStory.tsx
export type SavedToday =
  | { status: "measured"; eur: number }
  | { status: "measuring" };
export type PlanConfidence = {
  level: "high" | "medium" | "low";
  reasons: string[];
};
export type PlanProvenance = {
  forecast_source: string;
  solar_confidence_pct: number;
  planner: "rule_based" | "adaptive" | "summer";
  intelligence: {
    state: string;
    last_evaluated_at: string | null;
    last_result: string | null;
    reason: string;
  };
};
export type BatteryPlanData = {
  status: "on_track" | "needs_topup" | "behind_target" | "paused_safely" | "data_stale";
  summary: string;
  current_action:
    | "grid_charge"
    | "solar_charge"
    | "hold"
    | "discharge"
    | "self_consume"
    | "paused";
  current_reason: string;
  window_start: string;
  window_end: string;
  current_soc_pct: number | null;
  reserve_soc_pct: number;
  target_soc_pct: number | null;
  target_deadline: string | null;
  planned_grid_topup_kwh?: number;
  deviation: {
    status: "ok" | "behind_forecast" | "missing";
    message: string;
  };
  warnings: string[];
  graph: {
    forecast_soc: Array<{ ts: string; soc_pct: number | null }>;
    actual_soc: Array<{ ts: string; soc_pct: number | null }>;
    reserve_line: Array<{ ts: string; soc_pct: number | null }>;
    target_line: Array<{ ts: string; soc_pct: number | null }>;
    planned_actions: Array<{ start: string; end: string; action: string }>;
    price_windows: Array<{
      start: string;
      end: string;
      min_eur_per_kwh: number;
      max_eur_per_kwh: number;
    }>;
    solar: Array<{ ts: string; forecast_w: number; actual_w: number | null }>;
  };
  confidence?: PlanConfidence;
  provenance?: PlanProvenance;
};
```

- [ ] **Step 5: Remove obsolete CSS while preserving shared tooltip and footer rules**

Delete the old `.combined-plan*` block, the `.battery-plan*` graph/status/provenance rules, and `.plan-disclosure` rules. Keep:

```css
.sr-only {
  position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px;
  overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; border: 0;
}
.legend-dot {
  width: 9px; height: 9px; border-radius: 50%; display: inline-block; flex: none;
}
.chart-tip {
  position: absolute; top: 8px; transform: translateX(-50%);
  background: var(--panel-2); border: 1px solid var(--line); border-radius: 10px;
  padding: 8px 10px; font-size: 12px; pointer-events: none; box-shadow: var(--shadow);
  min-width: 130px; z-index: 3;
}
.chart-tip-title { color: var(--muted); margin-bottom: 4px; }
.chart-tip-row { display: flex; align-items: center; gap: 6px; line-height: 1.5; }
.chart-tip-val {
  margin-left: auto; font-variant-numeric: tabular-nums; padding-left: 10px;
}
.battery-plan-footer {
  display: flex; flex-wrap: wrap; gap: 8px 28px;
  margin-top: 14px; padding-top: 12px; border-top: 1px solid var(--line);
}
.bp-foot { display: inline-flex; align-items: baseline; gap: 7px; }
.bp-foot-label {
  font-size: 0.72rem; color: var(--muted);
  text-transform: uppercase; letter-spacing: 0.03em;
}
.bp-foot-value {
  font-size: 0.98rem; font-weight: 600; color: var(--text);
  font-variant-numeric: tabular-nums;
}
.bp-foot-btn {
  border: 0; background: transparent; padding: 0; cursor: pointer; font: inherit; color: inherit;
}
.bp-foot-btn:hover .bp-foot-value { color: var(--accent-text); }
.bp-foot-btn:focus-visible {
  outline: 2px solid var(--accent); outline-offset: 3px; border-radius: 6px;
}
.bp-foot-more { font-size: 0.72rem; color: var(--accent-text); font-weight: 600; }
```

- [ ] **Step 6: Rewrite every remaining removed-component assertion rather than dropping its intent**

Apply these exact selector and behavior replacements in `ui.spec.ts`:

```ts
// Dashboard hierarchy and theme loops
for (const id of [
  "run-mode-badge",
  "data-quality",
  "home-state",
  "outcome-tiles",
  "plan-story",
  "home-more",
  "alerts",
]) {
  await expect(page.getByTestId(id), `panel ${id} should render`).toBeVisible();
}
const ordered = await Promise.all(
  ["home-state", "outcome-tiles", "plan-story", "home-more"]
    .map((id) => page.getByTestId(id).boundingBox()),
);

// Hidden More contents: BatteryPlan and plan-disclosure are absent, not hidden.
for (const id of ["home-scores", "strategy-card", "car-card", "advanced-body"]) {
  await expect(page.getByTestId(id), `panel ${id} should be hidden`).not.toBeVisible();
}
await expect(page.getByTestId("battery-plan")).toHaveCount(0);
await expect(page.getByTestId("plan-disclosure")).toHaveCount(0);

// Mouse-only chart parity: keyboard order contains tiles and disclosure, not SVG slots.
const controls = page.locator([
  '[data-density-kind="tile"]:is(button)',
  '[data-testid="home-more-toggle"]',
].join(", "));
expect(await controls.count()).toBeGreaterThan(1);

// Backend-up smoke check uses the replacement chart.
await expect(page.getByTestId("plan-story")).toBeVisible();
await expect(page.getByTestId("error")).toHaveCount(0);

// Non-dashboard views assert the dashboard chart is absent.
await expect(page.getByTestId("plan-story")).toHaveCount(0);
```

- [ ] **Step 7: Replace removed-chart behavior tests with the phase-1 contracts**

Replace the old technical-story, past-toggle, on-track detail, provenance-footnote, and car-window-overlay test block with these phase-1 tests:

- Rewrite the technical-evidence and disclosure cases into the one-chart, mixed-layer, reference-line, accessible-summary, and footer cases from Step 1.
- Rewrite the past-toggle and B-08 past-marker cases into the next-only request/control-absence case below, because the dashboard no longer owns historical presentation.
- Rewrite the on-track, recent-review, and both B-31 marker cases into the two hero-scoped cases below; keep their honest-marker assertions.
- Rewrite the BatteryPlan provenance/confidence case into the retained battery-plan hero-input case below.
- Rewrite the car-window overlay case into the independent car-card/six-layer boundary case below.

```ts
test("an unavailable story keeps the card honest and draws no invented marks", async ({ page }) => {
  const story = planStoryFixture();
  story.recent = [];
  story.slots = [];
  await page.route("**/api/energy-story?window=next", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify(story),
  }));
  await page.goto("/");
  await expect(page.getByTestId("plan-story")).toContainText("Battery plan is unavailable.");
  await expect(page.getByTestId("plan-story-price")).toHaveCount(0);
  await expect(page.getByTestId("plan-story-action-segment")).toHaveCount(0);
});

test("dashboard fetches only the next story window", async ({ page }) => {
  const storyRequests: string[] = [];
  await page.route("**/api/energy-story**", async (route) => {
    storyRequests.push(route.request().url());
    if (route.request().url().includes("window=next")) {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(planStoryFixture()),
      });
    } else {
      await route.continue();
    }
  });
  await page.goto("/");
  await expect(page.getByTestId("plan-story")).toBeVisible();
  await page.waitForTimeout(100);
  expect(storyRequests.some((url) => url.includes("window=past"))).toBe(false);
  expect(storyRequests.some((url) => url.includes("window=next"))).toBe(true);
  await expect(page.getByTestId("story-past")).toHaveCount(0);
  await expect(page.getByTestId("story-next")).toHaveCount(0);
  await expect(page.getByTestId("quiet-marker-night")).toHaveCount(0);
  await expect(page.getByTestId("quiet-marker-cheap")).toHaveCount(0);
});
```

- [ ] **Step 8: Rewrite the next-story evidence tests against the hero**

Append to the same dashboard `describe` block in `ui.spec.ts`:

```ts

test("next-story verdict, review, and honest trust markers stay in the hero", async ({ page }) => {
  const story = planStoryFixture();
  Object.assign(story, {
    on_track: {
      status: "behind",
      actual_soc_pct: 58,
      target_soc_pct: 88,
      deficit_kwh: 6.2,
      message: "Short of the 88% target with no grid top-up planned.",
    },
    recent_review: {
      message: "Last 3h solar reached 80% of forecast.",
      solar_actual_kwh: 3.2,
      solar_forecast_kwh: 4,
      solar_pct_of_forecast: 80,
    },
    trust_markers: ["Reserve respected", "No grid top-up needed"],
  });
  await page.route("**/api/energy-story?window=next", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify(story),
  }));
  await page.goto("/");

  const hero = page.getByTestId("home-state");
  await expect(hero.getByTestId("hero-synthesis")).toContainText("no grid top-up planned");
  await expect(hero.getByTestId("recent-review")).toContainText("80% of forecast");
  await expect(hero.getByTestId("trust-markers")).toContainText("Reserve respected");
  await expect(hero.getByTestId("trust-markers")).not.toContainText("No grid top-up needed");
  await expect(page.getByText("No grid top-up needed")).toHaveCount(0);
  await expect(page.getByTestId("plan-story-soc-recorded")).toBeAttached();
  await expect(page.getByTestId("plan-story-soc-forecast")).toBeAttached();
});

test("an on-track story keeps its non-redundant comfort marker in the hero", async ({ page }) => {
  const story = planStoryFixture();
  Object.assign(story, {
    on_track: {
      status: "ahead",
      actual_soc_pct: 58,
      target_soc_pct: 88,
      deficit_kwh: 0,
      message: "On track for tonight's target.",
    },
    trust_markers: ["Reserve respected", "No grid top-up needed"],
  });
  await page.route("**/api/energy-story?window=next", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify(story),
  }));
  await page.goto("/");

  const markers = page.getByTestId("home-state").getByTestId("trust-markers");
  await expect(markers).toContainText("Reserve respected");
  await expect(markers).toContainText("No grid top-up needed");
});
```

- [ ] **Step 9: Rewrite retained data-source and navigation boundary tests**

Append to the same dashboard `describe` block in `ui.spec.ts`:

```ts

test("battery-plan data still supplies the hero reason and confidence", async ({ page }) => {
  await page.route("**/api/battery-plan", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify(batteryPlanFixture({
      level: "medium",
      reasons: ["Still learning your roof."],
    })),
  }));
  await page.goto("/");
  await expect(page.getByTestId("hero-synthesis")).toContainText(
    "Battery is following the current plan.",
  );
  await expect(page.getByTestId("confidence-chip")).toHaveText("Medium confidence");
  await expect(page.getByTestId("battery-plan")).toHaveCount(0);
});

test("car advice stays in its own card instead of adding a sixth chart layer", async ({ page }) => {
  await routePlanStory(page);
  await page.goto("/");
  await openMore(page);
  await expect(page.getByTestId("plan-story").locator('[data-testid="bp-car-window"]')).toHaveCount(0);
  await expect(page.locator('[data-testid="car-card"], [data-testid="car-card-disabled"]')).toBeVisible();
});

test("deeper numbers remain available through Insights and All the details", async ({ page }) => {
  await page.goto("/");
  await page.getByTestId("nav-insights").click();
  await expect(page.getByTestId("insights")).toBeVisible();
  await page.getByTestId("nav-dashboard").click();
  await openAdvanced(page);
  await expect(page.getByTestId("detail-grid")).toBeVisible();
});
```

Update comments that name the deleted chart components to say `PlanStory`, `plan action strip`, or `per-slot action` as applicable.

- [ ] **Step 10: Update the other exact source references**

In `manage.spec.ts`, change:

```ts
await expect(page.getByTestId("plan-story")).toBeVisible();
```

In `playwright.config.ts`, replace the three-line timezone comment with:

```ts
// CI runners are UTC; local machines may differ. PlanStory.tsx formats timestamp-derived labels in
// the browser's local zone, and ui.spec.ts asserts deterministic local-time action windows.
// Pin the browser zone so those assertions describe the same instants on every host.
```

- [ ] **Step 11: Prove no removed selector or import survives**

Run:

```bash
rg -n 'CombinedPlanChart|<BatteryPlan|from "./BatteryPlan"|combinedStory|combined-plan-chart|data-testid="battery-plan"|plan-disclosure|openPlan|technicalStory|storyWindow|planOpen' \
  ems/web/frontend/src ems/web/frontend/e2e ems/web/frontend/playwright.config.ts
```

Expected: no matches and exit status 1.

- [ ] **Step 12: Run the full phase-1 verification matrix**

Run:

```bash
cd ems/web/frontend
npm test
npm run build
EMS_E2E_APP_PORT=18117 EMS_E2E_AUTH_PORT=18118 \
  npm run test:e2e -- e2e/ui.spec.ts e2e/manage.spec.ts --project=app
```

Expected: Vitest exits 0 with all 22 unit/component tests passing; TypeScript and Vite exit 0; Playwright exits 0 with no failed tests. The Playwright configuration supplies fresh `EMS_DB_PATH` values under `.e2e-data/` and the explicit ports prevent collision with the default 8099/8100 pair.

- [ ] **Step 13: Commit the atomic dashboard migration**

```bash
git add ems/web/frontend/src/App.tsx ems/web/frontend/src/EnergyStory.tsx \
  ems/web/frontend/src/styles.css ems/web/frontend/e2e/ui.spec.ts \
  ems/web/frontend/e2e/manage.spec.ts ems/web/frontend/playwright.config.ts
git add -u ems/web/frontend/src/CombinedPlanChart.tsx \
  ems/web/frontend/src/BatteryPlan.tsx
git commit -m "feat: replace dashboard plans with PlanStory"
```

## Self-Review

### Spec coverage

| Phase-1 requirement | Implemented and proven in |
| --- | --- |
| One always-visible chart directly after `OutcomeTiles` | Task 5 App composition and E2E one-chart test |
| No backend or endpoint contract change | Global Constraints; Task 5 retains both required fetches |
| Merge, parse, sort, and de-duplicate | Task 1 `normaliseSlots` |
| Recorded value wins a boundary collision | Task 1 recorded-wins unit test |
| Domain `t0`/`t1` from timestamps | Task 2 domain tests |
| Final slot receives nominal width | Task 2 domain-end and slot-width tests |
| True time scale, never slot index | Task 2 numeric scale tests |
| Slot width uses `SLOT_MS`, not count or next delta | Task 2 two separate width tests |
| Missing price/action quarters draw no mark | Task 2 missing-span test; Task 4 maps only `model.spans` |
| SoC line breaks after `1.5 × SLOT_MS` and null | Task 3 separate unit tests |
| Recorded/forecast style comes from each timestamp | Task 3 source-array inversion test; Task 4 solid/dashed paths |
| Contiguous recorded and forecast SoC paths meet at the interpolated `now` point | Task 3 shared-boundary unit test and `socRuns` |
| `now` positioned from `story.now` with disagreeing count fixture | Task 2 explicit 40% versus 50% test |
| Local timestamp x ticks at roughly four-hour spacing | Task 2 timestamp tick test; Task 4 formats `model.ticks` |
| Fixed 0–100% y-axis | Task 4 `socY` |
| Target and reserve lines with inline right labels | Task 4 markup test; Task 5 E2E assertions |
| Price full-height per-slot shading | Task 4 price layer |
| Solar soft filled context area | Task 3 `solarRuns`; Task 4 solar layer |
| Six-layer back-to-front order | Task 4 server-rendered order assertion |
| One action band per slot and five displayed labels | Task 3 canonical five-action contract; Task 4 action spans and legend |
| Action segments `aria-hidden`, no per-segment label | Task 4 markup; Task 5 E2E assertions |
| Existing palette only | Global Constraints; Tasks 3–4 use existing CSS variables |
| Solar and price captions preserve ranges | Task 4 `Solar 0–… W` and `Price €…–€…` text |
| Hover state is an index into merged slots | Task 4 `hover: number | null` |
| Mouse x maps through the SVG bounding box and inverted time scale | Tasks 2 and 4 |
| Hover over a hole returns null | Task 2 unit test; Task 5 mouse E2E |
| Mouse leave clears tooltip | Task 4 handler; Task 5 E2E |
| Dashed muted crosshair | Task 4 markup; Task 5 E2E |
| Existing tooltip class reuse and at most five non-null rows | Task 3 row test; Task 4 markup and CSS instruction |
| Mouse-only parity, tooltip never sole information route | Global Constraints; Task 5 keyboard-test rewrite and summary tests |
| Generated action sequence with local times and boundary SoC | Task 3 `actionWindows` and `describeCombinedPlan` |
| Spoken gaps | Task 3 summary test; Task 5 E2E |
| `sr-only` summary plus condensed container label | Tasks 3–4 |
| No chart headline, status pill, or warning | Task 4 server-rendered assertion |
| Footer saved amount, battery percentage, per-tower link only | Task 4 footer test; Task 5 E2E |
| `EnergyStory` removed from dashboard; past window state/fetch removed | Task 5 App migration and request test |
| `on_track`, `recent_review`, and `trust_markers` remain hero inputs, never chart inputs | Task 5 hero migration and two B-31 E2E rewrites |
| `SavedToday`, `PlanConfidence`, `BatteryPlanData` moved before deletion | Task 1 move and compatibility re-export; Task 5 delete |
| `/api/battery-plan` remains for hero confidence and reason | Task 5 retained fetch and hero E2E |
| `actionWindows` and `describeCombinedPlan` salvaged before deletion | Task 3 model exports; Task 5 delete |
| Removed component/disclosure specs rewritten | Task 5 full selector audit and replacement tests |
| Vitest dependency and `test` script | Task 1 |
| Pure model has no React or DOM imports | Tasks 1–3 |
| Isolated Playwright port and throwaway database | Task 5 verification command and existing Playwright environment |

### Author verification record

- Spec coverage review: every paragraph from Phase 1 through Testing maps to the table above. The initial draft omitted the local timestamp tick rule, the contiguous recorded/forecast join at the interpolated `now` point, the migration of `recent_review` and filtered `trust_markers` into the hero, and the removal of the BatteryPlan-only car overlay poll; those gaps are now explicit in Tasks 2, 3, and 5.
- Placeholder scan: zero matches for every forbidden placeholder stem named in the authoring request.
- Type consistency: `normaliseSlots`, `createTimeScale`, `slotSpan`, `slotSpans`, `findSlotAtTime`, `hoverIndexAtX`, `nowX`, `tickTimes`, `canonicalAction`, `gapWindows`, `socRuns`, `solarRuns`, `actionWindows`, `slotTipRows`, `describeCombinedPlan`, `describeCombinedPlanLabel`, `buildPlanStoryModel`, `formatClock`, `PlanStoryProps`, and `PlanStory` have one spelling and one signature across all producer/consumer blocks.
- Repository verification: every listed path exists at authoring time. `App.tsx` contains the two dashboard blocks and all named state/fetch symbols; tooltip classes exist in `styles.css`; shared types exist in the source components; `ui.spec.ts` contains every removed-selector family; `manage.spec.ts` and `playwright.config.ts` each contain one stale combined-chart reference.
- Spec/code differences recorded: the design says both old charts position by index, but `CombinedPlanChart.tsx` already uses a timestamp scale for marks and `now`; its remaining defects are separate arrays, no collision handling, no timestamp-gap SoC break, and index-selected ticks. The design names five action labels, while `EnergyStory.tsx` also declares `Idle`; this plan treats `idle` as the existing hold behavior so the replacement exposes exactly the five specified labels. The design says `on_track`, `recent_review`, and `trust_markers` are hero inputs, but `App.tsx` currently uses only `on_track.message`; Task 5 moves the review and filtered trust markers into the hero before deleting their old renderer. The design says the old E2E comment names `CombinedPlanChart`, while the source comment says “The combined chart is the visible primary plan.” The user’s explicit model split supersedes the design’s instruction to place salvaged pure helpers in `PlanStory.tsx`.
- Scope check: every task is frontend-only, uses the existing unparameterized `next` endpoint, and excludes all work outside the approved dashboard phase.
