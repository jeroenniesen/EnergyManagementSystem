import { Fragment, useState } from "react";

import type { EnergyStoryData, PlanProvenance } from "./EnergyStory";
import { HOME_PLAN_STORY, INTELLIGENCE_COPY, PLANNER_PROVENANCE_LABEL } from "./labels";
import {
  ACTION_META,
  buildPlanStoryModel,
  canonicalAction,
  formatClock,
  hoverIndexAtX,
  SLOT_MS,
  slotTipRows,
} from "./planStoryModel";

const W = 1000;
const H = 360;
const PAD = { l: 58, r: 62, t: 30, b: 56 };
const PLOT_W = W - PAD.l - PAD.r;
const PLOT_H = H - PAD.t - PAD.b;
const PRICE_BAND_HEIGHT = PLOT_H * 0.24;
const PRICE_BAND_TOP = PAD.t + PLOT_H - PRICE_BAND_HEIGHT;
const ACTION_Y = PAD.t + PLOT_H + 10;
const ACTION_H = 9;
const ACTION_PATTERN_ANGLE = {
  hold: 0,
  solar_charge: 30,
  grid_charge: 60,
  discharge: 90,
  self_consume: 120,
  idle: 150,
} as const;

type PlanStoryProvenance =
  Partial<Omit<PlanProvenance, "intelligence">> & {
    intelligence?: Partial<PlanProvenance["intelligence"]>;
  };

export type PlanStoryProps = {
  story: EnergyStoryData | null;
  provenance?: PlanStoryProvenance | null;
  /** B-96: Last-3h review demoted behind a plan-header disclosure (not in the hero). */
  recentReview?: string | null;
};

function RecentReviewDisclosure({ message }: { message: string }) {
  return (
    <details className="plan-story-recent-review" data-testid="recent-review">
      <summary className="plan-story-recent-review-toggle">{HOME_PLAN_STORY.recentReview}</summary>
      <p className="story-review plan-story-recent-review-body">{message}</p>
    </details>
  );
}

function ProvenanceCaption({
  provenance,
}: {
  provenance: PlanStoryProvenance | null;
}) {
  const clauses: Array<{ key: string; text: string; title?: string }> = [];
  if (
    provenance?.forecast_source &&
    typeof provenance.solar_confidence_pct === "number" &&
    Number.isFinite(provenance.solar_confidence_pct)
  ) {
    clauses.push({
      key: "forecast",
      text: `${provenance.forecast_source} at ${provenance.solar_confidence_pct}% confidence`,
      title: "The live solar forecast source feeding today's plan.",
    });
  }
  if (provenance?.planner) {
    clauses.push({
      key: "planner",
      text: PLANNER_PROVENANCE_LABEL[provenance.planner] ?? provenance.planner,
      title:
        "Which planner actually produced this plan — the dependable rule-based/adaptive " +
        "baseline, not a black box.",
    });
  }
  if (provenance?.intelligence?.state) {
    clauses.push({
      key: "intelligence",
      text:
        "scenario intelligence: " +
        (INTELLIGENCE_COPY[provenance.intelligence.state]?.short ??
          provenance.intelligence.state),
      title:
        provenance.intelligence.reason ??
        INTELLIGENCE_COPY[provenance.intelligence.state]?.detail,
    });
  }
  if (clauses.length === 0) return null;

  return (
    <p className="battery-plan-provenance" data-testid="battery-plan-provenance">
      {HOME_PLAN_STORY.plannedWith}{" "}
      {clauses.map((clause, index) => (
        <Fragment key={clause.key}>
          {index > 0 && " · "}
          <span title={clause.title}>{clause.text}</span>
        </Fragment>
      ))}
    </p>
  );
}

