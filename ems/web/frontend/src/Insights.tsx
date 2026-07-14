// Insights view: the three energy scores (self-consumption, CO₂, best-price) + where every kWh
// went, over a day/week/month/year. Fetches /api/report only on mount + when the period/date
// changes — never on the dashboard poll, so it adds no recurring device load (figures are rolled up
// from recorded history server-side). Every score explains itself (the "why").
import { useEffect, useState } from "react";

import { EnergyBehavior, type SeriesBucket } from "./EnergyBehavior";
import { FinanceSection } from "./FinanceSection";
import { HeatingAdvice } from "./HeatingAdvice";
import { scoreBand, ScoreRing } from "./ScoreRing";
import { ringLabel, scoreCaption, splitExplanation } from "./scoreCopy";
import { WeekDigest } from "./WeekDigest";
import { WhatIf } from "./WhatIf";

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

// B-06: "you vs last {period}" — a small trend chip per score card. Reuses the same period-nav
// date math (shiftAnchor) to fetch the SAME period one step back, then diffs each score by key.
// Muted styling throughout: a worse score is never alarm-red, just a muted amber "▼".
type Trend = { dir: "up" | "down" | "same"; diff: number };

function scoreTrend(score: Score, prevReport: Report | null): Trend | null {
  if (!prevReport || !prevReport.flows?.has_data || score.value == null) return null;
  const prevScore = prevReport.scores.find((p) => p.key === score.key);
  if (!prevScore || prevScore.value == null) return null;
  const diff = Math.round(score.value - prevScore.value);
  return { dir: diff > 0 ? "up" : diff < 0 ? "down" : "same", diff };
}

