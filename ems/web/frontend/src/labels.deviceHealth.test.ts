import { describe, expect, it } from "vitest";

import {
  DATA_QUALITY,
  DATA_SOURCE,
  DEVICE_HEALTH_SOURCE_ALERT_KEYS,
  pickDeviceHealthAlert,
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

  it("summarizeDeviceHealth prefers backend summary over local freshness", () => {
    const s = summarizeDeviceHealth(
      { grid: "stale", battery: "fresh" },
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

  it("summarizeDeviceHealth falls back to Deels verouderd when API absent", () => {
    const s = summarizeDeviceHealth({ forecast: "stale", battery: "fresh" }, null, false);
    expect(s.label).toBe("Deels verouderd");
    expect(s.detail).toContain("zonvoorspelling");
  });

  it("sourceDetail formats prijzen van hh:mm", () => {
    expect(sourceDetail("prices", "14:00")).toBe("prijzen van 14:00");
  });

  it("keeps unsafe header vocabulary on DATA_QUALITY", () => {
    expect(DATA_QUALITY.unsafe.label).toBe("Paused — self-use");
  });

  it("prices row prefers #148 live-price alert keys over freshness", () => {
    expect(DEVICE_HEALTH_SOURCE_ALERT_KEYS.prices[0]).toBe("no_live_prices");
    expect(DEVICE_HEALTH_SOURCE_ALERT_KEYS.prices[1]).toBe("tibber_prices_unavailable");
    const hit = pickDeviceHealthAlert("prices", [
      { key: "prices_stale", message: "stale" },
      { key: "tibber_prices_unavailable", message: "tibber down" },
      { key: "no_live_prices", message: "kijkmodus" },
    ]);
    // First matching key in map order wins — no_live_prices before tibber / stale.
    expect(hit?.key).toBe("no_live_prices");
    expect(
      pickDeviceHealthAlert("prices", [{ key: "tibber_prices_unavailable", message: "x" }])?.key,
    ).toBe("tibber_prices_unavailable");
  });
});
