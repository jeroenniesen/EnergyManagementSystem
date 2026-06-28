// The story dashboard: one timeline told two ways. "Next 24h" proves the strategy is sensible;
// "Last 24h" proves it worked. Same layers both ways — SoC line, electricity price, battery action,
// solar — plus a plain-language headline and the numbers that matter (€, kWh, self-sufficiency).

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
};
export type EnergyStoryData = {
  window: "past" | "next";
  now: string;
  current_soc_pct: number | null;
  reserve_soc_pct: number;
  target_soc_pct: number | null;
  target_kwh: number | null;
  target_deadline: string | null;
  slots: StorySlot[];
  totals: StoryTotals;
  headline: string;
};

const ACTION_LEGEND = [
  { key: "charge", label: "Charge" },
  { key: "discharge", label: "Discharge" },
  { key: "self_consume", label: "Self-consume" },
  { key: "hold", label: "Hold" },
  { key: "idle", label: "Idle" },
];

const W = 1000;
const H = 190;
const PAD = { l: 30, r: 12, t: 12, b: 4 };
const SLOT_MS = 15 * 60 * 1000;

function clock(ms: number): string {
  return new Date(ms).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function Stat({ label, value, accent }: { label: string; value: string; accent?: string }) {
  return (
    <div className="story-stat">
      <span className="story-stat-value" style={accent ? { color: accent } : undefined}>
        {value}
      </span>
      <span className="story-stat-label">{label}</span>
    </div>
  );
}

export function EnergyStory({
  story,
  window,
  onWindow,
}: {
  story: EnergyStoryData | null;
  window: "past" | "next";
  onWindow: (w: "past" | "next") => void;
}) {
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

  const maxPrice = Math.max(0.01, ...slots.map((s) => s.eur_per_kwh ?? 0));
  const maxSolar = Math.max(1, ...slots.map((s) => s.solar_w));
  const everyN = Math.max(1, Math.round(slots.length / 6));
  const nowMs = story ? Date.parse(story.now) : NaN;
  const dl = story?.target_deadline ? Date.parse(story.target_deadline) : NaN;
  const showDeadline = Number.isFinite(dl) && dl >= t0 && dl <= t1;
  const isPast = window === "past";

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
        <span className="badge badge-muted" data-testid="story-tag">
          {isPast ? "what happened" : "the plan"}
        </span>
      </div>

      <p className="story-headline" data-testid="story-headline">
        {story?.headline ?? "…"}
      </p>

      {slots.length === 0 ? (
        <p className="plan-reason" data-testid="story-empty">
          {isPast
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
                  label="self-sufficient"
                  value={`${t.self_sufficiency_pct.toFixed(0)}%`}
                  accent="var(--accent)"
                />
              )}
              <Stat label="grid import" value={`${t.import_kwh.toFixed(1)} kWh`} />
              {t.export_kwh > 0.05 && <Stat label="exported" value={`${t.export_kwh.toFixed(1)} kWh`} />}
              <Stat label="solar" value={`${t.solar_kwh.toFixed(1)} kWh`} accent="var(--summer)" />
              <Stat
                label={isPast ? "battery used" : "battery in/out"}
                value={
                  isPast
                    ? `${t.discharge_kwh.toFixed(1)} kWh`
                    : `${t.charge_kwh.toFixed(1)}/${t.discharge_kwh.toFixed(1)} kWh`
                }
              />
            </div>
          )}

          <div className="track-label">
            State of charge {isPast ? "(recorded)" : "(projected)"}
          </div>
          <svg
            className="soc-svg"
            viewBox={`0 0 ${W} ${H}`}
            preserveAspectRatio="xMidYMid meet"
            role="img"
            aria-label={story?.headline ?? "State of charge"}
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
              <line
                className="soc-target"
                x1={PAD.l}
                y1={y(story.target_soc_pct)}
                x2={W - PAD.r}
                y2={y(story.target_soc_pct)}
                data-testid="story-target"
              />
            )}
            {showDeadline && (
              <line className="soc-deadline" x1={x(dl)} y1={PAD.t} x2={x(dl)} y2={H - PAD.b} />
            )}
            {Number.isFinite(nowMs) && nowMs >= t0 && nowMs <= t1 && (
              <line className="soc-now" x1={x(nowMs)} y1={PAD.t} x2={x(nowMs)} y2={H - PAD.b} />
            )}
            {pts.length >= 2 && (
              <polyline
                className={isPast ? "soc-actual" : "soc-predicted"}
                points={socLine}
                data-testid="story-soc-line"
              />
            )}
          </svg>

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
                title={`${clock(Date.parse(s.start))} — ${s.action}`}
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
