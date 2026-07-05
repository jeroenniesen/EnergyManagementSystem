import { Icon } from "./icons";

type Point = { ts: string; soc_pct: number | null };
type SolarPoint = { ts: string; forecast_w: number; actual_w: number | null };
type Block = { start: string; end: string; action: string };
type PriceWindow = {
  start: string;
  end: string;
  min_eur_per_kwh: number;
  max_eur_per_kwh: number;
};

export type BatteryPlanData = {
  status: "on_track" | "needs_topup" | "behind_target" | "paused_safely" | "data_stale";
  summary: string;
  current_action: "grid_charge" | "solar_charge" | "hold" | "discharge" | "self_consume" | "paused";
  current_reason: string;
  window_start: string;
  window_end: string;
  current_soc_pct: number | null;
  reserve_soc_pct: number;
  target_soc_pct: number | null;
  target_deadline: string | null;
  deviation: { status: "ok" | "behind_forecast" | "missing"; message: string };
  warnings: string[];
  graph: {
    forecast_soc: Point[];
    actual_soc: Point[];
    reserve_line: Point[];
    target_line: Point[];
    planned_actions: Block[];
    price_windows: PriceWindow[];
    solar: SolarPoint[];
  };
};

const STATUS_LABEL: Record<BatteryPlanData["status"], string> = {
  on_track: "On track",
  needs_topup: "Needs top-up",
  behind_target: "Behind target",
  paused_safely: "Paused safely",
  data_stale: "Data stale",
};

const ACTION_LABEL: Record<string, string> = {
  grid_charge: "charging from grid",
  solar_charge: "charging from solar",
  hold: "holding",
  discharge: "discharging",
  self_consume: "self-use",
  paused: "paused",
};

const W = 900;
const H = 170;
const PAD = { l: 28, r: 12, t: 12, b: 18 };

type XY = { x: number; y: number };

// Split a series into contiguous runs of plotted points, BREAKING at any missing sample
// (soc_pct null / unparseable ts) instead of interpolating across the gap — matching the
// gaps-not-zeros rule the production EnergyBehavior chart uses. Runs of length 1 are drawn as dots.
function segments(points: Point[], x: (ts: string) => number, y: (soc: number) => number): XY[][] {
  const runs: XY[][] = [];
  let cur: XY[] = [];
  for (const p of points) {
    if (p.soc_pct != null && Number.isFinite(Date.parse(p.ts))) {
      cur.push({ x: x(p.ts), y: y(p.soc_pct) });
    } else if (cur.length) {
      runs.push(cur);
      cur = [];
    }
  }
  if (cur.length) runs.push(cur);
  return runs;
}

const poly = (seg: XY[]) => seg.map((p) => `${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(" ");

export function BatteryPlan({ plan }: { plan: BatteryPlanData | null }) {
  if (!plan) {
    return (
      <section className="battery-plan battery-plan-loading" data-testid="battery-plan">
        <p className="battery-plan-summary">Loading battery plan…</p>
      </section>
    );
  }

  const start = Date.parse(plan.window_start);
  const end = Date.parse(plan.window_end);
  const span = Math.max(1, end - start);
  const x = (ts: string) => PAD.l + ((Date.parse(ts) - start) / span) * (W - PAD.l - PAD.r);
  const y = (soc: number) => PAD.t + (1 - Math.max(0, Math.min(100, soc)) / 100) * (H - PAD.t - PAD.b);
  const forecastSegs = segments(plan.graph.forecast_soc, x, y);
  const actualSegs = segments(plan.graph.actual_soc, x, y);
  const maxSolar = Math.max(1, ...plan.graph.solar.map((p) => p.forecast_w));
  const tone = plan.status === "on_track" ? "good" :
    plan.status === "needs_topup" ? "warn" :
    plan.status === "behind_target" || plan.status === "data_stale" ? "bad" : "muted";

  return (
    <section className={`battery-plan battery-plan-${tone}`} data-testid="battery-plan" data-status={plan.status}>
      <div className="battery-plan-top">
        <span className={`battery-plan-status battery-plan-status-${tone}`}>
          {STATUS_LABEL[plan.status]}
        </span>
        <span className="battery-plan-action">{ACTION_LABEL[plan.current_action]}</span>
      </div>
      <p className="battery-plan-summary" data-testid="battery-plan-summary">{plan.summary}</p>
      <p className="battery-plan-reason" data-testid="battery-plan-reason">{plan.current_reason}</p>
      {plan.warnings.length > 0 && (
        <div className="battery-plan-warning" data-testid="battery-plan-warning">
          <Icon name="alert" /> {plan.warnings[0]}
        </div>
      )}

      <svg
        className="battery-plan-chart"
        viewBox={`0 0 ${W} ${H}`}
        role="img"
        aria-label={`${STATUS_LABEL[plan.status]}. ${plan.summary}`}
        data-testid="battery-plan-chart"
      >
        <rect x="0" y="0" width={W} height={H} rx="10" className="bp-bg" />
        {plan.graph.price_windows.map((p) => (
          <rect
            key={`${p.start}-${p.end}`}
            x={x(p.start)}
            y={PAD.t}
            width={Math.max(1, x(p.end) - x(p.start))}
            height={H - PAD.t - PAD.b}
            className="bp-price-window"
          />
        ))}
        {plan.graph.planned_actions.map((b) => (
          <rect
            key={`${b.start}-${b.end}-${b.action}`}
            x={x(b.start)}
            y={H - PAD.b + 3}
            width={Math.max(1, x(b.end) - x(b.start))}
            height="7"
            className={`bp-action bp-action-${b.action}`}
          />
        ))}
        {plan.graph.solar.map((s) => {
          const h = (s.forecast_w / maxSolar) * 28;
          return (
            <rect
              key={s.ts}
              x={x(s.ts)}
              y={H - PAD.b - h}
              width="3"
              height={h}
              className="bp-solar"
            />
          );
        })}
        {plan.graph.reserve_line.length > 0 && (
          <line
            x1={PAD.l}
            x2={W - PAD.r}
            y1={y(plan.reserve_soc_pct)}
            y2={y(plan.reserve_soc_pct)}
            className="bp-reserve"
          />
        )}
        {plan.target_soc_pct != null && (
          <line
            x1={PAD.l}
            x2={W - PAD.r}
            y1={y(plan.target_soc_pct)}
            y2={y(plan.target_soc_pct)}
            className="bp-target"
          />
        )}
        {forecastSegs.map((seg, i) =>
          seg.length >= 2
            ? <polyline key={`f${i}`} points={poly(seg)} className="bp-forecast" />
            : <circle key={`f${i}`} cx={seg[0].x} cy={seg[0].y} r={2.4} className="bp-forecast-dot" />
        )}
        {actualSegs.map((seg, i) =>
          seg.length >= 2
            ? <polyline key={`a${i}`} points={poly(seg)} className="bp-actual" />
            : <circle key={`a${i}`} cx={seg[0].x} cy={seg[0].y} r={2.4} className="bp-actual-dot" />
        )}
      </svg>
      <div className="battery-plan-legend" aria-hidden="true">
        <span><i className="leg actual" /> actual</span>
        <span><i className="leg forecast" /> forecast</span>
        <span><i className="leg cheap" /> cheap window</span>
        <span><i className="leg target" /> target</span>
        <span><i className="leg reserve" /> reserve</span>
      </div>
    </section>
  );
}
