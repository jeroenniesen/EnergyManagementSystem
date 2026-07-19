import { useEffect, useRef, useState } from "react";

import type { EnergyStoryData, StorySlot } from "./EnergyStory";

const W = 1000;
const H = 330;
const PAD = { l: 58, r: 62, t: 30, b: 38 };
const SLOT_MS = 15 * 60 * 1000;
const ACTION_LABEL: Record<string, string> = {
  solar_charge: "Charge from solar",
  grid_charge: "Charge from grid",
  discharge: "Power the house",
  self_consume: "Use solar first",
  hold: "Hold",
  idle: "Idle",
};
const ACTION_CUE: Record<string, { short: string; angle: number }> = {
  solar_charge: { short: "SOL", angle: 45 }, grid_charge: { short: "GRID", angle: -45 },
  discharge: { short: "USE", angle: 90 }, self_consume: { short: "SELF", angle: 0 },
  hold: { short: "HOLD", angle: 30 }, idle: { short: "IDLE", angle: 60 },
};

const finiteNumber = (value: unknown): value is number =>
  typeof value === "number" && Number.isFinite(value);
const clock = (start: string) =>
  new Date(start).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
const actionLabel = (action: string) => ACTION_LABEL[action] ?? action.replaceAll("_", " ");

type ActionWindow = { action: string; start: number; end: number };

function actionWindows(slots: StorySlot[]): ActionWindow[] {
  const windows: ActionWindow[] = [];
  slots.forEach((slot) => {
    if (!slot.action) return;
    const start = Date.parse(slot.start);
    if (!Number.isFinite(start)) return;
    const previous = windows.at(-1);
    if (previous?.action === slot.action && previous.end === start) previous.end = start + SLOT_MS;
    else windows.push({ action: slot.action, start, end: start + SLOT_MS });
  });
  return windows;
}

export function describeCombinedPlan(story: EnergyStoryData | null): string {
  if (!story) return "Next 24 hours plan is loading.";
  const slots = story.slots;
  const prices = slots.map((slot) => slot.eur_per_kwh).filter(finiteNumber);
  const solar = slots.map((slot) => slot.solar_w).filter(finiteNumber);
  const soc = slots.map((slot) => slot.soc_pct).filter(finiteNumber);
  const windows = actionWindows(slots);
  const parts = ["Next 24 hours energy plan."];
  const start = story.totals.soc_start_pct ?? soc[0];
  const end = story.target_soc_pct ?? story.totals.soc_end_pct ?? soc.at(-1);
  if (finiteNumber(start)) parts.push(`Battery starts at ${Math.round(start)}%.`);
  if (finiteNumber(end)) {
    const deadline = story.target_deadline ? Date.parse(story.target_deadline) : NaN;
    parts.push(`${story.target_soc_pct != null ? "Target" : "Battery ends at"} ${Math.round(end)}%${
      story.target_soc_pct != null && Number.isFinite(deadline) ? ` by ${clock(story.target_deadline!)}` : ""
    }.`);
    parts.push(`Reserve is ${Math.round(story.reserve_soc_pct)}%, ${end >= story.reserve_soc_pct ? "below the target" : "above the target"}.`);
  }
  if (prices.length) parts.push(`Maximum price €${Math.max(...prices).toFixed(2)} per kilowatt-hour.`);
  if (solar.length) parts.push(`Maximum solar forecast ${Math.round(Math.max(...solar))} watts.`);
  if (windows.length) parts.push(`Principal action windows: ${windows.map((window) =>
    `${actionLabel(window.action)} ${clock(new Date(window.start).toISOString())}–${clock(new Date(window.end).toISOString())}`
  ).join(", ")}.`);
  return parts.join(" ");
}

function slotLabel(slot: StorySlot): string {
  const details = [clock(slot.start)];
  if (finiteNumber(slot.eur_per_kwh)) details.push(`€${slot.eur_per_kwh.toFixed(2)}/kWh`);
  if (finiteNumber(slot.solar_w)) details.push(`${Math.round(slot.solar_w).toLocaleString()} W solar`);
  if (finiteNumber(slot.soc_pct)) details.push(`${Math.round(slot.soc_pct)}% state of charge`);
  if (slot.action) details.push(actionLabel(slot.action));
  return details.join(", ");
}

function lineSegments(
  slots: StorySlot[],
  value: (slot: StorySlot) => number | null,
  point: (slot: StorySlot, value: number) => string,
): string[] {
  const segments: string[] = [];
  let current: string[] = [];
  slots.forEach((slot) => {
    const n = value(slot);
    if (finiteNumber(n)) current.push(point(slot, n));
    else if (current.length) { segments.push(current.join(" ")); current = []; }
  });
  if (current.length) segments.push(current.join(" "));
  return segments;
}

