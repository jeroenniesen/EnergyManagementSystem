// B-63 / #88: show how likely the battery is to cover tonight's evening peak.
// Reads `/api/battery-plan`.evening_peak_coverage — hidden when unavailable.

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

export function EveningPeakCoverageCard({
  coverage,
}: {
  coverage: EveningPeakCoverage | null | undefined;
}) {
  if (!coverage?.available || coverage.probability == null) return null;
  const pct = formatPeakCoveragePct(coverage.probability);
  if (!pct) return null;

  return (
    <section
      className="evening-peak-coverage"
      data-testid="evening-peak-coverage"
      data-calibrated={coverage.calibrated ? "true" : "false"}
      title={coverage.reason}
    >
      <p className="evening-peak-coverage-label" data-testid="evening-peak-coverage-label">
        {coverage.label}
      </p>
      <p className="evening-peak-coverage-detail" data-testid="evening-peak-coverage-detail">
        Chance the battery covers the evening peak: <strong>{pct}</strong>
        {coverage.calibrated ? "" : " (default bands until more forecast history)"}
      </p>
    </section>
  );
}
