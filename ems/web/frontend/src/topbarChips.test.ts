import { describe, expect, it } from "vitest";

import {
  showTopbarDataQuality,
  showTopbarDataSource,
  topbarStatusChipTestIds,
} from "./topbarChips";

describe("B-99 topbar chip budget", () => {
  it("hides data-quality when complete", () => {
    expect(showTopbarDataQuality("complete")).toBe(false);
    expect(showTopbarDataQuality(null)).toBe(false);
    expect(showTopbarDataQuality(undefined)).toBe(false);
  });

  it("shows data-quality for attention states", () => {
    expect(showTopbarDataQuality("degraded")).toBe(true);
    expect(showTopbarDataQuality("price_fallback")).toBe(true);
    expect(showTopbarDataQuality("unsafe")).toBe(true);
  });

  it("demotes Live sensoren; keeps Demo / non-live", () => {
    expect(showTopbarDataSource("live")).toBe(false);
    expect(showTopbarDataSource("mock")).toBe(true);
    expect(showTopbarDataSource("replay")).toBe(true);
    expect(showTopbarDataSource(null)).toBe(false);
  });

  it("healthy live session shows ≤2 chips (run-mode only)", () => {
    const ids = topbarStatusChipTestIds({
      hasStatus: true,
      dry_run_reason: null,
      dev_mode: "live",
      data_quality: "complete",
    });
    expect(ids).toEqual(["run-mode-badge"]);
    expect(ids.length).toBeLessThanOrEqual(2);
  });

  it("live + dry-run reason stays within two chips (run-mode + attention)", () => {
    const ids = topbarStatusChipTestIds({
      hasStatus: true,
      dry_run_reason: "Settings watch-only is on.",
      dev_mode: "live",
      data_quality: "complete",
    });
    expect(ids).toEqual(["run-mode-badge", "run-mode-reason"]);
    expect(ids.length).toBeLessThanOrEqual(2);
  });

  it("live + degraded quality shows run-mode + data-quality (≤2)", () => {
    const ids = topbarStatusChipTestIds({
      hasStatus: true,
      dry_run_reason: null,
      dev_mode: "live",
      data_quality: "degraded",
    });
    expect(ids).toEqual(["run-mode-badge", "data-quality"]);
    expect(ids.length).toBeLessThanOrEqual(2);
  });

  it("mock/Demo still surfaces the Demo chip (not a healthy-live session)", () => {
    const ids = topbarStatusChipTestIds({
      hasStatus: true,
      dry_run_reason: null,
      dev_mode: "mock",
      data_quality: "complete",
    });
    expect(ids).toEqual(["run-mode-badge", "data-source"]);
  });
});
