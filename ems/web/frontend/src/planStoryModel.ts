import type { StorySlot } from "./EnergyStory";

export const SLOT_MS = 15 * 60 * 1000;

export type TimedStorySlot = StorySlot & { startMs: number };
export type SlotPointMetric = "soc" | "solar";

/**
 * Anchor point values where their measurements apply, not at the slot start.
 * `ems/planner/projection.py` defines forecast SoC as the END-of-slot state, while
 * `ems/retrospect.py` defines recorded SoC and power as slot means. Solar power is
 * a slot mean in both windows. Those backend definitions are the sources of truth:
 * means use the midpoint, while forecast SoC uses the end.
 */
export function slotValueAnchorMs(
  startMs: number,
  nowMs: number,
  metric: SlotPointMetric,
): number {
  const isForecast = startMs >= nowMs;
  return metric === "soc" && isForecast
    ? startMs + SLOT_MS
    : startMs + SLOT_MS / 2;
}

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

import type { EnergyStoryData } from "./EnergyStory";

const GAP_LIMIT_MS = 1.5 * SLOT_MS;

export type PlanAction =
  | "solar_charge"
  | "grid_charge"
  | "discharge"
  | "self_consume"
  | "hold"
  | "idle";

export const ACTION_META: Record<
  PlanAction,
  { label: string; phrase: string; className: string }
> = {
  solar_charge: {
    label: "Charge from solar",
    phrase: "Charges from solar",
    className: "seg-solar_charge",
  },
  grid_charge: {
    label: "Charge from grid",
    phrase: "Charges from grid",
    className: "seg-grid_charge",
  },
  discharge: {
    label: "Power the house",
    phrase: "Powers the house",
    className: "seg-discharge",
  },
  self_consume: {
    label: "Use solar first",
    phrase: "Uses solar first",
    className: "seg-self_consume",
  },
  hold: {
    label: "Hold",
    phrase: "Holds",
    className: "seg-hold",
  },
  idle: {
    label: "Idle",
    phrase: "Idles",
    className: "seg-idle",
  },
};

export function canonicalAction(action: string): PlanAction {
  if (action in ACTION_META) return action as PlanAction;
  // Unknown values fall back to "idle", never "hold". `action` is typed `string`
  // server-side, so an unrecognised value is possible. "Hold" is a deliberate
  // planned intent; claiming it for a value we failed to understand invents intent
  // from ignorance — the same error as folding `idle` into `hold`. "Idle" asserts
  // nothing, which is the honest reading of "we do not recognise this".
  return "idle";
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
export type SolarPoint = {
  startMs: number;
  timeMs: number;
  solarW: number;
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
  color?: string;
  className?: string;
};

export type PlanStoryModel = {
  slots: TimedStorySlot[];
  scale: TimeScale;
  spans: SlotSpan[];
  gaps: GapWindow[];
  soc: SocRun[];
  solar: SolarPoint[][];
  actions: ActionWindow[];
  ticks: number[];
  nowMs: number;
  nowX: number | null;
  minPrice: number;
  maxPrice: number;
  priceScaleMin: number;
  priceScaleMax: number;
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
    const point = {
      timeMs: slotValueAnchorMs(slot.startMs, nowMs, "soc"),
      socPct: slot.soc_pct,
    };
    if (!current || separated) {
      current = { kind, points: [point] };
      runs.push(current);
    } else if (current.kind !== kind) {
      const previous: SocPoint = current.points[current.points.length - 1];
      const joinTimeMs = Math.max(previous.timeMs, Math.min(nowMs, point.timeMs));
      const ratio = (joinTimeMs - previous.timeMs) / (point.timeMs - previous.timeMs);
      const join: SocPoint = {
        timeMs: joinTimeMs,
        socPct: previous.socPct + (point.socPct - previous.socPct) * ratio,
      };
      if (join.timeMs !== previous.timeMs) current.points.push(join);
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
  nowMs: number,
): SolarPoint[][] {
  const runs: SolarPoint[][] = [];
  for (const slot of slots) {
    if (!finiteNumber(slot.solar_w)) continue;
    const point = {
      startMs: slot.startMs,
      timeMs: slotValueAnchorMs(slot.startMs, nowMs, "solar"),
      solarW: slot.solar_w,
    };
    const previous = runs.at(-1)?.at(-1);
    if (!previous || slot.startMs - previous.startMs > GAP_LIMIT_MS) runs.push([point]);
    else runs[runs.length - 1].push(point);
  }
  return runs;
}

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
  rows.push({
    label: "Action",
    value: ACTION_META[action].label,
    className: ACTION_META[action].className,
  });
  if (finiteNumber(slot.grid_w)) {
    rows.push({
      label: "Grid flow",
      value: `${watts(slot.grid_w)} ${slot.grid_w >= 0 ? "import" : "export"}`,
      color: "var(--winter)",
    });
  }
  return rows.slice(0, 5);
}

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
    solar: solarRuns(slots, nowMs),
    actions: actionWindows(slots),
    ticks: tickTimes(slots),
    nowMs,
    nowX: nowX(scale, story.now),
    minPrice: prices.length ? Math.min(...prices) : 0,
    maxPrice: prices.length ? Math.max(...prices) : 0,
    priceScaleMin: prices.length ? Math.min(0, ...prices) : 0,
    priceScaleMax: prices.length ? Math.max(0, ...prices) : 0,
    maxSolar: solar.length ? Math.max(1, ...solar) : 1,
    summary: describeCombinedPlan(story),
    label: describeCombinedPlanLabel(story),
  };
}
