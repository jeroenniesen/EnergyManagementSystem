import type { SavedToday } from "./EnergyStory";
import type { Report } from "./HomeScores";
import { HOME_TILE } from "./labels";

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
      {/* B-94: show freshness only when this tile's signal is stale — never identical
          "Updated …" under all four tiles when healthy. */}
      {freshness.stale && freshness.updatedAt != null && (
        <span className="outcome-tile-freshness is-stale" data-testid={`${testId}-freshness`}>
          {HOME_TILE.staleUpdated}{" "}
          {new Date(freshness.updatedAt).toLocaleTimeString([], {
            hour: "2-digit",
            minute: "2-digit",
          })}
        </span>
      )}
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
    <section className="outcome-tiles" data-testid="outcome-tiles" aria-label={HOME_TILE.sectionAria}>
      <OutcomeTile
        testId="outcome-solar-score"
        label={HOME_TILE.solarScore.label}
        value={solarScore?.value == null ? "—" : String(solarScore.value)}
        title={solarScore?.value == null ? HOME_TILE.solarScore.unavailable : HOME_TILE.solarScore.title}
        onOpen={solarScore?.value == null ? undefined : onOpenInsights}
        freshness={freshness.report}
      />
      <OutcomeTile
        testId="outcome-soc"
        label={HOME_TILE.soc.label}
        value={socPct == null ? "—" : `${Math.round(socPct)}%`}
        title={socPct == null ? HOME_TILE.soc.unavailable : HOME_TILE.soc.title}
        onOpen={socPct == null ? undefined : onOpenBattery}
        freshness={freshness.status}
      />
      <OutcomeTile
        testId="outcome-savings"
        label={HOME_TILE.savings.label}
        value={savings}
        title={
          savedToday?.status === "measured"
            ? HOME_TILE.savings.title
            : savedToday?.status === "measuring"
              ? HOME_TILE.savings.measuring
              : HOME_TILE.savings.unavailable
        }
        onOpen={savedToday?.status === "measured" ? onOpenFinance : undefined}
        freshness={freshness.finance}
      />
      <OutcomeTile
        testId="outcome-grid-import"
        label={HOME_TILE.gridImport.label}
        value={gridImport == null ? "—" : `${gridImport.toFixed(1)} kWh`}
        title={
          gridImport == null ? HOME_TILE.gridImport.unavailable : HOME_TILE.gridImport.title
        }
        onOpen={gridImport == null ? undefined : onOpenInsights}
        freshness={freshness.report}
      />
    </section>
  );
}
