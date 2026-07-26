import { describe, expect, test } from "vitest";

import type { EnergyStoryData, StorySlot, StoryTotals } from "./EnergyStory";
import {
  actionWindows,
  buildPlanStoryModel,
  createTimeScale,
  describeCombinedPlan,
  findSlotAtTime,
  gapWindows,
  hoverIndexAtX,
  normaliseSlots,
  nowX,
  socRuns,
  solarRuns,
  slotSpans,
  slotTipRows,
  SLOT_MS,
  tickTimes,
} from "./planStoryModel";

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
    expect(spans[0].startMs).toBe(BASE);
    expect(spans[0].x).toBe(scale.x(BASE));
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

  test("recorded SoC points use slot midpoints and break across a gap", () => {
    const slots = normaliseSlots([], [storySlot(0), storySlot(15), storySlot(60)]);
    const runs = socRuns(slots, BASE + 90 * 60_000);
    expect(runs.map((run) => run.points.map((point) => point.timeMs))).toEqual([
      [BASE + SLOT_MS / 2, BASE + 15 * 60_000 + SLOT_MS / 2],
      [BASE + 60 * 60_000 + SLOT_MS / 2],
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

  test("forecast SoC points use slot ends while the recorded-to-forecast join stays continuous and monotonic", () => {
    const slots = normaliseSlots([], [
      storySlot(0, { soc_pct: 50 }),
      storySlot(15, { soc_pct: 60 }),
    ]);
    const runs = socRuns(slots, BASE + 15 * 60_000);
    expect(runs).toHaveLength(2);
    expect(runs[0].points.at(-1)?.timeMs).toBe(BASE + 15 * 60_000);
    expect(runs[1].points[0].timeMs).toBe(BASE + 15 * 60_000);
    expect(runs[1].points.at(-1)?.timeMs).toBe(BASE + 15 * 60_000 + SLOT_MS);
    expect(runs[0].points.at(-1)?.socPct).toBe(runs[1].points[0].socPct);
    const joinedTimes = runs.flatMap((run, index) =>
      run.points.slice(index === 0 ? 0 : 1).map((point) => point.timeMs),
    );
    expect(joinedTimes.every((timeMs, index) =>
      index === 0 || timeMs >= joinedTimes[index - 1],
    )).toBe(true);
  });

  test("a partial recorded slot cannot make the join at now run backward in time", () => {
    const slots = normaliseSlots([], [
      storySlot(0, { soc_pct: 50 }),
      storySlot(15, { soc_pct: 60 }),
    ]);
    const runs = socRuns(slots, BASE + 5 * 60_000);
    const joinedTimes = runs.flatMap((run, index) =>
      run.points.slice(index === 0 ? 0 : 1).map((point) => point.timeMs),
    );

    expect(runs[0].points.at(-1)).toEqual(runs[1].points[0]);
    expect(joinedTimes.every((timeMs, index) =>
      index === 0 || timeMs >= joinedTimes[index - 1],
    )).toBe(true);
  });

  test("solar points use slot midpoints and split instead of filling a missing interval", () => {
    const slots = normaliseSlots([], [storySlot(0), storySlot(15), storySlot(60)]);
    const runs = solarRuns(slots, BASE + 30 * 60_000);
    expect(runs.map((run) => run.length)).toEqual([2, 1]);
    expect(runs.map((run) => run.map((point) => point.timeMs))).toEqual([
      [BASE + SLOT_MS / 2, BASE + 15 * 60_000 + SLOT_MS / 2],
      [BASE + 60 * 60_000 + SLOT_MS / 2],
    ]);
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
        end: BASE + 15 * 60_000,
        startSocPct: 50,
        endSocPct: 51,
      },
      {
        action: "idle",
        start: BASE + 15 * 60_000,
        end: BASE + 30 * 60_000,
        startSocPct: 51,
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