export function PlanStory({
  story,
  provenance = null,
  recentReview = null,
}: PlanStoryProps) {
  const [hover, setHover] = useState<number | null>(null);
  const model = buildPlanStoryModel(story, PAD.l, PLOT_W);

  if (!story || !model) {
    return (
      <section className="plan-story" data-testid="plan-story" data-density-kind="chart">
        {recentReview && <RecentReviewDisclosure message={recentReview} />}
        <p>{HOME_PLAN_STORY.unavailable}</p>
        <ProvenanceCaption provenance={provenance} />
      </section>
    );
  }

  const socY = (value: number) =>
    PAD.t + (1 - Math.max(0, Math.min(100, value)) / 100) * PLOT_H;
  const solarY = (value: number) =>
    PAD.t + PLOT_H - (Math.max(0, value) / model.maxSolar) * PLOT_H * 0.38;
  const priceRange = Math.max(0.01, model.priceScaleMax - model.priceScaleMin);
  const priceY = (value: number) =>
    PRICE_BAND_TOP +
    ((model.priceScaleMax - value) / priceRange) * PRICE_BAND_HEIGHT;
  const hovered = hover == null ? null : model.slots[hover] ?? null;
  const hoveredX = hovered
    ? model.scale.x(hovered.startMs + SLOT_MS / 2)
    : null;
  const presentActions = [...new Set(model.slots.map((slot) => canonicalAction(slot.action)))];

  const onMove = (event: React.MouseEvent<SVGSVGElement>) => {
    const rect = event.currentTarget.getBoundingClientRect();
    const px = ((event.clientX - rect.left) / rect.width) * W;
    setHover(hoverIndexAtX(model.scale, model.slots, px));
  };

  return (
    <section
      className="plan-story"
      data-testid="plan-story"
      data-density-kind="chart"
      aria-label={model.label}
    >
      {recentReview && <RecentReviewDisclosure message={recentReview} />}
      <p className="sr-only" data-testid="plan-story-summary">{model.summary}</p>
      <div className="plan-story-chart-wrap">
        <svg
          className="plan-story-svg"
          data-testid="plan-story-plot"
          viewBox={`0 0 ${W} ${H}`}
          aria-hidden="true"
          onMouseMove={onMove}
          onMouseLeave={() => setHover(null)}
        >
          <defs>
            {Object.entries(ACTION_PATTERN_ANGLE).map(([action, angle]) => (
              <pattern
                key={action}
                id={`plan-story-ribbon-${action}`}
                width="6"
                height="6"
                patternUnits="userSpaceOnUse"
                patternTransform={`rotate(${angle})`}
              >
                <rect
                  width="6"
                  height="6"
                  className={`plan-story-ribbon-base action-${action}`}
                />
                <line
                  x1="0"
                  y1="0"
                  x2="0"
                  y2="6"
                  className="plan-story-ribbon-line"
                />
              </pattern>
            ))}
          </defs>

          <g className="plan-story-prices" data-testid="plan-story-prices">
            <line
              data-price-zero
              x1={PAD.l}
              x2={W - PAD.r}
              y1={priceY(0)}
              y2={priceY(0)}
            />
            {model.spans.map((span) => {
              const price = model.slots[span.index].eur_per_kwh;
              return typeof price === "number" && Number.isFinite(price) ? (
                <rect
                  key={span.startMs}
                  data-testid="plan-story-price"
                  data-price-negative={price < 0 ? "true" : undefined}
                  className={`plan-story-price${
                    price < 0 ? " plan-story-price-negative" : ""
                  }`}
                  x={span.x + span.width * 0.23}
                  y={Math.min(priceY(price), priceY(0))}
                  width={Math.max(2, span.width * 0.54)}
                  height={Math.abs(priceY(price) - priceY(0))}
                />
              ) : null;
            })}
            <text x={W - PAD.r} y={18} textAnchor="end">
              {HOME_PLAN_STORY.priceRange(
                model.minPrice.toFixed(2),
                model.maxPrice.toFixed(2),
              )}
            </text>
          </g>

          <g className="plan-story-solar" data-testid="plan-story-solar">
            {model.solar.map((run, index) => {
              const points = run.map((point) =>
                `${model.scale.x(point.timeMs)},${solarY(point.solarW)}`,
              );
              if (points.length === 0) return null;
              const firstX = model.scale.x(run[0].startMs);
              const lastX = model.scale.x(run[run.length - 1].startMs + SLOT_MS);
              return (
                <polygon
                  key={index}
                  points={`${firstX},${PAD.t + PLOT_H} ${points.join(" ")} ${lastX},${PAD.t + PLOT_H}`}
                />
              );
            })}
            <text x={PAD.l} y={18}>
              {HOME_PLAN_STORY.solarRange(Math.round(model.maxSolar).toLocaleString())}
            </text>
          </g>

          {/* B-102: quiet left-gutter SoC % ticks for power users — not a second chart. */}
          <g className="plan-story-soc-scale" data-testid="plan-story-soc-scale" aria-hidden="true">
            {[100, 50, 0].map((pct) => (
              <text
                key={pct}
                data-testid={`plan-story-soc-scale-${pct}`}
                x={PAD.l - 8}
                y={socY(pct) + 3}
                textAnchor="end"
              >
                {HOME_PLAN_STORY.socScale(pct)}
              </text>
            ))}
          </g>

          <g className="plan-story-soc" data-testid="plan-story-soc">
            {model.soc.map((run, index) => {
              const points = run.points.map((point) =>
                `${model.scale.x(point.timeMs)},${socY(point.socPct)}`,
              ).join(" ");
              return run.points.length > 1 ? (
                <polyline
                  key={`${run.kind}-${index}`}
                  data-testid={`plan-story-soc-${run.kind}-${index}`}
                  className={`plan-story-soc-line plan-story-soc-${run.kind}`}
                  points={points}
                />
              ) : (
                <circle
                  key={`${run.kind}-${index}`}
                  data-testid={`plan-story-soc-dot-${run.kind}-${index}`}
                  className={`plan-story-soc-dot plan-story-soc-${run.kind}`}
                  cx={model.scale.x(run.points[0].timeMs)}
                  cy={socY(run.points[0].socPct)}
                  r={3}
                />
              );
            })}
          </g>

          <g className="plan-story-references" data-testid="plan-story-references">
            <line x1={PAD.l} x2={W - PAD.r} y1={socY(story.reserve_soc_pct)} y2={socY(story.reserve_soc_pct)} />
            <text
              data-testid="plan-story-reserve-label"
              x={W - PAD.r - 5}
              y={socY(story.reserve_soc_pct) - 5}
              textAnchor="end"
            >
              {HOME_PLAN_STORY.reserveLabel(Math.round(story.reserve_soc_pct))}
            </text>
            {story.target_soc_pct != null && (
              <>
                <line
                  className="plan-story-target"
                  x1={PAD.l}
                  x2={W - PAD.r}
                  y1={socY(story.target_soc_pct)}
                  y2={socY(story.target_soc_pct)}
                />
                <text
                  data-testid="plan-story-target-label"
                  x={W - PAD.r - 5}
                  y={socY(story.target_soc_pct) - 5}
                  textAnchor="end"
                >
                  {HOME_PLAN_STORY.targetLabel(Math.round(story.target_soc_pct))}
                </text>
              </>
            )}
          </g>

          {model.nowX != null && (
            <g className="plan-story-now" data-testid="plan-story-now">
              <line x1={model.nowX} x2={model.nowX} y1={PAD.t} y2={PAD.t + PLOT_H} />
              <text x={model.nowX + 5} y={PAD.t + 14}>{HOME_PLAN_STORY.now}</text>
            </g>
          )}

          <g
            className="plan-story-actions"
            data-testid="plan-story-actions"
          >
            {model.actions.map((window) => {
              const x = model.scale.x(window.start);
              return (
                <rect
                  key={`${window.start}-${window.action}`}
                  data-testid="plan-story-action-segment"
                  data-action={window.action}
                  data-start-ms={window.start}
                  data-end-ms={window.end}
                  aria-hidden="true"
                  x={x}
                  y={ACTION_Y}
                  width={model.scale.x(window.end) - x}
                  height={ACTION_H}
                  fill={`url(#plan-story-ribbon-${window.action})`}
                  className={`plan-story-action-ribbon action-${window.action}`}
                />
              );
            })}
          </g>

          <g className="plan-story-axis" aria-hidden="true">
            {model.ticks.map((timeMs) => (
              <text key={timeMs} x={model.scale.x(timeMs)} y={H - 4} textAnchor="middle">
                {formatClock(timeMs)}
              </text>
            ))}
          </g>

          {hoveredX != null && (
            <line
              data-testid="plan-story-crosshair"
              x1={hoveredX}
              x2={hoveredX}
              y1={PAD.t}
              y2={PAD.t + PLOT_H}
              stroke="var(--muted)"
              strokeWidth={1}
              strokeDasharray="2 3"
            />
          )}
        </svg>

        {hovered && hoveredX != null && (
          <div
            className="chart-tip"
            style={{ left: `${(hoveredX / W) * 100}%` }}
            data-testid="plan-story-tip"
          >
            <div className="chart-tip-title">
              {formatClock(hovered.startMs)} ·{" "}
              {hovered.startMs < model.nowMs
                ? HOME_PLAN_STORY.tipRecorded
                : HOME_PLAN_STORY.tipForecast}
            </div>
            {slotTipRows(hovered).map((row) => (
              <div key={row.label} className="chart-tip-row">
                <span
                  className={`legend-dot${row.className ? ` ${row.className}` : ""}`}
                  style={row.color ? { background: row.color } : undefined}
                />
                {row.label}
                <span className="chart-tip-val">{row.value}</span>
              </div>
            ))}
          </div>
        )}
      </div>

      <div className="chart-legend" data-testid="plan-story-legend">
        <span className="legend-item"><span className="legend-line plan-story-actual-key" />{HOME_PLAN_STORY.recordedBattery}</span>
        <span className="legend-item"><span className="legend-line plan-story-forecast-key" />{HOME_PLAN_STORY.forecastBattery}</span>
        {presentActions.map((action) => (
          <span className="legend-item" key={action} data-action-cue={action}>
            <span className={`legend-dot plan-story-legend-action action-${action}`} />
            {ACTION_META[action].label}
          </span>
        ))}
      </div>

      <ProvenanceCaption provenance={provenance} />
    </section>
  );
}
