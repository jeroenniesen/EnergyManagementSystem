// Daily energy-distribution Sankey: where a day's energy came from (left: solar, grid, battery)
// and where it went (right: home, battery, export), band width = kWh. Self-contained — it fetches
// /api/energy-distribution only on mount and when the day changes, NEVER on the dashboard poll, so
// it adds no recurring load (the figures are rolled up from recorded history server-side).
import { useEffect, useState } from "react";

type Flows = {
  date: string;
  has_data: boolean;
  partial: boolean;
  solar_to_home: number;
  solar_to_battery: number;
  solar_to_grid: number;
  grid_to_home: number;
  grid_to_battery: number;
  battery_to_home: number;
  solar_kwh: number;
  grid_import_kwh: number;
  grid_export_kwh: number;
  battery_charge_kwh: number;
  battery_discharge_kwh: number;
  home_kwh: number;
  self_sufficiency_pct: number | null;
};

// Source colours (bands are coloured by where the energy came from). Match the rest of the app:
// solar = warm, grid = cool blue, battery = teal accent.
const SOLAR = "var(--summer)";
const GRID = "var(--winter)";
const BATTERY = "var(--accent)";

const EPS = 0.001;

// --- date helpers (local calendar day; the home's tz == the browser's, the usual case) ---
function ymd(dt: Date): string {
  return `${dt.getFullYear()}-${String(dt.getMonth() + 1).padStart(2, "0")}-${String(
    dt.getDate(),
  ).padStart(2, "0")}`;
}
function todayStr(): string {
  return ymd(new Date());
}
function shiftDate(d: string, days: number): string {
  const dt = new Date(`${d}T00:00:00`);
  dt.setDate(dt.getDate() + days);
  return ymd(dt);
}
function dayLabel(d: string): string {
  if (d === todayStr()) return "Today";
  if (d === shiftDate(todayStr(), -1)) return "Yesterday";
  return new Date(`${d}T00:00:00`).toLocaleDateString([], {
    weekday: "short",
    day: "numeric",
    month: "short",
  });
}
const kwh = (n: number) => `${n.toFixed(1)} kWh`;

// --- Sankey geometry ---
const VB_W = 600;
const VB_H = 300;
const PAD_TOP = 18;
const PAD_BOT = 14;
const NODE_W = 12;
const LEFT_X = 132;
const RIGHT_X = VB_W - 132 - NODE_W;
const GAP = 16; // vertical gap between stacked nodes
const AVAIL = VB_H - PAD_TOP - PAD_BOT;

type NodeDef = { id: string; label: string; value: number; color: string };
type Placed = NodeDef & { y: number; h: number; cursor: number };
type Band = {
  id: string; from: string; to: string; kwh: number; color: string; label: string; detail: string;
};

const SRC_NAME: Record<string, string> = {
  solar: "solar", grid: "grid import", "batt-out": "the battery",
};

function placeColumn(nodes: NodeDef[], scale: number, maxGaps: number): Placed[] {
  const shown = nodes.filter((n) => n.value > EPS);
  const gaps = (shown.length - 1) * GAP;
  const colH = shown.reduce((a, n) => a + n.value * scale, 0) + gaps;
  let y = PAD_TOP + (AVAIL - colH) / 2 + (maxGaps - gaps) / 2; // vertically centred
  return shown.map((n) => {
    const h = n.value * scale;
    const placed = { ...n, y, h, cursor: y };
    y += h + GAP;
    return placed;
  });
}

function ribbon(x0: number, y0: number, x1: number, y1: number, t: number): string {
  const xm = (x0 + x1) / 2;
  return (
    `M${x0},${y0} C${xm},${y0} ${xm},${y1} ${x1},${y1} ` +
    `L${x1},${y1 + t} C${xm},${y1 + t} ${xm},${y0 + t} ${x0},${y0 + t} Z`
  );
}

