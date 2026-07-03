// How the energy behaved over the window (spec 2026-07-03 A): grid (P1, import above / export
// below zero), house (without the car), car, and solar. Day = stepped power curves per 15-min
// slot; week/month/year = per-bucket kWh bars. Hand-rolled SVG like the rest of the app; colors
// follow the app's entity tokens (grid=--winter, solar=--summer, new --house/--car; CVD-checked).
// Identity is never color-alone: legend + tooltip + a figures table accompany the marks.
import { useMemo, useRef, useState } from "react";

export type SeriesBucket = {
  start: string;
  grid_import_kwh: number;
  grid_export_kwh: number;
  house_kwh: number;
  car_kwh: number;
  solar_kwh: number;
  samples: number;
};

type Period = "day" | "week" | "month" | "year";

const W = 720;
const H = 230;
const PAD = { l: 46, r: 10, t: 10, b: 22 };
const PLOT_W = W - PAD.l - PAD.r;
const PLOT_H = H - PAD.t - PAD.b;

const SERIES = [
  { key: "house", label: "House", color: "var(--house)" },
  { key: "car", label: "Car", color: "var(--car)" },
  { key: "grid", label: "Grid", color: "var(--winter)" },
  { key: "solar", label: "Solar", color: "var(--summer)" },
] as const;

function niceMax(v: number): number {
  if (v <= 0) return 1;
  const mag = 10 ** Math.floor(Math.log10(v));
  for (const m of [1, 2, 2.5, 5, 10]) if (v <= m * mag) return m * mag;
  return 10 * mag;
}

const fmtW = (w: number) => (Math.abs(w) >= 1000 ? `${(w / 1000).toFixed(1)} kW` : `${Math.round(w)} W`);
const fmtKwh = (k: number) => `${k.toFixed(k >= 10 ? 0 : 1)} kWh`;

function bucketLabel(iso: string, period: Period): string {
  const d = new Date(iso);
  if (period === "day") {
    return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  }
  if (period === "year") return d.toLocaleDateString([], { month: "short" });
  return d.toLocaleDateString([], { weekday: period === "week" ? "short" : undefined, day: "numeric", month: period === "week" ? undefined : "short" });
}

