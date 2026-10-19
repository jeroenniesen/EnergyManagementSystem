import { describe, expect, test } from "vitest";

import { digestEurNl } from "./labels";

describe("digestEurNl (#325)", () => {
  test("formats loss and driver amounts with a Dutch comma and a real minus", () => {
    expect(digestEurNl(-1.2)).toBe("−€1,20");
    expect(digestEurNl(0.7)).toBe("€0,70");
    expect(digestEurNl(0)).toBe("€0,00");
  });
});
