import { describe, expect, it } from "vitest";

import {
  WHATIF_LIVE_KEYS,
  digestSolarSettingsBody,
  whatifLiveSettingsBody,
} from "./applyLive";
import { WEEK_DIGEST_APPLY, WHATIF_APPLY_LIVE } from "./labels";

describe("digest solar apply payload (#337 F-36)", () => {
  it("writes exactly planner.solar_confidence", () => {
    expect(digestSolarSettingsBody({ key: "planner.solar_confidence", value: 70, current: 80 }))
      .toEqual({ "planner.solar_confidence": 70 });
    expect(WEEK_DIGEST_APPLY.button(70)).toBe("Pas 70% toe");
  });

  it("refuses any other key, including arming floors", () => {
    expect(digestSolarSettingsBody({ key: "control.dry_run", value: 0 })).toBeNull();
    expect(digestSolarSettingsBody({ key: "control.operational", value: 1 })).toBeNull();
    expect(digestSolarSettingsBody(null)).toBeNull();
  });
});

describe("what-if live payload (#337 F-38)", () => {
  it("keeps allow-listed overrides and names the confirm button", () => {
    expect(WHATIF_APPLY_LIVE.button).toBe("Gebruik deze knoppen live");
    expect(whatifLiveSettingsBody({ "planner.solar_confidence": 60 })).toEqual({
      "planner.solar_confidence": 60,
    });
    expect(WHATIF_LIVE_KEYS).not.toContain("control.dry_run");
    expect(WHATIF_LIVE_KEYS).not.toContain("control.operational");
  });

  it("drops the whole body when a key is outside the allow-list", () => {
    expect(whatifLiveSettingsBody({ "control.dry_run": false })).toBeNull();
    expect(whatifLiveSettingsBody({
      "planner.solar_confidence": 60,
      "control.operational": true,
    })).toBeNull();
    expect(whatifLiveSettingsBody({ "battery.usable_kwh": 20 })).toBeNull();
    expect(whatifLiveSettingsBody({})).toBeNull();
  });
});
