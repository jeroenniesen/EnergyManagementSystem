import type { SavedToday } from "./BatteryPlan";
import type { Report } from "./HomeScores";

function OutcomeTile({
  testId,
  label,
  value,
  title,
  onOpen,
  freshness,
}: {
  testId: string;
  label: string;
  value: string;
  title: string;
  onOpen?: () => void;
  freshness: TileFreshness;
}) {
  const content = (
    <>
      <span className="outcome-tile-label">{label}</span>
      <span className="outcome-tile-value" data-density-kind="number">{value}</span>
      {freshness.updatedAt != null && <span className={`outcome-tile-freshness${freshness.stale ? " is-stale" : ""}`}>
        {freshness.stale ? "Stale · " : ""}Updated {new Date(freshness.updatedAt).toLocaleTimeString([], {
          hour: "2-digit", minute: "2-digit",
        })}
      </span>}
    </>
  );

  return onOpen ? (
    <button
      type="button"
      className="outcome-tile outcome-tile-action"
      data-testid={testId}
      data-density-kind="tile"
      title={title}
      onClick={onOpen}
    >
      {content}
    </button>
  ) : (
    <div className="outcome-tile" data-testid={testId} data-density-kind="tile" title={title}>
      {content}
    </div>
  );
}

export type TileFreshness = { updatedAt: number | null; stale: boolean };

export function OutcomeTiles({
  report,
  savedToday,
  socPct,
  onOpenInsights,
  onOpenFinance,
  onOpenBattery,
  freshness,
}: {
  report: Report | null;
  savedToday: SavedToday | null;
  socPct: number | null;
  onOpenInsights: () => void;
  onOpenFinance: () => void;
  onOpenBattery?: () => void;
  freshness: { report: TileFreshness; status: TileFreshness; finance: TileFreshness };
}) {
  const solarScore = report?.scores.find((score) => score.key === "self_consumption") ?? null;
  const gridImport = report?.flows?.grid_import_kwh;
  const savings = savedToday?.status === "measured" ? `€${savedToday.eur.toFixed(2)}` : "—";

  return (
    <section className="outcome-tiles" data-testid="outcome-tiles" aria-label="Today so far">
      <OutcomeTile
        testId="outcome-solar-score"
        label="Solar score"
        value={solarScore?.value == null ? "—" : String(solarScore.value)}
        title={solarScore?.value == null ? "Solar score not available" : "Solar score today so far"}
        onOpen={solarScore?.value == null ? undefined : onOpenInsights}
        freshness={freshness.report}
      />
      <OutcomeTile
        testId="outcome-soc"
        label="Battery level"
        value={socPct == null ? "—" : `${socPct}%`}
        title={socPct == null ? "Live battery level not available" : "Live battery level"}
        onOpen={socPct == null ? undefined : onOpenBattery}
        freshness={freshness.status}
      />
      <OutcomeTile
        testId="outcome-savings"
        label="Saved"
        value={savings}
        title={
          savedToday?.status === "measured"
            ? "Savings today so far"
            : savedToday?.status === "measuring"
              ? "Savings today so far: still measuring"
              : "Savings today so far not available"
        }
        onOpen={savedToday?.status === "measured" ? onOpenFinance : undefined}
        freshness={freshness.finance}
      />
      <OutcomeTile
        testId="outcome-grid-import"
        label="Grid import"
        value={gridImport == null ? "—" : `${gridImport.toFixed(1)} kWh`}
        title={gridImport == null ? "Grid import today so far not available" : "Grid import today so far"}
        onOpen={gridImport == null ? undefined : onOpenInsights}
        freshness={freshness.report}
      />
    </section>
  );
}
