// B-63 / #88: show how likely the battery is to cover tonight's evening peak.
// Reads `/api/battery-plan`.evening_peak_coverage — hidden when unavailable.
//
// B-95: covered evening peak appears once above the fold via the **hero trust-marker**
// ("Battery covers the evening peak"). The full banner is only for at-risk coverage
// (probability below the covered threshold). When coverage is covered/≥85% and the hero
// can show trust markers, the banner is suppressed so we never stack marker + banner.

/** Matches api.py `_trust_markers` / energy-story copy. */
export const EVENING_PEAK_TRUST_MARKER = "Battery covers the evening peak";

/**
 * Same band as backend `_label_for` "Evening peak likely covered" (≥85%).
 * At or above this, reassurance lives on the hero trust-marker — not the banner.
 */
export const EVENING_PEAK_COVERED_THRESHOLD = 0.85;

export type EveningPeakCoverage = {
  probability: number | null;
  available: boolean;
  label: string;
  reason: string;
  calibrated: boolean;
  scenarios_covering: number;
  scenarios_total: number;
  peak_kwh_expected: number | null;
  available_kwh: number | null;
};

export function formatPeakCoveragePct(probability: number | null | undefined): string | null {
  if (probability == null || !Number.isFinite(probability)) return null;
  const pct = Math.round(Math.max(0, Math.min(1, probability)) * 100);
  return `${pct}%`;
}

/** True when calibrated-band coverage is "likely covered" / 100%. */
export function isEveningPeakCovered(probability: number | null | undefined): boolean {
  return (
    probability != null &&
    Number.isFinite(probability) &&
    probability >= EVENING_PEAK_COVERED_THRESHOLD
  );
}

/**
 * Whether the full EveningPeakCoverageCard should render.
 *
 * - Unavailable / null probability → hide
 * - Covered + hero can own the trust-marker → hide (B-95: one reassurance surface)
 * - Covered + no hero → show card as the sole surface (no triple stack possible)
 * - Below threshold → show risk banner
 */
export function shouldShowEveningPeakCoverageCard(
  coverage: EveningPeakCoverage | null | undefined,
  opts: { heroOwnsCoveredMarker?: boolean } = {},
): boolean {
  if (!coverage?.available || coverage.probability == null) return false;
  if (!Number.isFinite(coverage.probability)) return false;
  if (isEveningPeakCovered(coverage.probability) && opts.heroOwnsCoveredMarker) {
    return false;
  }
  return true;
}

export function EveningPeakCoverageCard({
  coverage,
  heroOwnsCoveredMarker = false,
}: {
  coverage: EveningPeakCoverage | null | undefined;
  /** When true, covered/100% is already (or will be) shown as a hero trust-marker. */
  heroOwnsCoveredMarker?: boolean;
}) {
  if (!shouldShowEveningPeakCoverageCard(coverage, { heroOwnsCoveredMarker })) {
    return null;
  }
  // coverage is defined + available + finite probability after the guard above
  const c = coverage!;
  const pct = formatPeakCoveragePct(c.probability);
  if (!pct) return null;

  const atRisk = !isEveningPeakCovered(c.probability);

  return (
    <section
      className={`evening-peak-coverage${atRisk ? " evening-peak-coverage--risk" : ""}`}
      data-testid="evening-peak-coverage"
      data-calibrated={c.calibrated ? "true" : "false"}
      data-risk={atRisk ? "true" : "false"}
      title={c.reason}
    >
      <p className="evening-peak-coverage-label" data-testid="evening-peak-coverage-label">
        {c.label}
      </p>
      <p className="evening-peak-coverage-detail" data-testid="evening-peak-coverage-detail">
        Chance the battery covers the evening peak: <strong>{pct}</strong>
        {c.calibrated ? "" : " (default bands until more forecast history)"}
      </p>
    </section>
  );
}
