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
    expect(html).toContain('data-testid="plan-story-soc-recorded-0"');
    expect(html).toContain('data-testid="plan-story-soc-forecast-1"');
    expect(html).toContain('data-testid="plan-story-target-label"');
    expect(html).toContain('data-testid="plan-story-reserve-label"');
    expect(html).toContain('data-testid="plan-story-action-segment"');
    expect(html).toContain('aria-hidden="true"');
    expect(html).toContain("Powers the house");
    expect(html).toContain("No recorded data");
    expect(html).not.toContain("<h2");
    expect(html).not.toContain("warning");
  });

  test("gives singleton SoC dots distinct test IDs from their polylines", () => {
    const recorded = renderToStaticMarkup(createElement(PlanStory, {
      story: storyData(
        [storySlot(0), storySlot(15), storySlot(45)],
        [],
        120,
      ),
    }));
    const forecast = renderToStaticMarkup(createElement(PlanStory, {
      story: storyData(
        [],
        [storySlot(0), storySlot(15), storySlot(45)],
        -15,
      ),
    }));

    expect(recorded).toContain('data-testid="plan-story-soc-recorded-0"');
    expect(recorded).toContain('data-testid="plan-story-soc-dot-recorded-1"');
    expect(forecast).toContain('data-testid="plan-story-soc-forecast-0"');
    expect(forecast).toContain('data-testid="plan-story-soc-dot-forecast-1"');
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

  test("renders a recorded idle action as Idle and never as Hold", () => {
    const html = renderToStaticMarkup(createElement(PlanStory, {
      story: storyData([storySlot(0, { action: "idle" })], [], 15),
    }));

    expect(html).toContain(">Idle<");
    expect(html).toContain("Idles");
    expect(html).not.toContain(">Hold<");
    expect(html).not.toContain("Holds");
  });

  test("renders point values at semantic anchors while action bands stay at slot starts", () => {
    const html = renderToStaticMarkup(createElement(PlanStory, {
      story: storyData(
        [storySlot(0, { soc_pct: 50, solar_w: 400 })],
        [storySlot(15, { soc_pct: 60, solar_w: 800 })],
        15,
      ),
    }));
    const recordedPoints = html.match(
      /data-testid="plan-story-soc-recorded-\d+"[^>]*points="([^"]+)"/,
    )?.[1];
    const forecastPoints = html.match(
      /data-testid="plan-story-soc-forecast-\d+"[^>]*points="([^"]+)"/,
    )?.[1];
    const solarPoints = html.match(
      /<polygon[^>]*points="([^"]+)"/,
    )?.[1];

    expect(recordedPoints?.split(" ").map((point) => Number(point.split(",")[0]))).toEqual([
      278,
      498,
    ]);
    expect(forecastPoints?.split(" ").map((point) => Number(point.split(",")[0]))).toEqual([
      498,
      938,
    ]);
    expect(solarPoints?.split(" ").slice(1, -1).map((point) => Number(point.split(",")[0]))).toEqual([
      278,
      718,
    ]);
    expect(html).toContain(`data-start-ms="${BASE}"`);
    expect(html).toContain(`data-start-ms="${BASE + 15 * 60_000}"`);
  });
});
