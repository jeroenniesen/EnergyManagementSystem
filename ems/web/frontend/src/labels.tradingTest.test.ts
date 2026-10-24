import { describe, expect, it } from "vitest";

import {
  TRADING_TEST_BATTERY,
  tradingTestBlockReason,
  tradingTestBlockedByStatus,
  tradingTestShowBlockReason,
  tradingTestStartDisabled,
} from "./labels";

describe("TRADING_TEST_BATTERY (#367)", () => {
  it("exposes the Dutch consequence sentence", () => {
    expect(TRADING_TEST_BATTERY.consequence).toContain("2 minuten");
    expect(TRADING_TEST_BATTERY.consequence).toContain("reservevloer");
  });

  it("tradingTestBlockedByStatus follows writes_allowed + test_battery_allowed", () => {
    expect(
      tradingTestBlockedByStatus({
        writes_allowed: false,
        block_reason: "watch",
        test_battery_allowed: undefined,
      }),
    ).toBe(true);
    expect(
      tradingTestBlockedByStatus({
        writes_allowed: true,
        block_reason: null,
        test_battery_allowed: false,
        test_battery_block_reason: "car",
      }),
    ).toBe(true);
    expect(
      tradingTestBlockedByStatus({
        writes_allowed: true,
        block_reason: null,
        test_battery_allowed: true,
      }),
    ).toBe(false);
  });

  it("tradingTestBlockReason prefers test-specific reason then block_reason", () => {
    expect(
      tradingTestBlockReason({
        writes_allowed: false,
        block_reason: "yaml floor",
        test_battery_block_reason: "unsafe data",
      }),
    ).toBe("unsafe data");
    expect(
      tradingTestBlockReason({
        writes_allowed: false,
        block_reason: "yaml floor",
        test_battery_block_reason: null,
      }),
    ).toBe("yaml floor");
  });

  it("tradingTestShowBlockReason is false when only busy would disable (writes ok)", () => {
    expect(
      tradingTestShowBlockReason({
        writes_allowed: true,
        block_reason: null,
        test_battery_allowed: true,
      }),
    ).toBe(false);
    expect(
      tradingTestShowBlockReason({
        writes_allowed: false,
        block_reason: "Watch only",
      }),
    ).toBe(true);
  });

  it("tradingTestStartDisabled blocks on writes_allowed and probe", () => {
    const ok = {
      writes_allowed: true,
      block_reason: null,
      test_battery_allowed: true,
    };
    expect(
      tradingTestStartDisabled(ok, { canOperate: true, busy: false, probeActive: false }),
    ).toBe(false);
    expect(
      tradingTestStartDisabled(
        { ...ok, writes_allowed: false },
        { canOperate: true, busy: false },
      ),
    ).toBe(true);
    expect(
      tradingTestStartDisabled(ok, { canOperate: true, busy: false, probeActive: true }),
    ).toBe(true);
  });
});
