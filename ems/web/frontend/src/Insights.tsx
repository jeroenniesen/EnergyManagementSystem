// Insights view: the three energy scores (self-consumption, CO₂, best-price) + where every kWh
// went, over a day/week/month/year. Fetches /api/report only on mount + when the period/date
// changes — never on the dashboard poll, so it adds no recurring device load (figures are rolled up
// from recorded history server-side). Every score explains itself (the "why").
import { useEffect, useState } from "react";

import { EnergyBehavior, type SeriesBucket } from "./EnergyBehavior";
import { FinanceSection } from "./FinanceSection";
import { HeatingAdvice } from "./HeatingAdvice";
import { scoreBand, ScoreRing } from "./ScoreRing";
import { scoreCaption } from "./scoreCopy";
import { WeekDigest } from "./WeekDigest";

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

type GasSummary = {
  m3: number;
  kwh_eq: number;
  eur: number;
  co2_kg: number;
};

type Report = {
  period: string;
  label: string;
  partial: boolean;
  window_start?: string;
  window_end?: string;
  flows: Flows;
  scores: Score[];
  series?: SeriesBucket[];
  gas?: GasSummary | null;
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
// Day-just-starting (period=day, window still in progress, <1 kWh measured): a night reading is
// not a verdict — "You ran 0%… cut 0%…" at 00:30 read as failure in production. Calm line instead.
function isEarlyDay(period: string, report: Report, f: Flows): boolean {
  return period === "day" && report.partial && (f.home_kwh ?? 0) < 1.0;
}

function headline(report: Report, f: Flows, early: boolean): string {
  if (early) return "The day's just starting — scores build as the sun comes up.";
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

function GasPanel({ gas, partial }: { gas: GasSummary; partial: boolean }) {
  return (
    <div className="gas-panel" data-testid="gas-panel">
      <h3 className="card-title flow-title">Gas{partial ? " (so far)" : ""}</h3>
      <div className="gas-head">
        <span className="gas-dot" style={{ background: "var(--gas)" }} />
        <span className="gas-m3" data-testid="gas-m3">{gas.m3.toFixed(1)} m³</span>
        <span className="gas-window">used this window</span>
      </div>
      <div className="gas-tiles">
        <div className="gas-tile" data-testid="gas-kwh">
          <div className="gas-tile-val">{gas.kwh_eq.toFixed(0)} kWh</div>
          <div className="gas-tile-name">energy-equivalent</div>
        </div>
        <div className="gas-tile" data-testid="gas-eur">
          <div className="gas-tile-val">€{gas.eur.toFixed(2)}</div>
          <div className="gas-tile-name">cost</div>
        </div>
        <div className="gas-tile" data-testid="gas-co2">
          <div className="gas-tile-val">{gas.co2_kg.toFixed(1)} kg</div>
          <div className="gas-tile-name">CO₂</div>
        </div>
      </div>
      <p className="gas-hint">
        Heating is typically the biggest energy cost left — see the CO₂ score.
      </p>
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
      <WeekDigest />
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
          {headline(report, f, isEarlyDay(period, report, f)) && (
            <p className="insights-headline" data-testid="insights-headline">
              {headline(report, f, isEarlyDay(period, report, f))}
            </p>
          )}
          <div className={`score-grid${loading ? " is-loading" : ""}`} data-testid="score-grid">
            {report.scores.map((s) => {
              const early = isEarlyDay(period, report, f);
              const value = early ? null : s.value;
              return (
              <div
                key={s.key}
                className={`score-card score-${scoreBand(value)}`}
                data-testid={`score-${s.key}`}
                data-state={early ? "early" : undefined}
                role="group"
                aria-label={
                  early
                    ? `${s.label}: the day's just starting`
                    : `${s.label} score: ${
                        s.value == null ? "not available" : `${Math.round(s.value)} out of 100`
                      }`
                }
              >
                <ScoreRing
                  value={value}
                  label={s.label}
                  caption={early ? "Waiting for the sun" : scoreCaption(s.key, s.value)}
                  size={116}
                  testId={`score-${s.key}-value`}
                />
                <div className="score-detail">
                  {!early && s.raw != null && s.unit !== "%" && (
                    <div className="score-raw">{rawText(s)}</div>
                  )}
                  <p className="score-explain">
                    {early ? "Scores build as the day fills in." : s.explanation}
                  </p>
                </div>
              </div>
              );
            })}
          </div>

          {report.series && (
            <EnergyBehavior buckets={report.series} period={period} partial={report.partial} />
          )}

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

          {report.gas && (
            <>
              <GasPanel gas={report.gas} partial={report.partial} />
              <HeatingAdvice
                gas={report.gas}
                partial={report.partial}
                period={period}
                windowStart={report.window_start}
                windowEnd={report.window_end}
              />
            </>
          )}

          <FinanceSection period={period} anchor={anchor} />
        </>
      )}
    </section>
  );
}
