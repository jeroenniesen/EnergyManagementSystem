import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import {
  EveningPeakCoverageCard,
  EVENING_PEAK_COVERED_THRESHOLD,
  EVENING_PEAK_TRUST_MARKER,
  applyEveningPeakTrustMarkerPolicy,
  formatPeakCoveragePct,
  isEveningPeakCovered,
  shouldShowEveningPeakCoverageCard,
  type EveningPeakCoverage,
} from "./EveningPeakCoverage";

describe("formatPeakCoveragePct", () => {
  it("formats a probability as a percent", () => {
    expect(formatPeakCoveragePct(0.666)).toBe("67%");
    expect(formatPeakCoveragePct(1)).toBe("100%");
    expect(formatPeakCoveragePct(0)).toBe("0%");
  });

  it("returns null when unavailable", () => {
    expect(formatPeakCoveragePct(null)).toBeNull();
    expect(formatPeakCoveragePct(undefined)).toBeNull();
    expect(formatPeakCoveragePct(Number.NaN)).toBeNull();
  });
});

describe("EveningPeakCoverage contract", () => {
  it("accepts the API shape used by /api/battery-plan", () => {
    const sample: EveningPeakCoverage = {
      probability: 0.667,
      available: true,
      label: "Evening peak may be covered (~67%)",
      reason: "2 of 3 scenarios cover the evening peak.",
      calibrated: true,
      scenarios_covering: 2,
      scenarios_total: 3,
      peak_kwh_expected: 3.2,
      available_kwh: 4.1,
    };
    expect(formatPeakCoveragePct(sample.probability)).toBe("67%");
  });
});

describe("B-95 evening-peak single surface", () => {
  const atRisk: EveningPeakCoverage = {
    probability: 0.667,
    available: true,
    label: "Evening peak may be covered (~67%)",
    reason: "2 of 3 scenarios cover the evening peak.",
    calibrated: true,
    scenarios_covering: 2,
    scenarios_total: 3,
    peak_kwh_expected: 3.2,
    available_kwh: 4.1,
  };
  const covered: EveningPeakCoverage = {
    ...atRisk,
    probability: 1,
    label: "Evening peak likely covered (~100%)",
    scenarios_covering: 3,
  };
  const likelyCovered: EveningPeakCoverage = {
    ...atRisk,
    probability: EVENING_PEAK_COVERED_THRESHOLD,
    label: "Evening peak likely covered (~85%)",
  };

  it("treats ≥85% (and 100%) as covered", () => {
    expect(isEveningPeakCovered(1)).toBe(true);
    expect(isEveningPeakCovered(0.85)).toBe(true);
    expect(isEveningPeakCovered(0.849)).toBe(false);
    expect(isEveningPeakCovered(0.667)).toBe(false);
    expect(isEveningPeakCovered(null)).toBe(false);
  });

  it("shows the risk banner when coverage is below threshold", () => {
    expect(shouldShowEveningPeakCoverageCard(atRisk, { heroOwnsCoveredMarker: true })).toBe(
      true,
    );
    expect(shouldShowEveningPeakCoverageCard(atRisk, { heroOwnsCoveredMarker: false })).toBe(
      true,
    );
  });

  it("hides the banner when covered and the hero owns the trust-marker", () => {
    expect(
      shouldShowEveningPeakCoverageCard(covered, { heroOwnsCoveredMarker: true }),
    ).toBe(false);
    expect(
      shouldShowEveningPeakCoverageCard(likelyCovered, { heroOwnsCoveredMarker: true }),
    ).toBe(false);
  });

  it("keeps a sole covered surface when the hero cannot show markers", () => {
    expect(
      shouldShowEveningPeakCoverageCard(covered, { heroOwnsCoveredMarker: false }),
    ).toBe(true);
  });

  it("hides when unavailable", () => {
    expect(
      shouldShowEveningPeakCoverageCard(
        { ...atRisk, available: false, probability: null },
        { heroOwnsCoveredMarker: false },
      ),
    ).toBe(false);
  });

  it("renders risk markup and suppresses covered when hero owns the marker", () => {
    const riskHtml = renderToStaticMarkup(
      createElement(EveningPeakCoverageCard, {
        coverage: atRisk,
        heroOwnsCoveredMarker: true,
      }),
    );
    expect(riskHtml).toContain('data-testid="evening-peak-coverage"');
    expect(riskHtml).toContain('data-risk="true"');
    expect(riskHtml).toContain("67%");

    const coveredHtml = renderToStaticMarkup(
      createElement(EveningPeakCoverageCard, {
        coverage: covered,
        heroOwnsCoveredMarker: true,
      }),
    );
    expect(coveredHtml).toBe("");
  });

  it("exports the hero trust-marker string used by App", () => {
    expect(EVENING_PEAK_TRUST_MARKER).toBe("Batterij dekt de avondpiek");
  });

  it("strips the covers chip when coverage is at risk (no stack with risk banner)", () => {
    const withApiChip = [
      "Reserve respected",
      "Battery covers the evening peak",
      EVENING_PEAK_TRUST_MARKER,
      "No grid top-up needed",
    ];
    expect(applyEveningPeakTrustMarkerPolicy(withApiChip, atRisk)).toEqual([
      "Reserve respected",
      "No grid top-up needed",
    ]);
    expect(
      applyEveningPeakTrustMarkerPolicy(withApiChip, atRisk, {
        injectCoveredMarker: true,
      }),
    ).toEqual(["Reserve respected", "No grid top-up needed"]);
  });

  it("injects the covers chip once when covered and the hero owns it", () => {
    expect(
      applyEveningPeakTrustMarkerPolicy(["Reserve respected"], covered, {
        injectCoveredMarker: true,
      }),
    ).toEqual(["Reserve respected", EVENING_PEAK_TRUST_MARKER]);
    expect(
      applyEveningPeakTrustMarkerPolicy(
        ["Reserve respected", "Battery covers the evening peak"],
        covered,
        { injectCoveredMarker: true },
      ),
    ).toEqual(["Reserve respected", EVENING_PEAK_TRUST_MARKER]);
    expect(
      applyEveningPeakTrustMarkerPolicy(
        ["Reserve respected", EVENING_PEAK_TRUST_MARKER],
        covered,
        { injectCoveredMarker: true },
      ),
    ).toEqual(["Reserve respected", EVENING_PEAK_TRUST_MARKER]);
  });

  it("leaves markers alone when coverage is unavailable", () => {
    const markers = ["Reserve respected", EVENING_PEAK_TRUST_MARKER];
    expect(
      applyEveningPeakTrustMarkerPolicy(markers, {
        ...atRisk,
        available: false,
        probability: null,
      }),
    ).toEqual(markers);
  });
});