export function CombinedPlanChart({ story }: { story: EnergyStoryData | null }) {
  const slots = story?.slots ?? [];
  const recent = story?.recent ?? [];
  const [selectedIndex, setSelectedIndex] = useState<number | null>(0);
  const controls = useRef<(SVGRectElement | null)[]>([]);
  useEffect(() => setSelectedIndex((index) => index == null ? null : Math.min(index, Math.max(0, slots.length - 1))), [slots.length]);
  useEffect(() => {
    const dismiss = (event: KeyboardEvent) => { if (event.key === "Escape") setSelectedIndex(null); };
    window.addEventListener("keydown", dismiss);
    return () => window.removeEventListener("keydown", dismiss);
  }, []);

  if (!story || !slots.length) {
    return <section className="combined-plan" data-testid="combined-plan-chart" data-density-kind="chart"><p>Next 24 hours plan is unavailable.</p></section>;
  }

  const t0 = Date.parse((recent[0] ?? slots[0]).start);
  const t1 = Date.parse(slots.at(-1)!.start) + SLOT_MS;
  const span = Math.max(1, t1 - t0);
  const plotW = W - PAD.l - PAD.r;
  const plotH = H - PAD.t - PAD.b;
  const x = (time: number) => PAD.l + ((time - t0) / span) * plotW;
  const slotWidth = (slot: StorySlot) =>
    x(Date.parse(slot.start) + SLOT_MS) - x(Date.parse(slot.start));
  const socY = (value: number) => PAD.t + (1 - Math.max(0, Math.min(100, value)) / 100) * plotH;
  const prices = slots.map((slot) => slot.eur_per_kwh).filter(finiteNumber);
  const solar = slots.map((slot) => slot.solar_w).filter(finiteNumber);
  const minPrice = prices.length ? Math.min(0, ...prices) : 0;
  const maxPrice = prices.length ? Math.max(0, ...prices) : 1;
  const priceSpan = Math.max(0.01, maxPrice - minPrice);
  const maxSolar = solar.length ? Math.max(...solar, 1) : 1;
  const priceY = (value: number) => PAD.t + ((maxPrice - value) / priceSpan) * plotH;
  const solarY = (value: number) => PAD.t + (1 - Math.max(0, value) / maxSolar) * plotH;
  const windows = actionWindows(slots);
  const plannedSoc = lineSegments(slots, (slot) => slot.soc_pct,
    (slot, value) => `${x(Date.parse(slot.start) + SLOT_MS / 2)},${socY(value)}`);
  const actualSoc = lineSegments(recent, (slot) => slot.soc_pct,
    (slot, value) => `${x(Date.parse(slot.start) + SLOT_MS / 2)},${socY(value)}`);
  const solarSegments = lineSegments(slots, (slot) => finiteNumber(slot.solar_w) ? slot.solar_w : null,
    (slot, value) => `${x(Date.parse(slot.start) + SLOT_MS / 2)},${solarY(value)}`);
  const missing = [
    prices.length ? null : "price",
    solar.length ? null : "solar",
    plannedSoc.length || actualSoc.length ? null : "state of charge",
    windows.length ? null : "actions",
  ].filter((value): value is string => value != null);
  const selected = selectedIndex == null ? null : slots[selectedIndex] ?? null;
  const everyN = Math.max(1, Math.ceil(slots.length / 6));

  const moveSelection = (index: number, direction: -1 | 1) => {
    const next = Math.max(0, Math.min(slots.length - 1, index + direction));
    setSelectedIndex(next);
    controls.current[next]?.focus();
  };

  return (
    <section className="combined-plan" data-testid="combined-plan-chart" data-density-kind="chart" aria-label={describeCombinedPlan(story)}>
      <div className="combined-plan-heading"><h2>Next 24 hours</h2><span>SoC · solar · price · plan</span></div>
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={describeCombinedPlan(story)}>
        <defs>
          {Object.entries(ACTION_CUE).map(([action, cue]) => <pattern key={action}
            id={`combined-plan-pattern-${action}`} width="10" height="10" patternUnits="userSpaceOnUse"
            patternTransform={`rotate(${cue.angle})`}><line x1="0" y1="0" x2="0" y2="10"
              className="combined-plan-pattern-line" /></pattern>)}
        </defs>
        <g className="combined-plan-windows">
          {windows.map((window) => <g key={`${window.start}-${window.action}`}>
            <rect x={x(window.start)} y={PAD.t} width={x(window.end) - x(window.start)}
              height={plotH} className={`combined-plan-window action-${window.action}`} />
            <rect x={x(window.start)} y={PAD.t} width={x(window.end) - x(window.start)}
              height={plotH} fill={`url(#combined-plan-pattern-${ACTION_CUE[window.action] ? window.action : "idle"})`}
              className="combined-plan-window-pattern" />
            <text className="combined-plan-window-label" x={x(window.start) + 4} y={PAD.t + 14}>
              {(ACTION_CUE[window.action] ?? { short: actionLabel(window.action).slice(0, 4) }).short}
            </text>
          </g>)}
        </g>
        <g className="combined-plan-solar">
          {solarSegments.map((points, index) => {
            const pairs = points.split(" ");
            const firstX = pairs[0].split(",")[0];
            const lastX = pairs.at(-1)!.split(",")[0];
            return <polygon key={index} points={`${points} ${lastX},${PAD.t + plotH} ${firstX},${PAD.t + plotH}`} />;
          })}
          <text x={PAD.l} y={18}>Solar 0–{Math.round(maxSolar).toLocaleString()} W</text>
        </g>
        <g className="combined-plan-prices">
          <line data-price-zero x1={PAD.l} x2={W - PAD.r} y1={priceY(0)} y2={priceY(0)} />
          {slots.map((slot) => finiteNumber(slot.eur_per_kwh) && <rect key={slot.start}
            data-price-negative={slot.eur_per_kwh < 0 ? "true" : undefined}
            className={slot.eur_per_kwh < 0 ? "combined-plan-price-negative" : undefined}
            x={x(Date.parse(slot.start)) + slotWidth(slot) * 0.23}
            y={Math.min(priceY(slot.eur_per_kwh), priceY(0))}
            width={Math.max(2, slotWidth(slot) * 0.54)}
            height={Math.abs(priceY(slot.eur_per_kwh) - priceY(0))} />)}
          <text x={W - PAD.r} y={18}>Price €{minPrice.toFixed(2)}–€{maxPrice.toFixed(2)}</text>
        </g>
        <g className="combined-plan-soc">
          {actualSoc.map((points, index) => <polyline key={`actual-${index}`} className="combined-plan-soc-actual" points={points} />)}
          {plannedSoc.map((points, index) => <polyline key={`plan-${index}`} className="combined-plan-soc-forecast" points={points} />)}
          <line x1={PAD.l} x2={W - PAD.r} y1={socY(story.reserve_soc_pct)} y2={socY(story.reserve_soc_pct)} />
          <text x={W - PAD.r + 5} y={socY(story.reserve_soc_pct) + 4}>reserve {Math.round(story.reserve_soc_pct)}%</text>
          {finiteNumber(story.target_soc_pct) && <><line data-testid="combined-plan-target-soc"
            className="combined-plan-target" x1={PAD.l} x2={W - PAD.r}
            y1={socY(story.target_soc_pct)} y2={socY(story.target_soc_pct)} />
            <text x={W - PAD.r + 5} y={socY(story.target_soc_pct) + 4}>target {Math.round(story.target_soc_pct)}%</text></>}
        </g>
        <g className="combined-plan-axis">
          {slots.map((slot, index) => index % everyN === 0 && <text key={slot.start}
            x={x(Date.parse(slot.start) + SLOT_MS / 2)} y={H - 10}>{clock(slot.start)}</text>)}
          {Number.isFinite(Date.parse(story.now)) && Date.parse(story.now) >= t0 && Date.parse(story.now) <= t1 && <>
            <line x1={x(Date.parse(story.now))} x2={x(Date.parse(story.now))} y1={PAD.t} y2={PAD.t + plotH} />
            <text x={x(Date.parse(story.now)) + 5} y={PAD.t + 14}>now</text>
          </>}
          {story.target_deadline && Number.isFinite(Date.parse(story.target_deadline)) &&
            Date.parse(story.target_deadline) >= t0 && Date.parse(story.target_deadline) <= t1 && <>
            <line data-testid="combined-plan-target-deadline" className="combined-plan-deadline"
              x1={x(Date.parse(story.target_deadline))} x2={x(Date.parse(story.target_deadline))}
              y1={PAD.t} y2={PAD.t + plotH} />
            <text className="combined-plan-deadline-label" x={x(Date.parse(story.target_deadline)) - 4}
              y={PAD.t + plotH - 7}>target {clock(story.target_deadline)}</text>
          </>}
        </g>
        <g className="combined-plan-controls">
          {slots.map((slot, index) => <rect key={slot.start} ref={(node) => { controls.current[index] = node; }}
            x={x(Date.parse(slot.start))} y={PAD.t} width={slotWidth(slot)} height={plotH}
            tabIndex={0} role="button" data-testid="combined-plan-slot" aria-label={slotLabel(slot)}
            onFocus={() => setSelectedIndex(index)} onPointerEnter={() => setSelectedIndex(index)}
            onClick={() => setSelectedIndex(index)} onKeyDown={(event) => {
              if (event.key === "ArrowLeft") { event.preventDefault(); moveSelection(index, -1); }
              if (event.key === "ArrowRight") { event.preventDefault(); moveSelection(index, 1); }
            }} />)}
        </g>
      </svg>
      {selected && <div className="combined-plan-readout" data-testid="combined-plan-readout" aria-live="polite">
        <span>{slotLabel(selected)}</span>
        <button type="button" data-testid="combined-plan-readout-close" aria-label="Close selected chart point"
          onClick={() => setSelectedIndex(null)}>Close</button>
      </div>}
      {missing.length > 0 && <p className="combined-plan-missing" data-testid="combined-plan-missing">
        Still showing available data; missing {missing.join(", ")}.
      </p>}
    </section>
  );
}
