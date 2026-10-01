import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, test } from "vitest";

import type {
  EnergyStoryData,
  PlanProvenance,
  StorySlot,
  StoryTotals,
} from "./EnergyStory";
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

function svgAttr(markup: string, name: string): string | null {
  return markup.match(new RegExp(`${name}="([^"]+)"`))?.[1] ?? null;
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
    expect(html).toContain("Voedt de woning");
    expect(html).toContain("Geen gemeten data");
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

  // B-97: SoC / Saved live in OutcomeTiles — PlanStory must not repeat them as a footer KPI strip.
  test("omits the footer KPI strip that duplicated OutcomeTiles SoC and Saved", () => {
    const html = renderToStaticMarkup(createElement(PlanStory, {
      story: storyData([], [storySlot(0)], 0),
    }));

    expect(html).toContain('data-testid="plan-story-plot"');
    expect(html).toContain('data-testid="plan-story-legend"');
    expect(html).not.toContain("story-footer");
    expect(html).not.toContain("Saved today");
    expect(html).not.toContain("see each battery");
    expect(html).not.toContain(">Mode<");
  });

  test("B-96: recent review renders as a collapsed plan-header disclosure", () => {
    const html = renderToStaticMarkup(createElement(PlanStory, {
      story: storyData([], [storySlot(0)], 0),
      recentReview: "Last 3h solar reached 80% of forecast.",
    }));

    expect(html).toContain('data-testid="recent-review"');
    expect(html).toContain("Laatste 3 uur");
    expect(html).toContain("Last 3h solar reached 80% of forecast.");
    expect(html).toContain("<details");
    // Chart still present — disclosure does not replace PlanStory.
    expect(html).toContain('data-testid="plan-story-plot"');
  });

  test("renders a recorded idle action as Inactief and never as Vasthouden", () => {
    const html = renderToStaticMarkup(createElement(PlanStory, {
      story: storyData([storySlot(0, { action: "idle" })], [], 15),
    }));

    expect(html).toContain(">Inactief<");
    expect(html).toContain("Staat stil");
    expect(html).not.toContain(">Vasthouden<");
    expect(html).not.toContain("Houdt vast");
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
    expect(html).toContain(`data-end-ms="${BASE + 30 * 60_000}"`);
  });

  test("renders one native SVG action ribbon per merged window", () => {
    const html = renderToStaticMarkup(createElement(PlanStory, {
      story: storyData([], [
        storySlot(0, { action: "hold" }),
        storySlot(15, { action: "hold" }),
        storySlot(30, { action: "hold" }),
        storySlot(45, { action: "idle" }),
        storySlot(60, { action: "idle" }),
      ], 0),
    }));
    const ribbons = [...html.matchAll(
      /<rect[^>]*data-testid="plan-story-action-segment"[^>]*>/g,
    )].map((match) => match[0]);

    expect(ribbons).toHaveLength(2);
    expect(ribbons.map((ribbon) => svgAttr(ribbon, "data-action"))).toEqual([
      "hold",
      "idle",
    ]);
    expect(ribbons.map((ribbon) => Number(svgAttr(ribbon, "width")))).toEqual([
      528,
      352,
    ]);
    expect(ribbons.map((ribbon) => svgAttr(ribbon, "fill"))).toEqual([
      "url(#plan-story-ribbon-hold)",
      "url(#plan-story-ribbon-idle)",
    ]);
    expect(html).not.toContain("<foreignObject");
  });

  test("defines all six action textures as user-space SVG patterns with distinct angles", () => {
    const html = renderToStaticMarkup(createElement(PlanStory, {
      story: storyData([], [storySlot(0)], 0),
    }));
    const expectedAngles = {
      hold: 0,
      solar_charge: 30,
      grid_charge: 60,
      discharge: 90,
      self_consume: 120,
      idle: 150,
    };

    for (const [action, angle] of Object.entries(expectedAngles)) {
      expect(html).toContain(
        `id="plan-story-ribbon-${action}" width="6" height="6" ` +
        `patternUnits="userSpaceOnUse" patternTransform="rotate(${angle})"`,
      );
      expect(html).toContain(`class="plan-story-ribbon-base action-${action}"`);
    }
    expect(html.match(/class="plan-story-ribbon-line"/g)).toHaveLength(6);
  });

  test("renders available plan provenance clauses after the legend without a KPI footer", () => {
    const provenance: PlanProvenance = {
      forecast_source: "Forecast.Solar",
      solar_confidence_pct: 100,
      planner: "rule_based",
      intelligence: {
        state: "not_active",
        last_evaluated_at: null,
        last_result: null,
        reason: "not wired into the live path",
      },
    };
    const full = renderToStaticMarkup(createElement(PlanStory, {
      story: storyData([], [storySlot(0)], 0),
      provenance,
    }));
    const partial = renderToStaticMarkup(createElement(PlanStory, {
      story: storyData([], [storySlot(0)], 0),
      provenance: { planner: "adaptive" },
    }));

    expect(full).toContain(
      "Gepland met <span title=\"The live solar forecast source feeding today&#x27;s plan.\">" +
      "Forecast.Solar at 100% confidence</span> · ",
    );
    expect(full).toContain("rule-based winter planner");
    expect(full).toContain("scenario intelligence: not active yet");
    expect(full.indexOf("battery-plan-provenance")).toBeGreaterThan(
      full.indexOf("plan-story-legend"),
    );
    expect(full).not.toContain("story-footer");
    expect(partial).toContain("Gepland met <span");
    expect(partial).toContain("adaptive summer planner");
    expect(partial).not.toContain("% confidence");
    expect(partial).not.toContain("scenario intelligence:");
  });

  test("keeps provenance visible while the energy story has no plottable slots", () => {
    const html = renderToStaticMarkup(createElement(PlanStory, {
      story: storyData([], [], 0),
      provenance: { planner: "rule_based" },
    }));

    expect(html).toContain("Batterijplan niet beschikbaar.");
    expect(html).toContain("Gepland met");
    expect(html).toContain("rule-based winter planner");
  });

  test("draws negative prices below a zero baseline while captioning the observed range", () => {
    const html = renderToStaticMarkup(createElement(PlanStory, {
      story: storyData([], [
        storySlot(0, { eur_per_kwh: -0.2 }),
        storySlot(15, { eur_per_kwh: -0.1 }),
      ], 0),
    }));
    const zeroLine = html.match(/<line[^>]*data-price-zero="true"[^>]*>/)?.[0] ?? "";
    const bars = [...html.matchAll(
      /<rect[^>]*data-testid="plan-story-price"[^>]*>/g,
    )].map((match) => match[0]);
    const zeroY = Number(svgAttr(zeroLine, "y1"));

    expect(zeroY).toBeCloseTo(238.24);
    expect(svgAttr(zeroLine, "y2")).toBe(String(zeroY));
    expect(bars).toHaveLength(2);
    expect(bars.map((bar) => svgAttr(bar, "data-price-negative"))).toEqual([
      "true",
      "true",
    ]);
    expect(bars.map((bar) => Number(svgAttr(bar, "y")))).toEqual([zeroY, zeroY]);
    const heights = bars.map((bar) => Number(svgAttr(bar, "height")));
    expect(heights[0]).toBeCloseTo(65.76);
    expect(heights[1]).toBeCloseTo(32.88);
    for (const width of bars.map((bar) => Number(svgAttr(bar, "width")))) {
      expect(width).toBeCloseTo(237.6);
    }
    expect(bars.every((bar) => svgAttr(bar, "opacity") == null)).toBe(true);
    expect(html).toContain("Prijs €-0.20–€-0.10");
    expect(html).not.toContain("Prijs €-0.20–€0.00");
    expect(html).not.toContain("Price €-0.20–€0.00");
  });
});
