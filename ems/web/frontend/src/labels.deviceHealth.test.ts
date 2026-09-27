import { describe, expect, it } from "vitest";

import {
  DATA_QUALITY,
  DATA_SOURCE,
  sourceDetail,
  summarizeDeviceHealth,
} from "./labels";

describe("issue #79 device-health labels", () => {
  it("uses Deels verouderd for degraded data quality (never Gedegradeerd)", () => {
    expect(DATA_QUALITY.degraded.label).toBe("Deels verouderd");
    expect(DATA_QUALITY.degraded.label.toLowerCase()).not.toContain("gedegradeerd");
  });

  it("uses Demo for the simulator data-source badge", () => {
    expect(DATA_SOURCE.sim.label).toBe("Demo");
  });

  it("summarizeDeviceHealth: freshness grid stale → Deels verouderd", () => {
    const s = summarizeDeviceHealth({ grid: "stale", battery: "fresh" }, null, true);
    expect(s.label).toBe("Deels verouderd");
    expect(s.detail).toContain("P1-meter");
  });

  it("summarizeDeviceHealth: demo with all fresh → Demo", () => {
    const s = summarizeDeviceHealth(
      { grid: "fresh", battery: "fresh", prices: "fresh", forecast: "fresh" },
      {
        sources: [],
        summary: {
          badge: "demo",
          label: "Demo",
          detail: "Cijfers komen niet van jouw huis — dit is demodata.",
          severity: "warning",
        },
      },
      true,
    );
    expect(s.label).toBe("Demo");
  });

  it("sourceDetail formats prijzen van hh:mm", () => {
    expect(sourceDetail("prices", "14:00")).toBe("prijzen van 14:00");
  });
});
