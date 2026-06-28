// Average battery SoC over time: what actually happened (recorded) + what the plan will do next
// (predicted), on one timeline, plus the per-tower breakdown of the cluster. Inline SVG — no CDN.

export type Tower = {
  ip: string;
  role: string | null;
  soc_pct: number | null;
  power_w: number;
  capacity_kwh: number | null;
  online: boolean;
};
export type BatteryAggregate = {
  soc_pct: number;
  power_w: number;
  capacity_kwh: number | null;
  online_towers: number;
  total_towers: number;
} | null;
export type Battery = {
  current_mode: string | null;
  capabilities: { services: string[]; p1_paired: boolean } | null;
  towers: Tower[];
  aggregate: BatteryAggregate;
};

export type EnergyForecast = {
  now: string;
  current_soc_pct: number | null;
  reserve_soc_pct: number;
  history: { ts: string; soc_pct: number }[];
  projection: { start: string; intent: string; soc_pct: number }[];
  summary: string;
  soc_end_pct: number | null;
  soc_min_pct: number | null;
  soc_max_pct: number | null;
  import_kwh: number;
  export_kwh: number;
  solar_kwh: number;
  load_kwh: number;
};

type Pt = { t: number; soc: number };

const W = 1000;
const H = 240;
const PAD = { l: 30, r: 12, t: 12, b: 22 };