function trendLabel(trend: Trend, period: Period): string {
  if (trend.dir === "up") return `▲ +${trend.diff} vs last ${period}`;
  if (trend.dir === "down") return `▼ −${Math.abs(trend.diff)} vs last ${period}`;
  return `· same as last ${period}`;
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

// The score card's ONE detail line (first sentence of the score's explanation, always shown) +
// the remainder behind a "More" disclosure — the exact same progressive-disclosure idiom
// Settings' field help uses (splitHelp/FieldHelp: first sentence always visible, the rest one
// tap away), reused here so a two-sentence score (e.g. CO₂ with a gas footnote) doesn't read as
// a wall of text next to a one-sentence score (production screenshot finding). The full
// explanation is ALSO available without tapping, via the ring's own hover tooltip.
function ScoreDetailLine({ text, testId }: { text: string; testId: string }) {
  const { first, rest } = splitExplanation(text);
  const [open, setOpen] = useState(false);
  if (!rest) {
    return (
      <p className="score-explain" data-testid={testId}>
        {first}
      </p>
    );
  }
  return (
    <p className="score-explain" data-testid={testId}>
      {first}
      {open ? ` ${rest}` : ""}{" "}
      <button
        type="button"
        className="help-more"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        {open ? "Less" : "More"}
      </button>
    </p>
  );
}

// Slim sticky in-page section nav (roadmap: Insights is now a long stack — digest, scores,
// behavior, flows, gas, heating, finance, what-if). Appears only once the user has scrolled past
// the top of the page (a fixed bar sliding in, the same idiom as Settings' sticky save bar —
// see .settings-savebar — just flipped to the top), and tracks which section is currently under
// the bar via `aria-current`. Never touches `window.location.hash` — App.tsx's hash router treats
// any unrecognised fragment as "dashboard" (see viewFromHash), so a real `href="#id"` anchor would
// silently navigate the WHOLE APP away from Insights; scrollIntoView keeps this purely in-page.
type NavSection = { id: string; label: string };

function useSectionNav(sections: NavSection[]): { activeId: string | null; visible: boolean } {
  const [activeId, setActiveId] = useState<string | null>(null);
  const [visible, setVisible] = useState(false);
  const key = sections.map((s) => s.id).join("|");

  useEffect(() => {
    if (sections.length === 0) {
      setVisible(false);
      setActiveId(null);
      return;
    }
    function onScroll() {
      setVisible(window.scrollY > 280);
      // The section whose top has scrolled up past the bar's height "owns" the current view.
      let current = sections[0].id;
      for (const s of sections) {
        const el = document.getElementById(s.id);
        if (el && el.getBoundingClientRect().top <= 96) current = s.id;
      }
      // The LAST section can be too short to ever scroll its own top past the 96px line (nothing
      // below it to scroll further into) — once the page is scrolled to its bottom, that section
      // is unambiguously the one in view, regardless of where its top sits.
      const atBottom =
        window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 4;
      if (atBottom) current = sections[sections.length - 1].id;
      setActiveId(current);
    }
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", onScroll);
    return () => {
      window.removeEventListener("scroll", onScroll);
      window.removeEventListener("resize", onScroll);
    };
    // `key` (the joined id list) is the real dependency — it changes exactly when `sections`'
    // content changes, and the effect closes over the current `sections` from this render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  return { activeId, visible };
}

function scrollToSection(id: string) {
  const el = document.getElementById(id);
  if (!el) return;
  const reduce = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
  el.scrollIntoView({ behavior: reduce ? "auto" : "smooth", block: "start" });
}

function SectionNav({ sections, activeId }: { sections: NavSection[]; activeId: string | null }) {
  return (
    <nav
      className="insights-section-nav-bar"
      aria-label="Jump to section"
      data-testid="insights-section-nav"
    >
      <div className="insights-section-nav-inner">
        {sections.map((s) => (
          <button
            key={s.id}
            type="button"
            className="insights-section-nav-item"
            data-testid={`insights-nav-${s.id.replace("insights-sec-", "")}`}
            aria-current={activeId === s.id ? "true" : undefined}
            onClick={() => scrollToSection(s.id)}
          >
            {s.label}
          </button>
        ))}
      </div>
    </nav>
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
  const [prevReport, setPrevReport] = useState<Report | null>(null);
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

  // B-06: one extra, best-effort fetch per period/anchor change — the SAME period one step back,
  // reusing shiftAnchor's date math — so every score card can show a "vs last {period}" trend
  // without each card issuing its own request. Failure (or no history that far back) just means no
  // trend chip renders; it never blocks or errors the main report.
  useEffect(() => {
    let alive = true;
    const prevAnchor = shiftAnchor(anchor, period, -1);
    fetch(`/api/report?period=${period}&date=${prevAnchor}`)
      .then((r) => (r.ok ? r.json() : null))
      .then((v: Report | null) => {
        if (alive) setPrevReport(v);
      })
      .catch(() => {
        if (alive) setPrevReport(null);
      });
    return () => {
      alive = false;
    };
  }, [period, anchor]);

  const f = report?.flows;
  const hasData = !!f && f.has_data;

  // Which sections actually exist right now (Gas only when the report has gas data; everything
  // past "Week" needs hasData) — the nav only ever links to something that's really on the page.
  // Ordered to match the page's actual top-to-bottom order (Gas sits before Money in the stack
  // today — see the "no reordering" note below) so the active-section scan and each tap's
  // direction stay sane.
  const sections: NavSection[] = [{ id: "insights-sec-week", label: "Week" }];
  if (report && hasData && f) {
    sections.push({ id: "insights-sec-scores", label: "Scores" });
    sections.push({ id: "insights-sec-energy", label: "Energy" });
    if (report.gas) sections.push({ id: "insights-sec-gas", label: "Gas" });
    sections.push({ id: "insights-sec-money", label: "Money" });
    sections.push({ id: "insights-sec-whatif", label: "What-if" });
  }
  const { activeId, visible } = useSectionNav(sections);

  return (
    <section className="insights" data-testid="insights" aria-label="Insights and reporting">
      {visible && sections.length > 1 && <SectionNav sections={sections} activeId={activeId} />}
      <div id="insights-sec-week">
        <WeekDigest />
      </div>
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
          {/* One consistent anatomy per card (B-37-style): ring | headline word | trend chip |
              ONE detail line. The full explanation lives behind the ring's own hover tooltip AND
              a "More" disclosure on the detail line (Settings' field-help idiom) — never as a
              second/third paragraph, so a two-sentence score (CO₂ with a gas footnote) doesn't
              tower over a one-sentence score (production screenshot: wall-of-text vs. an
              almost-empty card side by side). */}
          <div
            id="insights-sec-scores"
            className={`score-grid${loading ? " is-loading" : ""}`}
            data-testid="score-grid"
          >
            {report.scores.map((s) => {
              const early = isEarlyDay(period, report, f);
              const value = early ? null : s.value;
              const headlineWord = early ? "Waiting for the sun" : (scoreCaption(s.key, s.value) ?? "—");
              const trend = early ? null : scoreTrend(s, prevReport);
              const detailText = early ? "Scores build as the day fills in." : s.explanation;
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
                  label={ringLabel(s.key, s.label)}
                  hint={early ? undefined : s.explanation}
                  size={116}
                  testId={`score-${s.key}-value`}
                />
                <div className="score-detail">
                  <p className="score-headline" data-testid={`score-${s.key}-headline`}>
                    {headlineWord}
                  </p>
                  {trend && (
                    <div
                      className={`score-trend score-trend-${trend.dir}`}
                      data-testid={`score-${s.key}-trend`}
                    >
                      {trendLabel(trend, period)}
                    </div>
                  )}
                  <ScoreDetailLine text={detailText} testId={`score-${s.key}-line`} />
                </div>
              </div>
              );
            })}
          </div>

          <div id="insights-sec-energy">
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
          </div>

          {/* Page order stays exactly as it was (Gas before Money) — a restructure of the stack
              is a separate loop's job; the nav below is ordered to MATCH this actual order so
              the active-section tracking and the "next" direction of each tap stay sane. */}
          {report.gas && (
            <div id="insights-sec-gas">
              <GasPanel gas={report.gas} partial={report.partial} />
              <HeatingAdvice
                gas={report.gas}
                partial={report.partial}
                period={period}
                windowStart={report.window_start}
                windowEnd={report.window_end}
              />
            </div>
          )}

          <div id="insights-sec-money">
            <FinanceSection period={period} anchor={anchor} />
          </div>

          <div id="insights-sec-whatif">
            <WhatIf />
          </div>
        </>
      )}
    </section>
  );
}
