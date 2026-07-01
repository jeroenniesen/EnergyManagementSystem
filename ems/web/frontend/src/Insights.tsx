// Insights view: the three energy scores (self-consumption, CO₂, best-price) + where every kWh
// went, over a day/week/month/year. Fetches /api/report only on mount + when the period/date
// changes — never on the dashboard poll, so it adds no recurring device load (figures are rolled up
// from recorded history server-side). Every score explains itself (the "why").
import { useEffect, useState } from "react";

import { scoreBand, ScoreRing } from "./ScoreRing";
import { scoreCaption } from "./scoreCopy";

type Score = {
  key: string;
  label: string;
  value: number | null; // 0..100, 100 = best
  raw: number | null;
  unit: string;
  explanation: string;
};

type Flows = {
  has_data: boolean;
  partial: boolean;
  solar_kwh: number;
  grid_import_kwh: number;
  grid_export_kwh: number;
  battery_charge_kwh: number;
  battery_discharge_kwh: number;
  home_kwh: number;
  car_kwh: number;
  car_guard_leak_kwh: number;
  self_sufficiency_pct: number | null;
  solar_self_consumption_pct: number | null;
};

type Report = {
  period: string;
  label: string;
  partial: boolean;
  flows: Flows;
  scores: Score[];
};

type Period = "day" | "week" | "month" | "year";
const PERIODS: Period[] = ["day", "week", "month", "year"];

function ymd(dt: Date): string {
  return `${dt.getFullYear()}-${String(dt.getMonth() + 1).padStart(2, "0")}-${String(
    dt.getDate(),
  ).padStart(2, "0")}`;
}
const todayStr = () => ymd(new Date());

function shiftAnchor(anchor: string, period: Period, dir: number): string {
  const dt = new Date(`${anchor}T00:00:00`);
  if (period === "day") dt.setDate(dt.getDate() + dir);
  else if (period === "week") dt.setDate(dt.getDate() + 7 * dir);
  else if (period === "month") dt.setMonth(dt.getMonth() + dir);
  else dt.setFullYear(dt.getFullYear() + dir);
  return ymd(dt);
}

const kwh = (n: number) => `${n.toFixed(1)} kWh`;

function rawText(s: Score): string {
  if (s.raw == null) return "";
  if (s.unit === "kg") return `${s.raw.toFixed(1)} kg CO₂`;
  if (s.unit === "€/kWh") return `€${s.raw.toFixed(2)} / kWh avg import`;
  return `${s.raw} ${s.unit}`;
}

// A warm one-line summary of the window, synthesised from the flows + scores (goal A: motivation).
function headline(report: Report, f: Flows): string {
  const ss = f.self_sufficiency_pct;
  if (ss == null) return "";
  const co2 = report.scores.find((s) => s.key === "co2")?.value;
  const soFar = report.partial ? " so far" : "";
  let line = `You ran ${Math.round(ss)}% on your own solar + battery`;
  line += co2 != null ? ` and cut ${Math.round(co2)}% of a no-solar home's CO₂${soFar}.` : `${soFar}.`;
  return line;
}

function FlowRow({ color, label, val }: { color: string; label: string; val: number }) {
  return (
    <div className="flow-row">
      <span className="flow-dot" style={{ background: color }} />
      <span className="flow-name">{label}</span>
      <span className="flow-kwh">{kwh(val)}</span>
    </div>
  );
}