export function EnergyBehavior({ buckets, period, partial }: {
  buckets: SeriesBucket[];
  period: Period;
  partial: boolean;
}) {
  const [hover, setHover] = useState<number | null>(null);
  const svgRef = useRef<SVGSVGElement | null>(null);
  const sampled = buckets.filter((b) => b.samples > 0);

  const totals = useMemo(() => ({
    house: sampled.reduce((s, b) => s + b.house_kwh, 0),
    car: sampled.reduce((s, b) => s + b.car_kwh, 0),
    imp: sampled.reduce((s, b) => s + b.grid_import_kwh, 0),
    exp: sampled.reduce((s, b) => s + b.grid_export_kwh, 0),
    solar: sampled.reduce((s, b) => s + b.solar_kwh, 0),
  }), [buckets]);

  if (sampled.length === 0) return null;

  const summary =
    `Energy behavior: house used ${fmtKwh(totals.house)}, car ${fmtKwh(totals.car)}, ` +
    `imported ${fmtKwh(totals.imp)}, exported ${fmtKwh(totals.exp)}, solar ${fmtKwh(totals.solar)}` +
    `${partial ? " so far" : ""}.`;

  const isDay = period === "day";
  // Per-bucket values: day = watts (signed grid); longer = kWh (export negative).
  const val = (b: SeriesBucket, key: string): number => {
    const scale = isDay ? 4000 : 1; // kWh per 15 min → W
    if (key === "house") return b.house_kwh * scale;
    if (key === "car") return b.car_kwh * scale;
    if (key === "solar") return b.solar_kwh * scale;
    return (b.grid_import_kwh - b.grid_export_kwh) * scale; // grid, signed
  };
  const maxPos = niceMax(Math.max(...buckets.map((b) =>
    Math.max(val(b, "house"), val(b, "car"), val(b, "solar"), val(b, "grid")))));
  const maxNeg = niceMax(Math.max(0, ...buckets.map((b) => -val(b, "grid"))));
  const hasNeg = maxNeg > (isDay ? 20 : 0.01);
  const ySpan = maxPos + (hasNeg ? maxNeg : 0);
  const y = (v: number) => PAD.t + ((maxPos - v) / ySpan) * PLOT_H;
  const x = (i: number) => PAD.l + (i / buckets.length) * PLOT_W;
  const bw = PLOT_W / buckets.length;
  const y0 = y(0);
  const fmtV = isDay ? fmtW : fmtKwh;

  const onMove = (e: React.MouseEvent<SVGSVGElement>) => {
    const rect = svgRef.current?.getBoundingClientRect();
    if (!rect) return;
    const px = ((e.clientX - rect.left) / rect.width) * W;
    const i = Math.floor(((px - PAD.l) / PLOT_W) * buckets.length);
    setHover(i >= 0 && i < buckets.length && buckets[i].samples > 0 ? i : null);
  };

  const ticks = [maxPos, maxPos / 2, 0, ...(hasNeg ? [-maxNeg] : [])];

  return (
    <div className="behavior" data-testid="energy-behavior">
      <h3 className="card-title flow-title">How your energy behaved{partial ? " (so far)" : ""}</h3>
      <p className="sr-only">{summary}</p>
      <div className="behavior-wrap">
        <svg
          ref={svgRef}
          viewBox={`0 0 ${W} ${H}`}
          className="behavior-svg"
          role="img"
          aria-label={summary}
          onMouseMove={onMove}
          onMouseLeave={() => setHover(null)}
        >
          {ticks.map((t) => (
            <g key={t}>
              <line x1={PAD.l} x2={W - PAD.r} y1={y(t)} y2={y(t)}
                stroke="var(--line)" strokeWidth={t === 0 ? 1.4 : 1} strokeDasharray={t === 0 ? undefined : "3 4"} />
              <text x={PAD.l - 6} y={y(t) + 3} textAnchor="end" className="behavior-tick">
                {fmtV(t)}
              </text>
            </g>
          ))}
          {isDay
            ? SERIES.map((s) => {
                let d = "";
                const dots: Array<{ cx: number; cy: number }> = [];
                buckets.forEach((b, i) => {
                  if (b.samples === 0) return;
                  const cmd = d === "" || buckets[i - 1]?.samples === 0 ? "M" : "L";
                  d += `${cmd}${(x(i) + bw / 2).toFixed(1)},${y(val(b, s.key)).toFixed(1)}`;
                  // An isolated slot (no sampled neighbour) has no line segment — a path with a
                  // lone M draws nothing, which made a freshly-started day look empty. Dot it.
                  if (buckets[i - 1]?.samples === 0 || i === 0) {
                    if (!buckets[i + 1] || buckets[i + 1].samples === 0) {
                      dots.push({ cx: x(i) + bw / 2, cy: y(val(b, s.key)) });
                    }
                  }
                });
                return (
                  <g key={s.key}>
                    <path d={d} fill="none" stroke={s.color} strokeWidth={2}
                      strokeLinejoin="round" strokeLinecap="round" />
                    {dots.map((p) => (
                      <circle key={p.cx} cx={p.cx} cy={p.cy} r={3} fill={s.color} />
                    ))}
                  </g>
                );
              })
            : buckets.map((b, i) => {
                if (b.samples === 0) return null;
                const cols = ["house", "car", "grid"] as const;
                const colW = Math.min(16, (bw - 6) / 3);
                return (
                  <g key={b.start}>
                    {cols.map((k, ci) => {
                      const cx = x(i) + bw / 2 + (ci - 1) * (colW + 2) - colW / 2;
                      if (k === "grid") {
                        const impH = (b.grid_import_kwh / ySpan) * PLOT_H;
                        const expH = (b.grid_export_kwh / ySpan) * PLOT_H;
                        return (
                          <g key={k}>
                            {impH > 0.5 && <rect x={cx} y={y0 - impH} width={colW} height={impH}
                              rx={2} fill="var(--winter)" />}
                            {expH > 0.5 && <rect x={cx} y={y0 + 2} width={colW} height={Math.max(1, expH - 2)}
                              rx={2} fill="var(--winter)" opacity={0.55} />}
                          </g>
                        );
                      }
                      const h = (val(b, k) / ySpan) * PLOT_H;
                      const color = k === "house" ? "var(--house)" : "var(--car)";
                      return h > 0.5
                        ? <rect key={k} x={cx} y={y0 - h} width={colW} height={h} rx={2} fill={color} />
                        : null;
                    })}
                  </g>
                );
              })}
          {buckets.map((b, i) =>
            (isDay ? i % 24 === 0 : buckets.length <= 12 || i % 7 === 0) && (
              <text key={`x${b.start}`} x={x(i) + bw / 2} y={H - 6} textAnchor="middle"
                className="behavior-tick">
                {bucketLabel(b.start, period)}
              </text>
            ))}
          {hover != null && (
            <line x1={x(hover) + bw / 2} x2={x(hover) + bw / 2} y1={PAD.t} y2={H - PAD.b}
              stroke="var(--muted)" strokeWidth={1} strokeDasharray="2 3" />
          )}
        </svg>
        {hover != null && buckets[hover] && (
          <div
            className="chart-tip"
            style={{ left: `${((x(hover) + bw / 2) / W) * 100}%` }}
            data-testid="behavior-tip"
          >
            <div className="chart-tip-title">{bucketLabel(buckets[hover].start, period)}</div>
            {SERIES.map((s) => (
              <div key={s.key} className="chart-tip-row">
                <span className="legend-dot" style={{ background: s.color }} />
                {s.label}
                <span className="chart-tip-val">
                  {s.key === "grid" && buckets[hover].grid_export_kwh > buckets[hover].grid_import_kwh
                    ? `−${fmtV(isDay ? buckets[hover].grid_export_kwh * 4000 : buckets[hover].grid_export_kwh)} out`
                    : fmtV(val(buckets[hover], s.key))}
                </span>
              </div>
            ))}
          </div>
        )}
      </div>
      <div className="chart-legend" aria-hidden="true">
        {SERIES.map((s) => (
          <span key={s.key} className="legend-item">
            <span className="legend-dot" style={{ background: s.color }} />
            {s.label}
          </span>
        ))}
        {hasNeg && (
          <span className="legend-item">
            <span className="legend-dot legend-dot-export" />
            Export (below zero)
          </span>
        )}
      </div>
      <details className="chart-table">
        <summary>Figures</summary>
        <div className="chart-table-scroll">
          <table>
            <thead>
              <tr><th>{isDay ? "Time" : "Date"}</th><th>House</th><th>Car</th><th>Grid in</th>
                <th>Grid out</th><th>Solar</th></tr>
            </thead>
            <tbody>
              {sampled.map((b) => (
                <tr key={b.start}>
                  <td>{bucketLabel(b.start, period)}</td>
                  <td>{b.house_kwh.toFixed(2)}</td>
                  <td>{b.car_kwh.toFixed(2)}</td>
                  <td>{b.grid_import_kwh.toFixed(2)}</td>
                  <td>{b.grid_export_kwh.toFixed(2)}</td>
                  <td>{b.solar_kwh.toFixed(2)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </div>
  );
}
