import { describe, expect, it } from "vitest";

import {
  SETTINGS_SECTION_IDS,
  parseAppHash,
  resolveSettingsSection,
  serialiseAppHash,
} from "./settingsRoute";

describe("settings section hash (#368)", () => {
  it("parses a valid #manage/settings/<id>", () => {
    expect(parseAppHash("#manage/settings/control")).toEqual({
      view: "manage",
      tab: "settings",
      settingsSection: "control",
      settingsSectionKnown: true,
    });
    expect(parseAppHash("manage/settings/ev")).toEqual({
      view: "manage",
      tab: "settings",
      settingsSection: "ev",
      settingsSectionKnown: true,
    });
    expect(parseAppHash("#/manage/settings/planner").settingsSection).toBe("planner");
  });

  it("marks an unknown section id and resolves it to the first section", () => {
    expect(parseAppHash("#manage/settings/not-a-section")).toEqual({
      view: "manage",
      tab: "settings",
      settingsSection: "not-a-section",
      settingsSectionKnown: false,
    });
    expect(resolveSettingsSection("not-a-section", SETTINGS_SECTION_IDS)).toBe("connection");
    expect(resolveSettingsSection("control", SETTINGS_SECTION_IDS)).toBe("control");
    expect(resolveSettingsSection(undefined, SETTINGS_SECTION_IDS)).toBeUndefined();
    expect(resolveSettingsSection("not-a-section", [])).toBeUndefined();
    expect(SETTINGS_SECTION_IDS[0]).toBe("connection");
  });

  it("keeps the old manage routes and falls unknown views back to the dashboard", () => {
    expect(parseAppHash("#manage")).toEqual({ view: "manage", tab: "settings" });
    expect(parseAppHash("#manage/settings")).toEqual({ view: "manage", tab: "settings" });
    expect(parseAppHash("#manage/system")).toEqual({ view: "manage", tab: "system" });
    expect(parseAppHash("#manage/audit")).toEqual({ view: "manage", tab: "audit" });
    expect(parseAppHash("#settings")).toEqual({ view: "manage", tab: "settings" });
    expect(parseAppHash("#system")).toEqual({ view: "manage", tab: "system" });
    expect(parseAppHash("#audit")).toEqual({ view: "manage", tab: "audit" });
    expect(parseAppHash("#setup")).toEqual({ view: "setup", tab: "settings" });
    expect(parseAppHash("#dashboard")).toEqual({ view: "dashboard", tab: "settings" });
    expect(parseAppHash("#definitely-not-a-view")).toEqual({ view: "dashboard", tab: "settings" });
  });

  it("serialises a section push and leaves bare manage / system / audit unchanged", () => {
    expect(serialiseAppHash("manage", "settings", "control")).toBe("manage/settings/control");
    expect(serialiseAppHash("manage", "settings", "ev")).toBe("manage/settings/ev");
    expect(serialiseAppHash("manage", "settings")).toBe("manage");
    expect(serialiseAppHash("manage", "system")).toBe("manage/system");
    expect(serialiseAppHash("manage", "system", "planner")).toBe("manage/system");
    expect(serialiseAppHash("manage", "audit")).toBe("manage/audit");
    expect(serialiseAppHash("dashboard")).toBe("dashboard");
    expect(serialiseAppHash("setup")).toBe("setup");
    const round = serialiseAppHash("manage", "settings", "battery");
    expect(parseAppHash(`#${round}`)).toMatchObject({
      view: "manage",
      tab: "settings",
      settingsSection: "battery",
      settingsSectionKnown: true,
    });
  });

  it("uses the existing settings section ids", () => {
    expect([...SETTINGS_SECTION_IDS]).toEqual([
      "connection",
      "meters",
      "battery",
      "prices",
      "consumers",
      "site",
      "strategy",
      "planner",
      "control",
      "ev",
      "ai",
      "reporting",
      "notify",
      "access",
      "ui",
    ]);
  });
});