export function Insights() {
  const [period, setPeriod] = useState<Period>("day");
  const [anchor, setAnchor] = useState<string>(todayStr());
  const [report, setReport] = useState<Report | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    setError(false);
    fetch(`/api/report?period=${period}&date=${anchor}`)
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      .then((v: Report) => {
        if (alive) {
          setReport(v);
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
  }, [period, anchor]);

  const f = report?.flows;
  const hasData = !!f && f.has_data;

  return (
    <section className="insights" data-testid="insights" aria-label="Insights and reporting">
      <div className="insights-head">
        <div>
          <h2 className="card-title">Insights</h2>
          <p className="card-sub">Your energy scores and where every kWh went.</p>
        </div>
        <div className="period-picker" role="group" aria-label="Reporting period">
          {PERIODS.map((p) => (
            <button
              key={p}
              type="button"
              className={`period-btn${period === p ? " period-active" : ""}`}
              data-testid={`period-${p}`}
              aria-pressed={period === p}
              onClick={() => {
                setPeriod(p);
                setAnchor(todayStr());
              }}
            >
              {p[0].toUpperCase() + p.slice(1)}
            </button>
          ))}
        </div>
      </div>

      <div className="period-nav" role="group" aria-label="Choose a period">
        <button
          type="button"
          className="day-nav"
          aria-label="Previous period"
          data-testid="insights-prev"
          onClick={() => setAnchor((a) => shiftAnchor(a, period, -1))}
        >
          ‹
        </button>
        <span className="day-label" data-testid="insights-label" aria-live="polite">
          {report?.label ?? "…"}
        </span>
        <button
          type="button"
          className="day-nav"
          aria-label="Next period"
          data-testid="insights-next"
          disabled={!!report?.partial}
          onClick={() => setAnchor((a) => shiftAnchor(a, period, 1))}
        >
          ›
        </button>
      </div>

      {error && <p className="dist-msg">Couldn't load this report.</p>}
      {!error && loading && !report && <p className="dist-msg">Loading…</p>}
      {report && !error && !hasData && (
        <p className="dist-msg" data-testid="insights-empty">
          No energy recorded for this {period} yet.
        </p>
      )}

      {report && hasData && f && (
        <>
          {headline(report, f) && (
            <p className="insights-headline" data-testid="insights-headline">
              {headline(report, f)}
            </p>
          )}
          <div className={`score-grid${loading ? " is-loading" : ""}`} data-testid="score-grid">
            {report.scores.map((s) => (
              <div
                key={s.key}
                className={`score-card score-${scoreBand(s.value)}`}
                data-testid={`score-${s.key}`}
                role="group"
                aria-label={`${s.label} score: ${
                  s.value == null ? "not available" : `${Math.round(s.value)} out of 100`
                }`}
              >
                <ScoreRing
                  value={s.value}
                  label={s.label}
                  caption={scoreCaption(s.key, s.value)}
                  size={116}
                  testId={`score-${s.key}-value`}
                />
                <div className="score-detail">
                  {s.raw != null && s.unit !== "%" && <div className="score-raw">{rawText(s)}</div>}
                  <p className="score-explain">{s.explanation}</p>
                </div>
              </div>
            ))}
          </div>

          <div className="flow-report" data-testid="flow-report">
            <h3 className="card-title flow-title">
              Where your energy went{report.partial ? " (so far)" : ""}
            </h3>
            <div className="flow-cols">
              <div className="flow-col">
                <div className="flow-col-head">Came from</div>
                <FlowRow color="var(--summer)" label="Solar" val={f.solar_kwh} />
                <FlowRow color="var(--winter)" label="Grid" val={f.grid_import_kwh} />
                <FlowRow color="var(--accent)" label="Battery" val={f.battery_discharge_kwh} />
              </div>
              <div className="flow-col">
                <div className="flow-col-head">Went to</div>
                <FlowRow color="var(--text)" label="House" val={f.home_kwh} />
                <FlowRow color="var(--text)" label="Car" val={f.car_kwh} />
                <FlowRow color="var(--winter)" label="Exported" val={f.grid_export_kwh} />
                <FlowRow color="var(--accent)" label="Battery charged" val={f.battery_charge_kwh} />
              </div>
            </div>
            {f.car_guard_leak_kwh > 0.05 && (
              <p className="flow-warn" data-testid="leak-warn">
                ⚠ {kwh(f.car_guard_leak_kwh)} went from the battery into the car — the car-guard
                should prevent this.
              </p>
            )}
          </div>
        </>
      )}
    </section>
  );
}
