// The story dashboard: one timeline told two ways. "Next 24h" proves the strategy is sensible;
// "Last 24h" proves it worked. Same layers both ways — SoC line, electricity price, battery action,
// solar — plus a plain-language headline and the numbers that matter (€, kWh, self-sufficiency).

import { Icon } from "./icons";

export type StorySlot = {
  start: string;
  soc_pct: number | null;
  grid_w: number;
  solar_w: number;
  battery_w: number;
  load_w: number;
  eur_per_kwh: number | null;
  action: string;
};
export type StoryTotals = {
  import_kwh: number;
  export_kwh: number;
  solar_kwh: number;
  charge_kwh: number;
  discharge_kwh: number;
  load_kwh: number;
  grid_cost_eur: number | null;
  self_sufficiency_pct: number | null;
  soc_start_pct: number | null;
  soc_end_pct: number | null;
  soc_min_pct: number | null;
  soc_max_pct: number | null;
};
export type EnergyStoryData = {
  window: "past" | "next";
  now: string;
  current_soc_pct: number | null;
  reserve_soc_pct: number;
  target_soc_pct: number | null;
  target_kwh: number | null;
  target_deadline: string | null;
  current_price_eur_per_kwh: number | null;
  slots: StorySlot[];
  totals: StoryTotals;
  headline: string;
};

const ACTION_LEGEND = [
  { key: "charge", label: "Charge" },
  { key: "discharge", label: "Power the house" },
  { key: "self_consume", label: "Use solar first" },
  { key: "hold", label: "Hold" },
  { key: "idle", label: "Idle" },
];
const ACTION_LABEL: Record<string, string> = Object.fromEntries(
  ACTION_LEGEND.map((a) => [a.key, a.label]),
);

const W = 1000;
const H = 190;
const PAD = { l: 30, r: 12, t: 12, b: 4 };
const SLOT_MS = 15 * 60 * 1000;

