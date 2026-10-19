import { describe, expect, it } from "vitest";

import { LIVE_WRITING } from "./labels";
import {
  liveWritingChip,
  liveWritingConditions,
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

  it("live + dry-run reason stays within two chips (cause is inside the one badge)", () => {
    // #314 merges the old run-mode-reason chip into run-mode-badge, so a reason
    // no longer spends a second slot of the B-99 budget.
    const ids = topbarStatusChipTestIds({
      hasStatus: true,
      dry_run_reason: "Settings watch-only is on.",
      dev_mode: "live",
      data_quality: "complete",
    });
    expect(ids).toEqual(["run-mode-badge"]);
    expect(ids).not.toContain("run-mode-reason");
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

  it("dry-run plus degraded quality is still ≤2 (merged cause chip)", () => {
    const ids = topbarStatusChipTestIds({
      hasStatus: true,
      dry_run_reason: "config.yaml forces watch-only",
      dev_mode: "live",
      data_quality: "degraded",
    });
    expect(ids).toEqual(["run-mode-badge", "data-quality"]);
    expect(ids.length).toBeLessThanOrEqual(2);
  });
});

describe("#314 liveWritingChip", () => {
  const watchOnly = "Live schrijven: NEE — Instellingen (Alleen kijken)";

  it("maps each floor, grace and live to text + data-cause", () => {
    const cases: Array<{
      name: string;
      status: Parameters<typeof liveWritingChip>[0];
      label: string;
      cause: string | null;
      state: string;
    }> = [
      {
        name: "mock",
        status: { dry_run: true, dry_run_cause: "mock", dev_mode: "mock", live_writing: false, live_writing_state: "no" },
        label: "Live schrijven: NEE — demo",
        cause: "mock",
        state: "no",
      },
      {
        name: "replay",
        status: { dry_run: true, dry_run_cause: "mock", dev_mode: "replay", live_writing: false, live_writing_state: "no" },
        label: "Live schrijven: NEE — demo",
        cause: "mock",
        state: "no",
      },
      {
        name: "yaml",
        status: { dry_run: true, dry_run_cause: "config_dry_run", dev_mode: "live", live_writing_state: "no" },
        label: "Live schrijven: NEE — config.yaml",
        cause: "config_dry_run",
        state: "no",
      },
      {
        name: "settings",
        status: { dry_run: true, dry_run_cause: "settings_dry_run", dev_mode: "live", live_writing_state: "no" },
        label: watchOnly,
        cause: "settings_dry_run",
        state: "no",
      },
      {
        name: "unarmed",
        status: { dry_run: true, dry_run_cause: "unarmed", dev_mode: "live", live_writing_state: "no" },
        label: "Live schrijven: NEE — niet bewapend",
        cause: "unarmed",
        state: "no",
      },
      {
        name: "prices",
        status: { dry_run: true, dry_run_cause: "no_live_prices", dev_mode: "live", live_writing_state: "no" },
        label: "Live schrijven: NEE — geen live prijzen",
        cause: "no_live_prices",
        state: "no",
      },
      {
        name: "devices",
        status: { dry_run: true, dry_run_cause: "no_live_devices", dev_mode: "live", live_writing_state: "no" },
        label: "Live schrijven: NEE — geen live apparaten",
        cause: "no_live_devices",
        state: "no",
      },
      {
        name: "observing",
        status: {
          dry_run: false,
          dry_run_cause: "observing",
          dry_run_reason: "Opstarten (1–2 min) — sensoren",
          live_writing: false,
          live_writing_state: "starting",
          dev_mode: "live",
        },
        label: LIVE_WRITING.starting,
        cause: "observing",
        state: "starting",
      },
      {
        name: "live",
        status: { dry_run: false, dry_run_cause: null, live_writing: true, live_writing_state: "yes", dev_mode: "live" },
        label: LIVE_WRITING.yes,
        cause: null,
        state: "yes",
      },
    ];
    for (const row of cases) {
      const chip = liveWritingChip(row.status);
      expect(chip.label, row.name).toBe(row.label);
      expect(chip.cause, row.name).toBe(row.cause);
      expect(chip.state, row.name).toBe(row.state);
      expect(chip.label, row.name).not.toContain("Stuurt batterij");
    }
  });

  it("during observing never says JA or Stuurt batterij, and grace differs from Watch-only", () => {
    const chip = liveWritingChip({
      dry_run: false,
      dry_run_cause: "observing",
      live_writing: true,
      live_writing_state: "yes",
      dev_mode: "live",
    });
    expect(chip.label).toBe(LIVE_WRITING.starting);
    expect(chip.label).not.toContain("Live schrijven: JA");
    expect(chip.label).not.toContain("Stuurt batterij");
    expect(chip.state).toBe("starting");
    expect(chip.label).not.toBe(watchOnly);
    expect(chip.label).not.toContain("Alleen kijken");
  });

  it("missing or unknown status fails safe to NEE", () => {
    expect(liveWritingChip(null).label).toBe(LIVE_WRITING.no);
    expect(liveWritingChip(undefined).label).toBe(LIVE_WRITING.no);
    expect(liveWritingChip({}).label).toBe(LIVE_WRITING.no);
    expect(liveWritingChip({ live_writing_state: "maybe", dry_run: null }).label).toBe(LIVE_WRITING.no);
    expect(liveWritingChip({ dry_run: true, live_writing_state: "yes" }).label).not.toContain("JA");
  });

  it("uses the decision idle reason as the tooltip only while NEE", () => {
    const idle = "Laatste keer: niets te doen, batterij blijft op zelfgebruik.";
    const chip = liveWritingChip(
      { dry_run: true, dry_run_cause: "unarmed", dry_run_reason: "Control is unarmed", live_writing_state: "no" },
      idle,
    );
    expect(chip.title).toBe(idle);
    const live = liveWritingChip(
      { dry_run: false, live_writing: true, live_writing_state: "yes" },
      idle,
    );
    expect(live.title).toBe(LIVE_WRITING.yesTitle);
    expect(live.title).not.toBe(idle);
  });
});

describe("#314 liveWritingConditions", () => {
  const ready = {
    "control.dry_run": false,
    "control.operational": true,
    "connection.use_live_devices": true,
    "meters.p1_ip": "192.0.2.10",
    "battery.indevolt_ip": "192.0.2.20",
    "connection.use_live_prices": true,
    "prices.tibber_token.__set": true,
  };
  const liveStatus = { dev_mode: "live", dry_run_cause: null, live_writing_state: "yes" };

  it("marks each condition independently and links to a section", () => {
    const rows = liveWritingConditions(ready, liveStatus);
    expect(rows.map((r) => [r.id, r.ok, r.section])).toEqual([
      ["yaml", true, "control"],
      ["watch_only", true, "control"],
      ["operational", true, "control"],
      ["devices", true, "connection"],
      ["prices", true, "prices"],
    ]);
    expect(rows.every((r) => r.detail.length > 0)).toBe(true);
    expect(rows.find((r) => r.id === "yaml")?.detail).toMatch(/config.yaml/);
  });

  it("fails closed when status or settings are missing", () => {
    const rows = liveWritingConditions(null, null);
    expect(rows.every((r) => r.ok === false)).toBe(true);
  });

  it("yaml is off for mock even when Settings look armed", () => {
    const rows = liveWritingConditions(ready, { dev_mode: "mock", dry_run_cause: "mock" });
    expect(rows.find((r) => r.id === "yaml")?.ok).toBe(false);
    expect(rows.find((r) => r.id === "watch_only")?.ok).toBe(true);
  });
});
