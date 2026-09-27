import { describe, expect, test } from "vitest";

import { EMS_UNREACHABLE, formatLaatstBekend } from "./labels";

describe("B-09 EMS unreachable copy (#73)", () => {
  test("three-line contract: message + failsafe + Laatst bekend helper", () => {
    expect(EMS_UNREACHABLE.message).toMatch(/unreachable/i);
    expect(EMS_UNREACHABLE.ems_doing.toLowerCase()).toContain("watch-only");
    expect(EMS_UNREACHABLE.ems_doing.toLowerCase()).toContain("self-use");
    // Never over-claim failsafe without confirmed AUTO; no viewer-network claim.
    const blob = `${EMS_UNREACHABLE.message} ${EMS_UNREACHABLE.ems_doing}`.toLowerCase();
    expect(blob).not.toContain("the battery is safe");
    expect(blob).not.toContain("nothing changes");
    expect(blob).not.toContain("home assistant");
    expect(blob).not.toContain("network loss");
  });

  test("formatLaatstBekend includes the fixed phrase and hh:mm", () => {
    const at = new Date(2026, 8, 27, 14, 5, 0).getTime(); // local 14:05
    const line = formatLaatstBekend(at);
    expect(line).toContain("Laatst bekend");
    expect(line).toMatch(/Laatst bekend \d{2}:\d{2}/);
    expect(line).toBe("Laatst bekend 14:05");
  });

  test("formatLaatstBekend keeps the phrase with em-dash when time is unknown", () => {
    expect(formatLaatstBekend(null)).toBe("Laatst bekend —");
  });

  test("unsafe data-quality labels never say safe mode or the battery is safe", async () => {
    const { DATA_QUALITY, CONFIDENCE } = await import("./labels");
    const blob = `${DATA_QUALITY.unsafe.label} ${DATA_QUALITY.unsafe.title} ${CONFIDENCE.unsafe}`.toLowerCase();
    expect(blob).not.toContain("safe mode");
    expect(blob).not.toContain("the battery is safe");
    expect(blob).toContain("self-use");
  });
});