function clock(ms: number): string {
  return new Date(ms).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function Stat({
  label,
  value,
  accent,
  title,
}: {
  label: string;
  value: string;
  accent?: string;
  title?: string;
}) {
  return (
    <div className="story-stat" title={title}>
      <span className="story-stat-value" style={accent ? { color: accent } : undefined}>
        {value}
      </span>
      <span className="story-stat-label">{label}</span>
    </div>
  );
}

export function EnergyStory({
  story: rawStory,
  window,
  onWindow,
}: {
  story: EnergyStoryData | null;
  window: "past" | "next";
  onWindow: (w: "past" | "next") => void;
}) {
  // The toggle flips `window` instantly, but the matching data arrives one fetch later. Until it
  // does, the previously-loaded story is for the OTHER window — ignore it so we never render "Next"
  // with last-24h content (or vice-versa). `switching` lets us show a neutral "Loading…" rather
  // than the cold-start empty message. (Also makes out-of-order responses self-correct.)
  const switching = rawStory != null && rawStory.window !== window;
  const story = switching ? null : rawStory;
  const slots = story?.slots ?? [];
  const t = story?.totals;

  // SoC line points (skip slots with no SoC). Plotted at slot start — a single forward series,
  // so no bridging is needed.
  const pts = slots
    .filter((s) => s.soc_pct != null)
    .map((s) => ({ t: Date.parse(s.start), soc: s.soc_pct as number }))
    .filter((p) => Number.isFinite(p.t));
  const t0 = slots.length ? Date.parse(slots[0].start) : 0;
  const t1 = slots.length ? Date.parse(slots[slots.length - 1].start) + SLOT_MS : 1;
  const span = Math.max(1, t1 - t0);
  const x = (ms: number) => PAD.l + ((ms - t0) / span) * (W - PAD.l - PAD.r);
  const y = (soc: number) => PAD.t + (1 - soc / 100) * (H - PAD.t - PAD.b);
  const socLine = pts.map((p) => `${x(p.t).toFixed(1)},${y(p.soc).toFixed(1)}`).join(" ");
  // Filled area under the SoC line — reads as the "energy in the battery", not just a line.
  const baseY = (H - PAD.b).toFixed(1);
  const socArea =
    pts.length >= 2
      ? `${socLine} ${x(pts[pts.length - 1].t).toFixed(1)},${baseY} ${x(pts[0].t).toFixed(1)},${baseY}`
      : "";

  const maxPrice = Math.max(0.01, ...slots.map((s) => s.eur_per_kwh ?? 0));
  const maxSolar = Math.max(1, ...slots.map((s) => s.solar_w));
  const everyN = Math.max(1, Math.round(slots.length / 6));
  const isPast = window === "past";
  // Domain insight a dynamic-tariff user cares about most: did the battery cover the day's most
  // expensive hour instead of buying it? Show it only when it's a win (don't nag otherwise).
  const priced = slots.filter((s) => s.eur_per_kwh != null);
  const peak = priced.length
    ? priced.reduce((a, b) => ((b.eur_per_kwh as number) > (a.eur_per_kwh as number) ? b : a))
    : null;
  const peakInsight =
    peak && (peak.eur_per_kwh as number) > 0 && peak.battery_w > 50
      ? `${isPast ? "Covered" : "Covers"} the day's €${(peak.eur_per_kwh as number).toFixed(2)}/kWh ` +
        "peak from the battery — not the grid."
      : null;

  const nowMs = story ? Date.parse(story.now) : NaN;
  const dl = story?.target_deadline ? Date.parse(story.target_deadline) : NaN;
  const showDeadline = Number.isFinite(dl) && dl >= t0 && dl <= t1;

  // A spoken-word description of the chart for screen readers — the SVG line alone is invisible
  // to them, so spell out the start, low point, target and reserve floor.
  const socChartLabel = (() => {
    if (!story || !t || pts.length === 0) return "Battery level over time";
    const span24 = isPast ? "the last 24 hours" : "the next 24 hours";
    const parts = [`Battery level over ${span24}.`];
    if (t.soc_start_pct != null) parts.push(`Starts at ${Math.round(t.soc_start_pct)}%.`);
    if (t.soc_min_pct != null) parts.push(`Lowest ${Math.round(t.soc_min_pct)}%.`);
    if (story.target_soc_pct != null) {
      parts.push(`Target ${Math.round(story.target_soc_pct)}%.`);
    }
    parts.push(`Reserve floor ${Math.round(story.reserve_soc_pct)}%.`);
    return parts.join(" ");
  })();

  return (
    <section className="story" data-testid="energy-story">
      <div className="story-head">
        <div className="story-toggle" role="radiogroup" aria-label="Story timeframe">
          <button
            type="button"
            role="radio"
            aria-checked={isPast}
            className={`story-tab${isPast ? " story-tab-on" : ""}`}
            data-testid="story-past"
            onClick={() => onWindow("past")}
          >
            ← Last 24 hours
          </button>
          <button
            type="button"
            role="radio"
            aria-checked={!isPast}
            className={`story-tab${!isPast ? " story-tab-on" : ""}`}
            data-testid="story-next"
            onClick={() => onWindow("next")}
          >
            Next 24 hours →
          </button>
        </div>
        <div className="story-head-right">
          {story?.current_price_eur_per_kwh != null && (
            <span className="story-price" data-testid="story-price">
              €{story.current_price_eur_per_kwh.toFixed(2)}/kWh {isPast ? "latest" : "now"}
            </span>
          )}
          <span className="badge badge-muted" data-testid="story-tag">
            {isPast ? "what happened" : "the plan"}
          </span>
        </div>
      </div>

      <p className="story-headline" data-testid="story-headline">
        {story?.headline ?? "…"}
      </p>

      {peakInsight && (
        <p className="story-insight" data-testid="story-insight">
          <Icon name="bolt" /> {peakInsight}
        </p>
      )}

      {isPast && t && t.soc_min_pct != null && story && (() => {
        const ok = (t.soc_min_pct as number) >= story.reserve_soc_pct - 0.5;
        const msg = ok
          ? `The battery stayed above its ${story.reserve_soc_pct.toFixed(0)}% reserve all day` +
            (story.target_soc_pct != null && (t.soc_max_pct ?? 0) >= story.target_soc_pct - 1
              ? ` and reached the ${story.target_soc_pct.toFixed(0)}% night target.`
              : ".")
          : `The battery dipped to ${(t.soc_min_pct as number).toFixed(0)}% — below the ` +
            `${story.reserve_soc_pct.toFixed(0)}% reserve floor.`;
        return (
          <p
            className={`story-check ${ok ? "story-check-ok" : "story-check-warn"}`}
            data-testid="story-validation"
          >
            <Icon name={ok ? "check" : "alert"} /> {msg}
          </p>
        );
      })()}

      {isPast && slots.length > 0 && (t1 - t0) / 3_600_000 < 6 && (
        <p className="plan-reason" data-testid="story-thin">
          Only ~{Math.max(1, Math.round((t1 - t0) / 3_600_000))}h of history so far — the full 24h
          builds as the system keeps running.
        </p>
      )}

      {slots.length === 0 ? (
        <p className="plan-reason" data-testid="story-empty">
          {switching
            ? "Loading…"
            : isPast
            ? "No history yet — leave the system running and the last-24h story fills in."
            : "No plan yet — prices/forecast are still loading."}
        </p>
      ) : (
        <>
          {t && (
            <div className="story-stats" data-testid="story-stats">
              {t.grid_cost_eur != null && (
                <Stat
                  label={isPast ? "grid cost" : "projected grid cost"}
                  value={`€${t.grid_cost_eur.toFixed(2)}`}
                />
              )}
              {t.self_sufficiency_pct != null && (
                <Stat
                  label="powered by you"
                  value={`${t.self_sufficiency_pct.toFixed(0)}%`}
                  accent="var(--accent-text)"
                  title="Share of your electricity that came from your own solar and battery instead of the grid."
                />
              )}
              <Stat
                label="from the grid"
                value={`${t.import_kwh.toFixed(1)} kWh`}
                title="Energy bought from the grid over this period."
              />
              {t.export_kwh > 0.05 && (
                <Stat
                  label="back to grid"
                  value={`${t.export_kwh.toFixed(1)} kWh`}
                  title="Surplus solar sent back to the grid."
                />
              )}
              <Stat label="solar" value={`${t.solar_kwh.toFixed(1)} kWh`} accent="var(--summer-text)" />
              <Stat
                label={isPast ? "battery used" : "battery in / out"}
                value={
                  isPast
                    ? `${t.discharge_kwh.toFixed(1)} kWh`
                    : `${t.charge_kwh.toFixed(1)}/${t.discharge_kwh.toFixed(1)} kWh`
                }
                title={
                  isPast
                    ? "Energy the battery delivered to the home."
                    : "Energy charged into the battery / delivered from it over the plan."
                }
              />
            </div>
          )}

          <div className="track-label">
            Battery level {isPast ? "(measured)" : "(forecast)"}
          </div>
          <svg
            className="soc-svg"
            viewBox={`0 0 ${W} ${H}`}
            preserveAspectRatio="xMidYMid meet"
            role="img"
            aria-label={socChartLabel}
            data-testid="story-soc"
          >
            {[0, 50, 100].map((g) => (
              <g key={g}>
                <line className="soc-grid" x1={PAD.l} y1={y(g)} x2={W - PAD.r} y2={y(g)} />
                <text className="soc-axis" x={2} y={y(g) + 3}>
                  {g}
                </text>
              </g>
            ))}
            <line
              className="soc-reserve"
              x1={PAD.l}
              y1={y(story?.reserve_soc_pct ?? 10)}
              x2={W - PAD.r}
              y2={y(story?.reserve_soc_pct ?? 10)}
              data-testid="story-reserve"
            />
            {story?.target_soc_pct != null && (
              <>
                <line
                  className="soc-target"
                  x1={PAD.l}
                  y1={y(story.target_soc_pct)}
                  x2={W - PAD.r}
                  y2={y(story.target_soc_pct)}
                  data-testid="story-target"
                />
                <text
                  className="soc-axis soc-target-label"
                  x={W - PAD.r}
                  y={y(story.target_soc_pct) - 5}
                  textAnchor="end"
                >
                  {Math.round(story.target_soc_pct)}% night target
                </text>
              </>
            )}
            {showDeadline && (
              <line className="soc-deadline" x1={x(dl)} y1={PAD.t} x2={x(dl)} y2={H - PAD.b} />
            )}
            {Number.isFinite(nowMs) && nowMs >= t0 && nowMs <= t1 && (
              <line className="soc-now" x1={x(nowMs)} y1={PAD.t} x2={x(nowMs)} y2={H - PAD.b} />
            )}
            {pts.length >= 2 && (
              <polygon className={isPast ? "soc-area-past" : "soc-area-next"} points={socArea} />
            )}
            {pts.length >= 2 && (
              <polyline
                className={isPast ? "soc-actual" : "soc-predicted"}
                points={socLine}
                data-testid="story-soc-line"
              />
            )}
          </svg>

          <div className="legend soc-mini-legend">
            <span className="legend-item">
              <span className={`legend-line ${isPast ? "legend-actual" : "legend-predicted"}`} />
              {isPast ? "Battery level (measured)" : "Battery level (forecast)"}
            </span>
            {story?.target_soc_pct != null && (
              <span className="legend-item">
                <span className="legend-line legend-target" /> Night target
              </span>
            )}
            <span className="legend-item">
              <span className="legend-line legend-reserve" /> Minimum reserve
            </span>
            {showDeadline && (
              <span className="legend-item">
                <span className="legend-line legend-deadline" /> Sunset
              </span>
            )}
          </div>

          <div className="track-label">Electricity price (cheap = short)</div>
          <div className="track track-price" role="img" aria-label="Electricity price">
            {slots.map((s, i) => (
              <span
                key={i}
                className="tbar tbar-price"
                style={{ height: `${((s.eur_per_kwh ?? 0) / maxPrice) * 100}%` }}
                title={`${clock(Date.parse(s.start))} · €${(s.eur_per_kwh ?? 0).toFixed(2)}/kWh`}
              />
            ))}
          </div>

          <div className="track-label">Battery {isPast ? "(what it did)" : "(planned)"}</div>
          <div className="track track-plan" role="img" aria-label="Battery action">
            {slots.map((s, i) => (
              <span
                key={i}
                className={`pseg seg-${s.action}`}
                title={`${clock(Date.parse(s.start))} — ${ACTION_LABEL[s.action] ?? s.action}`}
              />
            ))}
          </div>

          <div className="track-label">Solar {isPast ? "(produced)" : "(forecast)"}</div>
          <div className="track track-solar" role="img" aria-label="Solar">
            {slots.map((s, i) => (
              <span
                key={i}
                className="tbar tbar-solar"
                style={{ height: `${(s.solar_w / maxSolar) * 100}%` }}
                title={`${clock(Date.parse(s.start))} · ${Math.round(s.solar_w)} W`}
              />
            ))}
          </div>

          <div className="time-axis" aria-hidden="true">
            {slots.map((s, i) => (
              <span key={i} className="tick">
                {i % everyN === 0 ? clock(Date.parse(s.start)) : ""}
              </span>
            ))}
          </div>

          <div className="legend" data-testid="story-legend">
            {ACTION_LEGEND.filter((a) => slots.some((s) => s.action === a.key)).map((a) => (
              <span key={a.key} className="legend-item">
                <span className={`legend-swatch seg-${a.key}`} />
                {a.label}
              </span>
            ))}
          </div>
        </>
      )}
    </section>
  );
}
