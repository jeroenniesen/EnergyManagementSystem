import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { OutcomeTiles } from "./OutcomeTiles";
import type { Report } from "./HomeScores";

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
        freshness: {
          report: { updatedAt: Date.now(), stale: false },
          status: { updatedAt: Date.now(), stale: false },
          finance: { updatedAt: Date.now(), stale: false },
        },
      }),
    );
    expect(html).not.toMatch(/data-testid="outcome-(solar-score|soc|savings|grid-import)"[^>]*aria-label=/);
  });
});
