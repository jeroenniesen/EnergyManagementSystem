// How the energy behaved over the window (spec 2026-07-03 A), in TWO aligned panels:
//   "Used by the home"  — house + car STACKED, so "which device used the most" reads directly;
//   "Solar & grid"      — solar production, grid import above zero, export below.
// One mixed graph was a design flaw: the grid trace equals the sum of the consumers (grid =
// house + car + battery − solar), so a charging car and the grid drew exactly on top of each
// other. Separate directions, separate panels, shared x-axis + crosshair; one y-scale per panel
// (never a dual axis). Colors follow the app's entity tokens; legend + tooltip + figures table
// keep identity off color alone.
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
const PAD = { l: 46, r: 10, t: 8, b: 20 };
const PLOT_W = W - PAD.l - PAD.r;

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
  if (period === "day")
    return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
  if (period === "year") return d.toLocaleDateString([], { month: "short" });
  return d.toLocaleDateString([], {
    weekday: period === "week" ? "short" : undefined,
    day: "numeric",
    month: period === "week" ? undefined : "short",
  });
}

export function EnergyBehavior({ buckets, period, partial }: {
  buckets: SeriesBucket[];
  period: Period;
  partial: boolean;
}) {
  const [hover, setHover] = useState<number | null>(null);
  const wrapRef = useRef<HTMLDivElement | null>(null);
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
  const scale = isDay ? 4000 : 1; // day panels plot watts (kWh per 15 min → W); longer plot kWh
  const fmtV = isDay ? fmtW : fmtKwh;
  const n = buckets.length;
  const x = (i: number) => PAD.l + (i / n) * PLOT_W;
  const bw = PLOT_W / n;
  const cx = (i: number) => x(i) + bw / 2;
  const isolated = (i: number) =>
    buckets[i].samples > 0 &&
    (i === 0 || buckets[i - 1].samples === 0) &&
    (i === n - 1 || buckets[i + 1].samples === 0);

  const onMove = (e: React.MouseEvent<SVGSVGElement>) => {
    const rect = (e.currentTarget as SVGSVGElement).getBoundingClientRect();
    const px = ((e.clientX - rect.left) / rect.width) * W;
    const i = Math.floor(((px - PAD.l) / PLOT_W) * n);
    setHover(i >= 0 && i < n && buckets[i].samples > 0 ? i : null);
  };

  // A path along `top(i)` for consecutive sampled buckets; gaps break the path.
  const linePath = (top: (b: SeriesBucket) => number, y: (v: number) => number) => {
    let d = "";
    buckets.forEach((b, i) => {
      if (b.samples === 0) return;
      const cmd = d === "" || buckets[i - 1]?.samples === 0 ? "M" : "L";
      d += `${cmd}${cx(i).toFixed(1)},${y(top(b)).toFixed(1)}`;
    });
    return d;
  };
  // A filled band between lo(i) and hi(i) per contiguous sampled run.
  const bandPath = (lo: (b: SeriesBucket) => number, hi: (b: SeriesBucket) => number,
                    y: (v: number) => number) => {
    const runs: number[][] = [];
    buckets.forEach((b, i) => {
      if (b.samples === 0) return;
      const run = runs.length && buckets[i - 1]?.samples > 0 ? runs[runs.length - 1] : null;
      if (run) run.push(i);
      else runs.push([i]);
    });
    return runs.filter((r) => r.length > 1).map((r) => {
      const fwd = r.map((i) => `${cx(i).toFixed(1)},${y(hi(buckets[i])).toFixed(1)}`).join("L");
      const back = [...r].reverse()
        .map((i) => `${cx(i).toFixed(1)},${y(lo(buckets[i])).toFixed(1)}`).join("L");
      return `M${fwd}L${back}Z`;
    }).join("");
  };

  // ---- Panel A: consumption (house + car stacked) ----
  const H_A = 168;
  const plotA = H_A - PAD.t - PAD.b;
  const maxA = niceMax(Math.max(...buckets.map((b) => (b.house_kwh + b.car_kwh) * scale)));
  const yA = (v: number) => PAD.t + ((maxA - v) / maxA) * plotA;
  const houseTop = (b: SeriesBucket) => b.house_kwh * scale;
  const stackTop = (b: SeriesBucket) => (b.house_kwh + b.car_kwh) * scale;

  // ---- Panel B: solar & grid (import up, export down) ----
  const H_B = 168;
  const maxUp = niceMax(Math.max(...buckets.map((b) =>
    Math.max(b.solar_kwh, b.grid_import_kwh) * scale)));
  const maxDown = niceMax(Math.max(0.0001, ...buckets.map((b) => b.grid_export_kwh * scale)));
  const hasExp = totals.exp > 0.01;
  const plotB = H_B - PAD.t - PAD.b;
  const spanB = maxUp + (hasExp ? maxDown : 0);
  const yB = (v: number) => PAD.t + ((maxUp - v) / spanB) * plotB;
  const y0B = yB(0);

  const xLabels = (h: number) => buckets.map((b, i) =>
    (isDay ? i % 24 === 0 : n <= 12 || i % 7 === 0) && (
      <text key={`x${b.start}`} x={cx(i)} y={h - 6} textAnchor="middle" className="behavior-tick">
        {bucketLabel(b.start, period)}
      </text>
    ));
  const crosshair = (h: number) => hover != null && (
    <line x1={cx(hover)} x2={cx(hover)} y1={PAD.t} y2={h - PAD.b}
      stroke="var(--muted)" strokeWidth={1} strokeDasharray="2 3" />
  );

  const tipRows = hover != null && buckets[hover] ? [
    { label: "House", color: "var(--house)", v: buckets[hover].house_kwh },
    { label: "Car", color: "var(--car)", v: buckets[hover].car_kwh },
    { label: "Solar", color: "var(--summer)", v: buckets[hover].solar_kwh },
    { label: "Grid in", color: "var(--winter)", v: buckets[hover].grid_import_kwh },
    ...(buckets[hover].grid_export_kwh > 0.001
      ? [{ label: "Grid out", color: "var(--winter)", v: buckets[hover].grid_export_kwh }] : []),
  ] : [];

  return (
    <div className="behavior" data-testid="energy-behavior">
      <h3 className="card-title flow-title">How your energy behaved{partial ? " (so far)" : ""}</h3>
      <p className="sr-only">{summary}</p>
      <div className="behavior-wrap" ref={wrapRef}>
        <div data-testid="behavior-consumption">
          <h4 className="behavior-panel-title">Used by the home</h4>
          <svg viewBox={`0 0 ${W} ${H_A}`} className="behavior-svg" role="img"
            aria-label={`Used by the home: house ${fmtKwh(totals.house)}, car ${fmtKwh(totals.car)}.`}
            onMouseMove={onMove} onMouseLeave={() => setHover(null)}>
            {[maxA, maxA / 2, 0].map((t) => (
              <g key={t}>
                <line x1={PAD.l} x2={W - PAD.r} y1={yA(t)} y2={yA(t)} stroke="var(--line)"
                  strokeWidth={t === 0 ? 1.4 : 1} strokeDasharray={t === 0 ? undefined : "3 4"} />
                <text x={PAD.l - 6} y={yA(t) + 3} textAnchor="end" className="behavior-tick">
                  {fmtV(t)}
                </text>
              </g>
            ))}
            {isDay ? (
              <>
                <path d={bandPath(() => 0, houseTop, yA)} fill="var(--house)" opacity={0.3} />
                <path d={bandPath(houseTop, stackTop, yA)} fill="var(--car)" opacity={0.35} />
                <path d={linePath(houseTop, yA)} fill="none" stroke="var(--house)" strokeWidth={2}
                  strokeLinejoin="round" strokeLinecap="round" />
                <path d={linePath(stackTop, yA)} fill="none" stroke="var(--car)" strokeWidth={2}
                  strokeLinejoin="round" strokeLinecap="round" />
                {buckets.map((b, i) => isolated(i) && (
                  <g key={`dot${b.start}`}>
                    <circle cx={cx(i)} cy={yA(houseTop(b))} r={3} fill="var(--house)" />
                    {b.car_kwh > 0.001 &&
                      <circle cx={cx(i)} cy={yA(stackTop(b))} r={3} fill="var(--car)" />}
                  </g>
                ))}
              </>
            ) : (
              buckets.map((b, i) => {
                if (b.samples === 0) return null;
                const colW = Math.min(20, bw - 4);
                const bx = cx(i) - colW / 2;
                const hHouse = (houseTop(b) / maxA) * plotA;
                const hCar = ((b.car_kwh * scale) / maxA) * plotA;
                return (
                  <g key={b.start}>
                    {hHouse > 0.5 && <rect x={bx} y={yA(0) - hHouse} width={colW} height={hHouse}
                      rx={2} fill="var(--house)" />}
                    {hCar > 0.5 && <rect x={bx} y={yA(0) - hHouse - 2 - hCar} width={colW}
                      height={hCar} rx={2} fill="var(--car)" />}
                  </g>
                );
              })
            )}
            {crosshair(H_A)}
            {xLabels(H_A)}
          </svg>
          <div className="chart-legend" aria-hidden="true">
            <span className="legend-item"><span className="legend-dot"
              style={{ background: "var(--house)" }} />House</span>
            <span className="legend-item"><span className="legend-dot"
              style={{ background: "var(--car)" }} />Car (charging)</span>
          </div>
        </div>

        <div data-testid="behavior-grid">
          <h4 className="behavior-panel-title">Solar &amp; grid</h4>
          <svg viewBox={`0 0 ${W} ${H_B}`} className="behavior-svg" role="img"
            aria-label={`Solar and grid: solar ${fmtKwh(totals.solar)}, imported ${fmtKwh(totals.imp)}, exported ${fmtKwh(totals.exp)}.`}
            onMouseMove={onMove} onMouseLeave={() => setHover(null)}>
            {[maxUp, 0, ...(hasExp ? [-maxDown] : [])].map((t) => (
              <g key={t}>
                <line x1={PAD.l} x2={W - PAD.r} y1={yB(t)} y2={yB(t)} stroke="var(--line)"
                  strokeWidth={t === 0 ? 1.4 : 1} strokeDasharray={t === 0 ? undefined : "3 4"} />
                <text x={PAD.l - 6} y={yB(t) + 3} textAnchor="end" className="behavior-tick">
                  {fmtV(t)}
                </text>
              </g>
            ))}
            {isDay ? (
              <>
                <path d={bandPath(() => 0, (b) => b.grid_import_kwh * scale, yB)}
                  fill="var(--winter)" opacity={0.3} />
                <path d={linePath((b) => b.grid_import_kwh * scale, yB)} fill="none"
                  stroke="var(--winter)" strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
                {hasExp && (
                  <path d={bandPath((b) => -b.grid_export_kwh * scale, () => 0, yB)}
                    fill="var(--winter)" opacity={0.18} />
                )}
                {hasExp && (
                  <path d={linePath((b) => -b.grid_export_kwh * scale, yB)} fill="none"
                    stroke="var(--winter)" strokeWidth={1.5} strokeDasharray="4 3" />
                )}
                <path d={linePath((b) => b.solar_kwh * scale, yB)} fill="none"
                  stroke="var(--summer)" strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
                {buckets.map((b, i) => isolated(i) && (
                  <g key={`dot${b.start}`}>
                    <circle cx={cx(i)} cy={yB(b.grid_import_kwh * scale)} r={3} fill="var(--winter)" />
                    <circle cx={cx(i)} cy={yB(b.solar_kwh * scale)} r={3} fill="var(--summer)" />
                  </g>
                ))}
              </>
            ) : (
              buckets.map((b, i) => {
                if (b.samples === 0) return null;
                const colW = Math.min(16, (bw - 6) / 2);
                const sx = cx(i) - colW - 1;
                const gx = cx(i) + 1;
                const hSolar = ((b.solar_kwh * scale) / spanB) * plotB;
                const hImp = ((b.grid_import_kwh * scale) / spanB) * plotB;
                const hExp = ((b.grid_export_kwh * scale) / spanB) * plotB;
                return (
                  <g key={b.start}>
                    {hSolar > 0.5 && <rect x={sx} y={y0B - hSolar} width={colW} height={hSolar}
                      rx={2} fill="var(--summer)" />}
                    {hImp > 0.5 && <rect x={gx} y={y0B - hImp} width={colW} height={hImp}
                      rx={2} fill="var(--winter)" />}
                    {hExp > 0.5 && <rect x={gx} y={y0B + 2} width={colW}
                      height={Math.max(1, hExp - 2)} rx={2} fill="var(--winter)" opacity={0.5} />}
                  </g>
                );
              })
            )}
            {crosshair(H_B)}
            {xLabels(H_B)}
          </svg>
          <div className="chart-legend" aria-hidden="true">
            <span className="legend-item"><span className="legend-dot"
              style={{ background: "var(--summer)" }} />Solar</span>
            <span className="legend-item"><span className="legend-dot"
              style={{ background: "var(--winter)" }} />Grid in</span>
            <span className="legend-item"><span className="legend-dot legend-dot-export" />
              Grid out (below zero)</span>
          </div>
        </div>

        {hover != null && buckets[hover] && (
          <div className="chart-tip" style={{ left: `${(cx(hover) / W) * 100}%` }}
            data-testid="behavior-tip">
            <div className="chart-tip-title">{bucketLabel(buckets[hover].start, period)}</div>
            {tipRows.map((r) => (
              <div key={r.label} className="chart-tip-row">
                <span className="legend-dot" style={{ background: r.color }} />
                {r.label}
                <span className="chart-tip-val">{fmtV(r.v * scale)}</span>
              </div>
            ))}
          </div>
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