function clock(ms: number): string {
  return new Date(ms).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function poly(pts: Pt[], x: (t: number) => number, y: (s: number) => number): string {
  return pts.map((p) => `${x(p.t).toFixed(1)},${y(p.soc).toFixed(1)}`).join(" ");
}

function lastOctet(ip: string): string {
  const parts = ip.split(".");
  return parts.length === 4 ? `…${parts[3]}` : ip;
}

function TowerChips({ battery }: { battery: Battery }) {
  const agg = battery.aggregate;
  return (
    <div className="tower-chips" data-testid="tower-chips">
      {agg && (
        <span className="tower-chip tower-chip-agg" data-testid="tower-chip-aggregate">
          <span className="tower-chip-soc">{agg.soc_pct.toFixed(0)}%</span>
          <span className="tower-chip-meta">
            cluster avg · {agg.online_towers}/{agg.total_towers} online
            {agg.capacity_kwh != null ? ` · ${agg.capacity_kwh.toFixed(1)} kWh` : ""}
          </span>
        </span>
      )}
      {battery.towers.map((t) => (
        <span
          key={t.ip}
          className={`tower-chip ${t.online ? "" : "tower-chip-off"}`}
          data-testid="tower-chip"
          title={`${t.ip}${t.role ? ` (${t.role})` : ""}`}
        >
          <span className="tower-chip-soc">
            {t.online && t.soc_pct != null ? `${t.soc_pct.toFixed(0)}%` : "—"}
          </span>
          <span className="tower-chip-meta">
            {lastOctet(t.ip)} {t.role ?? ""}
            {t.capacity_kwh != null ? ` · ${t.capacity_kwh.toFixed(2)} kWh` : ""}
            {t.online ? "" : " · offline"}
          </span>
        </span>
      ))}
    </div>
  );
}

export function SocForecast({
  forecast,
  battery,
}: {
  forecast: EnergyForecast | null;
  battery: Battery | null;
}) {
  if (!forecast) return null;
  const nowT = Date.parse(forecast.now);
  const cur = forecast.current_soc_pct;

  const hist: Pt[] = forecast.history
    .map((h) => ({ t: Date.parse(h.ts), soc: h.soc_pct }))
    .filter((p) => Number.isFinite(p.t));
  const proj: Pt[] = forecast.projection
    .map((p) => ({ t: Date.parse(p.start), soc: p.soc_pct }))
    .filter((p) => Number.isFinite(p.t));

  // Bridge both lines through (now, current SoC) so actual meets predicted with no gap. Guard the
  // timestamp too — an unparseable `now` would inject NaN into both polylines and blank the chart.
  const bridge: Pt[] = cur != null && Number.isFinite(nowT) ? [{ t: nowT, soc: cur }] : [];
  const actual = [...hist, ...bridge];
  const predicted = [...bridge, ...proj];

  const all = [...actual, ...predicted];
  if (all.length < 2) {
    return (
      <section className="socchart" data-testid="soc-forecast">
        <div className="prices-head">
          <span className="metric-label">Battery SoC — recorded &amp; forecast</span>
        </div>
        {battery && <TowerChips battery={battery} />}
        <p className="plan-reason" data-testid="soc-forecast-empty">
          Gathering data — the SoC forecast appears once a plan and a little history exist.
        </p>
      </section>
    );
  }

  const minT = Math.min(...all.map((p) => p.t));
  const maxT = Math.max(...all.map((p) => p.t));
  const spanT = Math.max(1, maxT - minT);
  const x = (t: number) => PAD.l + ((t - minT) / spanT) * (W - PAD.l - PAD.r);
  const y = (soc: number) => PAD.t + (1 - soc / 100) * (H - PAD.t - PAD.b);

  const yGrid = [0, 25, 50, 75, 100];
  const reserveY = y(forecast.reserve_soc_pct);
  const nTicks = 6;
  const xTicks = Array.from({ length: nTicks + 1 }, (_, i) => minT + (spanT * i) / nTicks);

  const label =
    `Battery state of charge over time. ` +
    (cur != null ? `Now ${cur.toFixed(0)}%. ` : "") +
    forecast.summary;

  return (
    <section className="socchart" data-testid="soc-forecast">
      <div className="prices-head">
        <span className="metric-label">Battery SoC — recorded &amp; forecast</span>
        <span className="price-now" data-testid="soc-now">
          {cur != null ? `${cur.toFixed(0)}% now` : "—"}
        </span>
      </div>

      {battery && <TowerChips battery={battery} />}

      <svg
        className="soc-svg"
        viewBox={`0 0 ${W} ${H}`}
        preserveAspectRatio="xMidYMid meet"
        role="img"
        aria-label={label}
        data-testid="soc-svg"
      >
        {yGrid.map((g) => (
          <g key={g}>
            <line className="soc-grid" x1={PAD.l} y1={y(g)} x2={W - PAD.r} y2={y(g)} />
            <text className="soc-axis" x={2} y={y(g) + 3}>
              {g}
            </text>
          </g>
        ))}

        {/* reserve floor */}
        <line
          className="soc-reserve"
          x1={PAD.l}
          y1={reserveY}
          x2={W - PAD.r}
          y2={reserveY}
          data-testid="soc-reserve-line"
        />

        {/* now divider: solid history to the left, dashed prediction to the right */}
        <line className="soc-now" x1={x(nowT)} y1={PAD.t} x2={x(nowT)} y2={H - PAD.b} />

        {actual.length >= 2 && (
          <polyline className="soc-actual" points={poly(actual, x, y)} data-testid="soc-actual" />
        )}
        {predicted.length >= 2 && (
          <polyline
            className="soc-predicted"
            points={poly(predicted, x, y)}
            data-testid="soc-predicted"
          />
        )}

        {xTicks.map((t, i) => (
          <text key={i} className="soc-axis soc-xtick" x={x(t)} y={H - 6}>
            {clock(t)}
          </text>
        ))}
      </svg>

      <p className="soc-narrative" data-testid="soc-narrative">
        {forecast.summary}
      </p>
      <div className="soc-stats" data-testid="soc-stats">
        <span>
          Peak <b>{forecast.soc_max_pct != null ? `${forecast.soc_max_pct.toFixed(0)}%` : "—"}</b>
        </span>
        <span>
          Low <b>{forecast.soc_min_pct != null ? `${forecast.soc_min_pct.toFixed(0)}%` : "—"}</b>
        </span>
        <span>
          End <b>{forecast.soc_end_pct != null ? `${forecast.soc_end_pct.toFixed(0)}%` : "—"}</b>
        </span>
        <span>
          Grid <b>{forecast.import_kwh.toFixed(1)} kWh in</b> / {forecast.export_kwh.toFixed(1)} out
        </span>
        <span>
          Solar <b>{forecast.solar_kwh.toFixed(1)} kWh</b>
        </span>
      </div>

      <div className="legend" data-testid="soc-legend">
        <span className="legend-item">
          <span className="legend-line legend-actual" /> Recorded
        </span>
        <span className="legend-item">
          <span className="legend-line legend-predicted" /> Predicted
        </span>
        <span className="legend-item">
          <span className="legend-line legend-reserve" /> Reserve floor
        </span>
      </div>
    </section>
  );
}
