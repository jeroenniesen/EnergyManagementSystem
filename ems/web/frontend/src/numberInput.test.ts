import { describe, expect, it } from "vitest";

import { resolveNumberBlur } from "./numberInput";

const reserve = { min: 5, max: 50 };

describe("resolveNumberBlur (#366)", () => {
  it("restores on an empty field instead of jumping to min or 0", () => {
    expect(resolveNumberBlur("", reserve)).toEqual({ action: "restore" });
    expect(resolveNumberBlur("", { min: 0, max: 20 })).toEqual({ action: "restore" });
  });

  it("restores on whitespace instead of treating it as 0", () => {
    expect(resolveNumberBlur("   ", reserve)).toEqual({ action: "restore" });
    expect(resolveNumberBlur("\t\n", { min: 0, max: 50 })).toEqual({ action: "restore" });
  });

  it("parses a comma and a dot as the same decimal", () => {
    expect(resolveNumberBlur("1,5", { min: 0, max: 20 })).toEqual({ action: "commit", value: 1.5 });
    expect(resolveNumberBlur("1.5", { min: 0, max: 20 })).toEqual({ action: "commit", value: 1.5 });
    expect(resolveNumberBlur("  2,25 ", { min: 0, max: 20 })).toEqual({ action: "commit", value: 2.25 });
  });

  it("reports out of range without snapping to min or max", () => {
    expect(resolveNumberBlur("1", reserve)).toEqual({ action: "out_of_range", value: 1 });
    expect(resolveNumberBlur("80", reserve)).toEqual({ action: "out_of_range", value: 80 });
    expect(resolveNumberBlur("4,5", reserve)).toEqual({ action: "out_of_range", value: 4.5 });
  });

  it("commits values on the inclusive min and max", () => {
    expect(resolveNumberBlur("5", reserve)).toEqual({ action: "commit", value: 5 });
    expect(resolveNumberBlur("50", reserve)).toEqual({ action: "commit", value: 50 });
    expect(resolveNumberBlur("0", { min: 0, max: 50 })).toEqual({ action: "commit", value: 0 });
  });

  it("rounds integer fields after parsing the decimal separator", () => {
    expect(resolveNumberBlur("1,6", { min: 0, max: 10, integer: true })).toEqual({
      action: "commit",
      value: 2,
    });
    expect(resolveNumberBlur("0.4", { min: 1, max: 10, integer: true })).toEqual({
      action: "out_of_range",
      value: 0,
    });
  });

  it("restores text that is not a number", () => {
    expect(resolveNumberBlur("abc", reserve)).toEqual({ action: "restore" });
    expect(resolveNumberBlur("1.2.3", reserve)).toEqual({ action: "restore" });
    expect(resolveNumberBlur("1,2,3", { min: 0, max: 20 })).toEqual({ action: "restore" });
  });
});
