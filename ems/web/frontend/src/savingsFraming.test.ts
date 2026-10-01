import { describe, expect, test } from "vitest";
import { HOME_TILE } from "./labels";
import { frameSavedToday, isCalmNoBenefit } from "./savingsFraming";

describe("isCalmNoBenefit", () => {
  test("treats zero and negative as quiet no-benefit", () => {
    expect(isCalmNoBenefit(0)).toBe(true);
    expect(isCalmNoBenefit(-1.39)).toBe(true);
    expect(isCalmNoBenefit(0.01)).toBe(false);
  });
});

describe("frameSavedToday", () => {
  test("measuring uses Nog meten + muted euro icon, never €0.00", () => {
    const frame = frameSavedToday({ status: "measuring" });
    expect(frame.value).toBe(HOME_TILE.savings.measuringShort);
    expect(frame.value).toBe("Nog meten");
    expect(frame.title).toMatch(/nog meten/i);
    expect(frame.tone).toBe("measuring");
    expect(frame.icon).toBe("euro");
    expect(frame.openFinance).toBe(false);
    expect(frame.value).not.toContain("€");
  });

  test("negative measured savings use Vandaag nog geen voordeel, keep amount in title", () => {
    const frame = frameSavedToday({ status: "measured", eur: -1.39 });
    expect(frame.value).toBe(HOME_TILE.savings.noBenefit);
    expect(frame.value).toBe("Vandaag nog geen voordeel");
    expect(frame.title).toContain("−€1.39");
    expect(frame.tone).toBe("none");
    expect(frame.icon).toBe("euro");
    expect(frame.openFinance).toBe(true);
  });

  test("zero measured savings also calm-frame (never dominate with €0.00)", () => {
    const frame = frameSavedToday({ status: "measured", eur: 0 });
    expect(frame.value).toBe("Vandaag nog geen voordeel");
    expect(frame.title).toContain("€0.00");
    expect(frame.tone).toBe("none");
    expect(frame.icon).toBe("euro");
  });

  test("positive measured savings stay a plain € amount with a quiet euro icon", () => {
    const frame = frameSavedToday({ status: "measured", eur: 2.84 });
    expect(frame.value).toBe("€2.84");
    expect(frame.title).toBe(HOME_TILE.savings.title);
    expect(frame.tone).toBe("positive");
    expect(frame.icon).toBe("euro");
    expect(frame.openFinance).toBe(true);
  });

  test("null / unavailable stays a dash without alarm chrome", () => {
    const frame = frameSavedToday(null);
    expect(frame.value).toBe("—");
    expect(frame.tone).toBe("unavailable");
    expect(frame.icon).toBeNull();
    expect(frame.openFinance).toBe(false);
  });
});
