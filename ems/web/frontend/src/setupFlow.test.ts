import { describe, expect, test } from "vitest";

import {
  batteryChecklistOutcome,
  buildBatteryLinkPayload,
  clearSetupPersistence,
  interpretRestartResponse,
  needsP1IpField,
  privateIpv4Hint,
  SETUP_BATTERY_IP_KEY,
  SETUP_STEP_STORAGE_KEY,
} from "./setupFlow";

describe("setupFlow (#291)", () => {
  test("buildBatteryLinkPayload includes P1 when settings P1 is empty", () => {
    expect(buildBatteryLinkPayload("192.168.1.5", "192.168.1.10", "")).toEqual({
      "battery.indevolt_ip": "192.168.1.5",
      "connection.use_live_devices": true,
      "meters.p1_ip": "192.168.1.10",
    });
  });

  test("buildBatteryLinkPayload skips P1 when already configured", () => {
    expect(buildBatteryLinkPayload("192.168.1.5", "192.168.1.99", "10.0.0.2")).toEqual({
      "battery.indevolt_ip": "192.168.1.5",
      "connection.use_live_devices": true,
    });
  });

  test("needsP1IpField", () => {
    expect(needsP1IpField("")).toBe(true);
    expect(needsP1IpField("  ")).toBe(true);
    expect(needsP1IpField("192.168.0.1")).toBe(false);
  });

  test("privateIpv4Hint accepts RFC1918", () => {
    expect(privateIpv4Hint("192.168.2.1")).toBeNull();
    expect(privateIpv4Hint("10.0.0.5")).toBeNull();
    expect(privateIpv4Hint("172.20.1.1")).toBeNull();
  });

  test("privateIpv4Hint rejects public and empty", () => {
    expect(privateIpv4Hint("")).toMatch(/Vul/);
    expect(privateIpv4Hint("8.8.8.8")).toMatch(/privé/);
    expect(privateIpv4Hint("http://192.168.1.1")).toMatch(/privé/);
  });

  test("batteryChecklistOutcome after reload", () => {
    expect(batteryChecklistOutcome("Werkt", "192.168.1.3")?.phase).toBe("success");
    expect(batteryChecklistOutcome("Ontbreekt", "192.168.1.3")?.phase).toBe("error");
    expect(batteryChecklistOutcome("Ontbreekt", "192.168.1.3")?.message).toContain(
      "192.168.1.3",
    );
  });

  test("clearSetupPersistence removes wizard session keys", () => {
    sessionStorage.setItem(SETUP_STEP_STORAGE_KEY, "battery");
    sessionStorage.setItem(SETUP_BATTERY_IP_KEY, "192.168.1.1");
    clearSetupPersistence();
    expect(sessionStorage.getItem(SETUP_STEP_STORAGE_KEY)).toBeNull();
    expect(sessionStorage.getItem(SETUP_BATTERY_IP_KEY)).toBeNull();
  });

  test("interpretRestartResponse (#291 AC 11)", () => {
    expect(interpretRestartResponse(202, { boot_id: "abc" })).toEqual({
      kind: "started",
      bootId: "abc",
    });
    expect(interpretRestartResponse(409, { reason: "busy" })).toEqual({ kind: "busy" });
    expect(interpretRestartResponse(409, { reason: "not_supervised" })).toEqual({
      kind: "not_supervised",
    });
    expect(interpretRestartResponse(500, { detail: "kapot" }).kind).toBe("error");
  });
});
