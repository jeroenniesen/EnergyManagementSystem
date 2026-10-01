// B-63 / #88: show how likely the battery is to cover tonight's evening peak.
// Reads `/api/battery-plan`.evening_peak_coverage — hidden when unavailable.
//
// B-95: covered evening peak appears once above the fold via the **hero trust-marker**.
// The full banner is only for at-risk coverage (probability below the covered threshold).
// When coverage is covered/≥85% and the hero can show trust markers, the banner is
// suppressed so we never stack marker + banner. When coverage is at risk, strip any API
// "covers" chip so only the amber banner remains.
// B-98: Dutch-first chrome via labels.ts (API trust-marker strings may still be EN).

import { HOME_EVENING_PEAK } from "./labels";

/** Hero trust-marker when evening peak is covered (B-95 + B-98 Dutch-first). */
export const EVENING_PEAK_TRUST_MARKER = HOME_EVENING_PEAK.trustMarker;

/** Legacy EN string still emitted by energy-story / api `_trust_markers` — strip or replace. */
const EVENING_PEAK_TRUST_MARKER_EN = "Battery covers the evening peak";

function isEveningPeakCoversChip(marker: string): boolean {
  return marker === EVENING_PEAK_TRUST_MARKER || marker === EVENING_PEAK_TRUST_MARKER_EN;
}

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

/**
 * B-95: keep evening-peak reassurance to one surface.
 *
 * The energy-story API appends {@link EVENING_PEAK_TRUST_MARKER} whenever any projected
 * slot is DISCHARGE_FOR_LOAD — that does **not** mean coverage ≥85%. When calibrated
 * coverage is available and below the covered threshold, strip that chip so it cannot
 * stack with the amber risk banner. When covered, optionally inject the marker so the
 * hero owns the single calm reassurance surface.
 */
export function applyEveningPeakTrustMarkerPolicy(
  markers: readonly string[],
  coverage: EveningPeakCoverage | null | undefined,
  opts: { injectCoveredMarker?: boolean } = {},
): string[] {
  const out = [...markers];
  if (
    !coverage?.available ||
    coverage.probability == null ||
    !Number.isFinite(coverage.probability)
  ) {
    return out;
  }
  if (isEveningPeakCovered(coverage.probability)) {
    const hadChip = out.some(isEveningPeakCoversChip);
    const withoutLegacy = out.filter((marker) => !isEveningPeakCoversChip(marker));
    // Always collapse EN/NL covers chips to a single Dutch marker. When inject is on,
    // append even if the API omitted the chip so the hero owns one reassurance surface.
    if (opts.injectCoveredMarker || hadChip) {
      withoutLegacy.push(EVENING_PEAK_TRUST_MARKER);
    }
    return withoutLegacy;
  }
  // At risk: risk banner only — never keep the discharge-based "covers" chip (EN or NL).
  return out.filter((marker) => !isEveningPeakCoversChip(marker));
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
        {HOME_EVENING_PEAK.chancePrefix} <strong>{pct}</strong>
        {c.calibrated ? "" : HOME_EVENING_PEAK.uncalibratedSuffix}
      </p>
    </section>
  );
}
