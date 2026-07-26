import { useState } from "react";

import type { EnergyStoryData, SavedToday } from "./EnergyStory";
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
const ACTION_Y = PAD.t + PLOT_H + 10;
const ACTION_H = 10;

export type PlanStoryProps = {
  story: EnergyStoryData | null;
  savedToday?: SavedToday | null;
  socPct?: number | null;
  onBatteryClick?: () => void;
};

function Footer({
  savedToday,
  socPct,
  onBatteryClick,
}: Omit<PlanStoryProps, "story">) {
  if (!savedToday && socPct == null) return null;
  return (
    <div className="battery-plan-footer" data-testid="story-footer">
      {savedToday && (
        <span className="bp-foot" data-testid="saved-today" title="Measured vs. a no-battery day.">
          <span className="bp-foot-label">Saved today</span>
          <span className="bp-foot-value">
            {savedToday.status === "measured"
              ? `€${savedToday.eur.toFixed(2)} measured`
              : "€— · measuring"}
          </span>
        </span>
      )}
      {socPct != null && (onBatteryClick ? (
        <button
          type="button"
          className="bp-foot bp-foot-btn"
          data-testid="battery-tile"
          onClick={onBatteryClick}
          title="How full the home battery is — click to see each battery."
        >
          <span className="bp-foot-label">Battery</span>
          <span className="bp-foot-value">{socPct.toFixed(0)}%</span>
          <span className="bp-foot-more">see each battery →</span>
        </button>
      ) : (
        <span className="bp-foot" data-testid="battery-tile" title="How full the home battery is right now.">
          <span className="bp-foot-label">Battery</span>
          <span className="bp-foot-value">{socPct.toFixed(0)}%</span>
        </span>
      ))}
    </div>
  );
}

export function PlanStory({
  story,
  savedToday = null,
  socPct = null,
  onBatteryClick,
}: PlanStoryProps) {
  const [hover, setHover] = useState<number | null>(null);
  const model = buildPlanStoryModel(story, PAD.l, PLOT_W);

  if (!story || !model) {
    return (
      <section className="plan-story" data-testid="plan-story" data-density-kind="chart">
        <p>Battery plan is unavailable.</p>
        <Footer savedToday={savedToday} socPct={socPct} onBatteryClick={onBatteryClick} />
      </section>
    );
  }

  const socY = (value: number) =>
    PAD.t + (1 - Math.max(0, Math.min(100, value)) / 100) * PLOT_H;
  const solarY = (value: number) =>
    PAD.t + PLOT_H - (Math.max(0, value) / model.maxSolar) * PLOT_H * 0.38;
  const priceRange = Math.max(0.01, model.maxPrice - model.minPrice);
  const priceOpacity = (value: number) =>
    0.08 + ((value - model.minPrice) / priceRange) * 0.2;
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
          <g className="plan-story-prices" data-testid="plan-story-prices">
            {model.spans.map((span) => {
              const price = model.slots[span.index].eur_per_kwh;
              return typeof price === "number" && Number.isFinite(price) ? (
                <rect
                  key={span.startMs}
                  data-testid="plan-story-price"
                  x={span.x}
                  y={PAD.t}
                  width={span.width}
                  height={PLOT_H}
                  fill="var(--winter)"
                  opacity={priceOpacity(price)}
                />
              ) : null;
            })}
            <text x={W - PAD.r} y={18} textAnchor="end">
              Price €{model.minPrice.toFixed(2)}–€{model.maxPrice.toFixed(2)}
            </text>
          </g>

          <g className="plan-story-solar" data-testid="plan-story-solar">
            {model.solar.map((run, index) => {
              const points = run.map((slot) =>
                `${model.scale.x(slot.startMs + SLOT_MS / 2)},${solarY(slot.solar_w)}`,
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
            <text x={PAD.l} y={18}>Solar 0–{Math.round(model.maxSolar).toLocaleString()} W</text>
          </g>

          <g className="plan-story-soc" data-testid="plan-story-soc">
            {model.soc.map((run, index) => {
              const points = run.points.map((point) =>
                `${model.scale.x(point.timeMs)},${socY(point.socPct)}`,
              ).join(" ");
              return run.points.length > 1 ? (
                <polyline
                  key={`${run.kind}-${index}`}
                  data-testid={`plan-story-soc-${run.kind}`}
                  className={`plan-story-soc-line plan-story-soc-${run.kind}`}
                  points={points}
                />
              ) : (
                <circle
                  key={`${run.kind}-${index}`}
                  data-testid={`plan-story-soc-${run.kind}`}
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
              reserve {Math.round(story.reserve_soc_pct)}%
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
                  target {Math.round(story.target_soc_pct)}%
                </text>
              </>
            )}
          </g>

          {model.nowX != null && (
            <g className="plan-story-now" data-testid="plan-story-now">
              <line x1={model.nowX} x2={model.nowX} y1={PAD.t} y2={PAD.t + PLOT_H} />
              <text x={model.nowX + 5} y={PAD.t + 14}>now</text>
            </g>
          )}

          <g
            className="plan-story-actions"
            data-testid="plan-story-actions"
          >
            {model.spans.map((span) => {
              const action = canonicalAction(model.slots[span.index].action);
              return (
                <foreignObject
                  key={span.startMs}
                  data-testid="plan-story-action-segment"
                  data-action={action}
                  data-start-ms={span.startMs}
                  aria-hidden="true"
                  x={span.x}
                  y={ACTION_Y}
                  width={span.width}
                  height={ACTION_H}
                >
                  <div className={`plan-story-action-segment ${ACTION_META[action].className}`} />
                </foreignObject>
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
              {formatClock(hovered.startMs)} · {hovered.startMs < model.nowMs ? "recorded" : "forecast"}
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
        <span className="legend-item"><span className="legend-line plan-story-actual-key" />Recorded battery</span>
        <span className="legend-item"><span className="legend-line plan-story-forecast-key" />Forecast battery</span>
        {presentActions.map((action) => (
          <span className="legend-item" key={action}>
            <span className={`legend-dot ${ACTION_META[action].className}`} />
            {ACTION_META[action].label}
          </span>
        ))}
      </div>

      <Footer savedToday={savedToday} socPct={socPct} onBatteryClick={onBatteryClick} />
    </section>
  );
}
