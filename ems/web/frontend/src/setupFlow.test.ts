import { describe, expect, test } from "vitest";

import {
  batteryChecklistOutcome,
  buildBatteryLinkPayload,
  buildP1LinkPayload,
  clearSetupPersistence,
  interpretRestartResponse,
  needsP1IpField,
  p1ChecklistOutcome,
  P1_RESTART_MESSAGE,
  privateIpv4Hint,
  SETUP_BATTERY_IP_KEY,
  SETUP_P1_IP_KEY,
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

  test("buildP1LinkPayload (#292)", () => {
    expect(buildP1LinkPayload("192.168.1.20", "")).toEqual({ "meters.p1_ip": "192.168.1.20" });
    expect(buildP1LinkPayload("192.168.1.20", "10.0.0.2")).toEqual({
      "meters.p1_ip": "192.168.1.20",
    });
    expect(buildP1LinkPayload("10.0.0.2", "10.0.0.2")).toBeNull();
    expect(buildP1LinkPayload("", "192.168.1.5")).toBeNull();
  });

  test("p1ChecklistOutcome (#292 AC 3)", () => {
    expect(p1ChecklistOutcome("Werkt", "Werkt", "192.168.1.2")?.phase).toBe("success");
    expect(p1ChecklistOutcome("Ontbreekt", "Ontbreekt", "192.168.1.2")?.linkBattery).toBe(true);
    expect(p1ChecklistOutcome("Ontbreekt", "Ontbreekt", "192.168.1.2")?.message).toBe(
      "Koppel eerst je batterij",
    );
    const err = p1ChecklistOutcome("Ontbreekt", "Werkt", "192.168.3.3");
    expect(err?.phase).toBe("error");
    expect(err?.message).toBe(
      "P1-meter niet gevonden op 192.168.3.3. Staan de meter en EMS op hetzelfde netwerk?",
    );
  });

  test("P1 restart copy (#292 AC 2)", () => {
    expect(P1_RESTART_MESSAGE).toBe(
      "EMS start opnieuw om je P1-meter te zoeken. Dit duurt even, laat dit scherm open.",
    );
  });

  test("privateIpv4Hint P1 empty message", () => {
    expect(privateIpv4Hint("", "p1")).toMatch(/P1-meter/);
  });

  test("clearSetupPersistence clears P1 key (#292)", () => {
    sessionStorage.setItem(SETUP_P1_IP_KEY, "192.168.1.9");
    clearSetupPersistence();
    expect(sessionStorage.getItem(SETUP_P1_IP_KEY)).toBeNull();
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