export function EnergyDistribution() {
  const [date, setDate] = useState<string>(todayStr());
  const [flows, setFlows] = useState<Flows | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [active, setActive] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    setError(false);
    fetch(`/api/energy-distribution?date=${date}`)
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      .then((v: Flows) => {
        if (alive) {
          setFlows(v);
          setLoading(false);
        }
      })
      .catch(() => {
        if (alive) {
          setError(true);
          setLoading(false);
        }
      });
    return () => {
      alive = false;
    };
  }, [date]);

  const isToday = date === todayStr();

  const sourceTotal: Record<string, number> = flows
    ? { solar: flows.solar_kwh, grid: flows.grid_import_kwh,
        "batt-out": flows.battery_discharge_kwh }
    : {};
  const bands: Band[] = (flows
    ? [
        { id: "s-h", from: "solar", to: "home", kwh: flows.solar_to_home, color: SOLAR,
          label: "Solar → Home" },
        { id: "s-b", from: "solar", to: "batt-in", kwh: flows.solar_to_battery, color: SOLAR,
          label: "Solar → Battery" },
        { id: "s-g", from: "solar", to: "export", kwh: flows.solar_to_grid, color: SOLAR,
          label: "Solar → Grid (export)" },
        { id: "g-h", from: "grid", to: "home", kwh: flows.grid_to_home, color: GRID,
          label: "Grid → Home" },
        { id: "g-b", from: "grid", to: "batt-in", kwh: flows.grid_to_battery, color: GRID,
          label: "Grid → Battery" },
        { id: "b-h", from: "batt-out", to: "home", kwh: flows.battery_to_home, color: BATTERY,
          label: "Battery → Home" },
      ]
    : []
  )
    .filter((b) => b.kwh > EPS)
    .map((b) => {
      const st = sourceTotal[b.from] ?? 0;
      const pct = st > EPS ? Math.round((b.kwh / st) * 100) : 0;
      const share = pct ? ` — ${pct}% of ${SRC_NAME[b.from] ?? "its source"}` : "";
      return { ...b, detail: `${b.label}: ${kwh(b.kwh)}${share}` };
    });

  const total = bands.reduce((a, b) => a + b.kwh, 0);

  const left: NodeDef[] = flows
    ? [
        { id: "solar", label: "Solar", value: flows.solar_kwh, color: SOLAR },
        { id: "grid", label: "Grid", value: flows.grid_import_kwh, color: GRID },
        { id: "batt-out", label: "Battery", value: flows.battery_discharge_kwh, color: BATTERY },
      ]
    : [];
  const right: NodeDef[] = flows
    ? [
        { id: "home", label: "Home", value: flows.home_kwh, color: "var(--text)" },
        { id: "batt-in", label: "Battery", value: flows.battery_charge_kwh, color: BATTERY },
        { id: "export", label: "Export", value: flows.grid_export_kwh, color: GRID },
      ]
    : [];

  const hasData = !!flows && flows.has_data && total > EPS;

  let placed: Record<string, Placed> = {};
  const paths: { band: Band; d: string }[] = [];
  if (hasData) {
    const leftGaps = (left.filter((n) => n.value > EPS).length - 1) * GAP;
    const rightGaps = (right.filter((n) => n.value > EPS).length - 1) * GAP;
    const maxGaps = Math.max(leftGaps, rightGaps);
    const scale = (AVAIL - maxGaps) / total;
    const lp = placeColumn(left, scale, maxGaps);
    const rp = placeColumn(right, scale, maxGaps);
    placed = Object.fromEntries([...lp, ...rp].map((p) => [p.id, p]));
    for (const band of bands) {
      const s = placed[band.from];
      const d = placed[band.to];
      if (!s || !d) continue;
      const t = band.kwh * scale;
      paths.push({ band, d: ribbon(LEFT_X + NODE_W, s.cursor, RIGHT_X, d.cursor, t) });
      s.cursor += t;
      d.cursor += t;
    }
  }

  return (
    <section
      className="card energy-dist"
      data-testid="energy-distribution"
      aria-label="Daily energy distribution"
    >
      <div className="energy-dist-head">
        <div>
          <h2 className="card-title">Energy distribution</h2>
          <p className="card-sub">Where the day's energy came from and where it went.</p>
        </div>
        <div className="day-picker" role="group" aria-label="Choose a day">
          <button
            type="button"
            className="day-nav"
            aria-label="Previous day"
            data-testid="dist-prev"
            onClick={() => setDate((d) => shiftDate(d, -1))}
          >
            ‹
          </button>
          <span className="day-label" data-testid="dist-day">
            {dayLabel(date)}
          </span>
          <button
            type="button"
            className="day-nav"
            aria-label="Next day"
            data-testid="dist-next"
            disabled={isToday}
            onClick={() => setDate((d) => shiftDate(d, 1))}
          >
            ›
          </button>
        </div>
      </div>

      {error && !flows && <p className="dist-msg">Couldn't load this day.</p>}
      {!flows && loading && <p className="dist-msg">Loading…</p>}
      {flows && !hasData && (
        <p className="dist-msg" data-testid="dist-empty">
          No energy recorded for {dayLabel(date).toLowerCase()} yet.
        </p>
      )}

      {flows && hasData && (
        <>
          {/* Wide diagram scrolls on narrow screens (keeps labels legible) rather than shrinking;
              dims while a newly-chosen day loads instead of blanking. */}
          <div className={`sankey-scroll${loading ? " is-loading" : ""}`}>
          <svg
            className="sankey"
            viewBox={`0 0 ${VB_W} ${VB_H}`}
            role="img"
            aria-label={`Energy distribution for ${dayLabel(date)}`}
            data-testid="sankey"
            onMouseLeave={() => setActive(null)}
          >
            <text className="sankey-col" x={LEFT_X + NODE_W} y={9} textAnchor="end">
              FROM
            </text>
            <text className="sankey-col" x={RIGHT_X} y={9} textAnchor="start">
              TO
            </text>
            {paths.map(({ band, d }) => (
              <path
                key={band.id}
                d={d}
                className={`sankey-band${active && active !== band.id ? " is-dim" : ""}`}
                fill={band.color}
                data-testid={`band-${band.id}`}
                onMouseEnter={() => setActive(band.id)}
                onFocus={() => setActive(band.id)}
                tabIndex={0}
                role="img"
                aria-label={band.detail}
              >
                <title>{band.detail}</title>
              </path>
            ))}
            {Object.values(placed).map((n) => {
              const isLeft = ["solar", "grid", "batt-out"].includes(n.id);
              const x = isLeft ? LEFT_X : RIGHT_X;
              return (
                <g key={n.id}>
                  <rect
                    x={x}
                    y={n.y}
                    width={NODE_W}
                    height={Math.max(1, n.h)}
                    rx={3}
                    fill={n.color}
                  />
                  <text
                    className="sankey-label"
                    x={isLeft ? x - 8 : x + NODE_W + 8}
                    y={n.y + n.h / 2}
                    textAnchor={isLeft ? "end" : "start"}
                    dominantBaseline="middle"
                  >
                    <tspan className="sankey-name">{n.label}</tspan>
                    <tspan className="sankey-kwh" x={isLeft ? x - 8 : x + NODE_W + 8} dy="1.15em">
                      {kwh(n.value)}
                    </tspan>
                  </text>
                </g>
              );
            })}
          </svg>
          </div>

          <div className="dist-summary" data-testid="dist-summary">
            {flows.self_sufficiency_pct != null && (
              <span className="dist-headline" data-testid="dist-selfsuff">
                <strong>{flows.self_sufficiency_pct.toFixed(0)}%</strong> of your home ran on solar
                + battery{flows.partial ? " so far today" : ""}.
              </span>
            )}
            {flows.grid_to_battery > EPS && (
              <span className="dist-note">
                {" "}
                {kwh(flows.grid_to_battery)} was bought from the grid to charge the battery.
              </span>
            )}
          </div>

          <div className="legend dist-legend">
            <span className="legend-item">
              <span className="legend-swatch" style={{ background: SOLAR }} /> Solar
            </span>
            <span className="legend-item">
              <span className="legend-swatch" style={{ background: GRID }} /> Grid
            </span>
            <span className="legend-item">
              <span className="legend-swatch" style={{ background: BATTERY }} /> Battery
            </span>
          </div>
        </>
      )}
    </section>
  );
}
