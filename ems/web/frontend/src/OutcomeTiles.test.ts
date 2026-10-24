import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { OutcomeTiles } from "./OutcomeTiles";
import type { Report } from "./HomeScores";
import { HOME_TILE } from "./labels";

const report: Report = {
  partial: false,
  flows: { has_data: true, grid_import_kwh: 3.2 },
  scores: [
    {
      key: "self_consumption",
      label: "Self-consumption",
      value: 80,
      explanation: "",
    },
  ],
};

const freshness = {
  report: { updatedAt: Date.now(), stale: false },
  status: { updatedAt: Date.now(), stale: false },
  finance: { updatedAt: Date.now(), stale: false },
};

const tileAriaLabelPattern =
  /data-testid="outcome-(solar-score|soc|savings|grid-import)"[^>]*aria-label=/;

describe("OutcomeTiles accessible names (#302)", () => {
  it("does not override tile names with aria-label — visible label + value are the name", () => {
    const html = renderToStaticMarkup(
      createElement(OutcomeTiles, {
        report,
        savedToday: { status: "measured", eur: 2.84 },
        socPct: 55,
        onOpenInsights: () => {},
        onOpenFinance: () => {},
        onOpenBattery: () => {},
        freshness,
      }),
    );
    expect(html).not.toMatch(tileAriaLabelPattern);
    expect(html).toContain(HOME_TILE.solarScore.label);
    expect(html).toContain("80");
    expect(html).toContain(HOME_TILE.soc.label);
    expect(html).toContain("55%");
  });

  it("renders tiles without onOpen as role=group with label and value in the markup", () => {
    const html = renderToStaticMarkup(
      createElement(OutcomeTiles, {
        report,
        savedToday: { status: "measured", eur: 2.84 },
        socPct: 55,
        onOpenInsights: () => {},
        onOpenFinance: () => {},
        freshness,
      }),
    );
    expect(html).not.toMatch(tileAriaLabelPattern);
    expect(html).toMatch(/data-testid="outcome-soc"[^>]*role="group"/);
    expect(html).toContain(HOME_TILE.soc.label);
    expect(html).toContain("55%");
    expect(html).not.toMatch(/<button[^>]*data-testid="outcome-soc"/);
  });
});
