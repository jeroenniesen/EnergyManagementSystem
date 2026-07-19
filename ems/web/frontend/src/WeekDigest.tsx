// "Your week" panel (BACKLOG B-58 / roadmap P2 "Trust at a glance"): what you saved, what the
// system did, one suggested tweak — the advisor voice, on a schedule. Sits at the TOP of Insights,
// above the score grid, so a household member answers "did we do well this week?" in 10 seconds
// (the roadmap bar) before any score/number justifies it. Its own /api/digest fetch, independent
// of Insights' own day/week/month/year period picker — this is always a WEEK, with its own small
// ‹ › stepper (mirrors Insights' period-nav). Collapsible to one line (the headline itself, like
// the Advanced disclosure toggle) so it never dominates a repeat visit.
import { useEffect, useState } from "react";

import { apiFetch } from "./auth";
import { eur } from "./format";

type BestDay = { date: string; saved_eur: number } | null;

type Digest = {
  week_label: string;
  saved_eur: number | null;
  best_day: BestDay;
  self_sufficiency_pct: number | null;
  solar_kwh: number;
  co2_avoided_note: string | null;
  actions: { mode_switches: number; negative_soaks: number; overrides: number };
  tweak: string | null;
  headline: string;
  days_measured: number;
  days_total: number;
};

// The trailing YYYY-MM-DD in a "Week of YYYY-MM-DD" label — mirrors ems/digest.py's own parsing,
// so the stepper can compute the adjacent week without a second round-trip just to learn the date.
function mondayOf(label: string): string | null {
  const m = label.match(/(\d{4}-\d{2}-\d{2})\s*$/);
  return m ? m[1] : null;
}

function shiftWeek(monday: string, dir: number): string {
  const dt = new Date(`${monday}T00:00:00`);
  dt.setDate(dt.getDate() + 7 * dir);
  return `${dt.getFullYear()}-${String(dt.getMonth() + 1).padStart(2, "0")}-${String(
    dt.getDate(),
  ).padStart(2, "0")}`;
}

export function WeekDigest() {
  const [anchor, setAnchor] = useState<string>(""); // "" = let the server pick the default week
  const [digest, setDigest] = useState<Digest | null>(null);
  const [error, setError] = useState(false);
  const [open, setOpen] = useState(true);

  useEffect(() => {
    let alive = true;
    setError(false);
    const url = anchor ? `/api/digest?week=${anchor}` : "/api/digest";
    apiFetch(url)
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      .then((v: Digest) => alive && setDigest(v))
      .catch(() => alive && setError(true));
    return () => {
      alive = false;
    };
  }, [anchor]);

  // Best-effort like the other Insights sub-panels (FinanceSection, HeatingAdvice): a digest
  // hiccup never blocks the scores/flows/finance the rest of Insights already shows.
  if (error || !digest) return null;

  const monday = mondayOf(digest.week_label);
  const step = (dir: number) => {
    if (monday) setAnchor(shiftWeek(monday, dir));
  };
  const partial = digest.days_measured < digest.days_total;
  const actionsTotal = digest.actions.mode_switches + digest.actions.overrides;

  return (
    <section className="week-digest" data-testid="week-digest" aria-label="Your week">
      <div className="week-digest-head">
        <button
          type="button"
          className={`week-digest-toggle${open ? " open" : ""}`}
          data-testid="week-digest-toggle"
          aria-expanded={open}
          onClick={() => setOpen((o) => !o)}
        >
          <span className="week-digest-chevron" aria-hidden="true">
            ›
          </span>
          <span data-testid="week-digest-headline">{digest.headline}</span>
        </button>
        <div className="week-nav" role="group" aria-label="Choose a week">
          <button
            type="button"
            className="day-nav"
            aria-label="Previous week"
            data-testid="week-digest-prev"
            disabled={!monday}
            onClick={() => step(-1)}
          >
            ‹
          </button>
          <span className="day-label" data-testid="week-digest-label">
            {digest.week_label}
          </span>
          <button
            type="button"
            className="day-nav"
            aria-label="Next week"
            data-testid="week-digest-next"
            disabled={!monday}
            onClick={() => step(1)}
          >
            ›
          </button>
        </div>
      </div>

      {open && (
        <div className="week-digest-body" data-testid="week-digest-body">
          <div className="week-digest-hero">
            <span
              className={`week-digest-hero-val${
                digest.saved_eur != null && digest.saved_eur >= 0 ? " week-digest-hero-good" : ""
              }`}
              data-testid="week-digest-saved"
            >
              {digest.saved_eur == null ? "—" : eur(digest.saved_eur)}
            </span>
            <span className="week-digest-hero-label">saved this week</span>
          </div>

          <div className="week-digest-facts">
            <div className="week-digest-fact" data-testid="week-digest-fact-self-sufficiency">
              <div className="week-digest-fact-val">
                {digest.self_sufficiency_pct == null
                  ? "—"
                  : `${Math.round(digest.self_sufficiency_pct)}%`}
              </div>
              <div className="week-digest-fact-name">self-sufficient</div>
            </div>
            <div className="week-digest-fact" data-testid="week-digest-fact-solar">
              <div className="week-digest-fact-val">{digest.solar_kwh % 1 === 0 ? digest.solar_kwh.toFixed(0) : digest.solar_kwh.toFixed(1)} kWh</div>
              <div className="week-digest-fact-name">from the sun</div>
            </div>
            <div
              className="week-digest-fact"
              data-testid="week-digest-fact-actions"
              title={`${digest.actions.mode_switches} battery mode changes · ${digest.actions.negative_soaks} paid-to-charge · ${digest.actions.overrides} manual`}
            >
              <div className="week-digest-fact-val">{actionsTotal}</div>
              <div className="week-digest-fact-name">battery adjustments</div>
            </div>
          </div>

          {digest.best_day && (
            <p className="week-digest-best-day" data-testid="week-digest-best-day">
              Best day: {digest.best_day.date} ({eur(digest.best_day.saved_eur)})
            </p>
          )}

          {digest.tweak && (
            <p className="advisor-hint week-digest-tweak" data-testid="week-digest-tweak">
              {digest.tweak}
            </p>
          )}

          {partial && (
            <p className="week-digest-coverage" data-testid="week-digest-coverage">
              {digest.days_measured} of {digest.days_total} days measured
            </p>
          )}
        </div>
      )}
    </section>
  );
}
