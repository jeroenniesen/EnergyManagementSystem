import { describe, expect, test } from "vitest";
import { batteryRecovery } from "./System";

describe("batteryRecovery (#father unreachable false positive)", () => {
  test("missing write driver does not say unreachable", () => {
    const text = batteryRecovery("no battery driver — read-only");
    expect(text.toLowerCase()).toContain("no write-capable battery driver");
    expect(text.toLowerCase()).not.toContain("unreachable");
  });

  test("probe failure says probe failed without claiming no driver", () => {
    const text = batteryRecovery(
      "battery unreachable — probe failed (check Indevolt IP and power)",
    );
    expect(text.toLowerCase()).toContain("probe failed");
    expect(text.toLowerCase()).toContain("indevolt");
    expect(text.toLowerCase()).not.toContain("no write-capable");
  });
});
