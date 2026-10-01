import type { ReactNode } from "react";
import type { SavedToday } from "./EnergyStory";
import type { Report } from "./HomeScores";
import { Icon } from "./icons";
import { HOME_TILE } from "./labels";
import { frameSavedToday } from "./savingsFraming";

function OutcomeTile({
  testId,
  label,
  value,
  title,
  onOpen,
  freshness,
  tone,
  icon,
}: {
  testId: string;
  label: string;
  value: string;
  title: string;
  onOpen?: () => void;
  freshness: TileFreshness;
  /** B-100: quiet honesty for measuring / no-benefit savings — never alarm. */
  tone?: "calm" | "positive" | null;
  icon?: ReactNode;
}) {
  const toneClass =
    tone === "calm" ? " is-calm" : tone === "positive" ? " is-positive" : "";
  const content = (
    <>
      <span className="outcome-tile-label">
        {icon}
        {label}
      </span>
      <span
        className={`outcome-tile-value${tone === "calm" ? " is-calm-copy" : ""}`}
        data-density-kind="number"
      >
        {value}
      </span>
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
      className={`outcome-tile outcome-tile-action${toneClass}`}
      data-testid={testId}
      data-tone={tone ?? undefined}
      data-density-kind="tile"
      title={title}
      onClick={onOpen}
    >
      {content}
    </button>
  ) : (
    <div
      className={`outcome-tile${toneClass}`}
      data-testid={testId}
      data-tone={tone ?? undefined}
      data-density-kind="tile"
      title={title}
    >
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
  const savings = frameSavedToday(savedToday);
  const savingsTone =
    savings.tone === "measuring" || savings.tone === "none"
      ? "calm"
      : savings.tone === "positive"
        ? "positive"
        : null;
  const savingsIcon =
    savings.icon != null ? (
      <span className="outcome-tile-icon-wrap" data-testid="outcome-savings-icon">
        <Icon name={savings.icon} className="outcome-tile-icon" />
      </span>
    ) : null;

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
        value={savings.value}
        title={savings.title}
        onOpen={savings.openFinance ? onOpenFinance : undefined}
        freshness={freshness.finance}
        tone={savingsTone}
        icon={savingsIcon}
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
