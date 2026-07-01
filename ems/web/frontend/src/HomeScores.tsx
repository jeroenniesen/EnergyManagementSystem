// The home-screen hero: today's three energy scores as Oura-style rings — the first, most motivating
// thing you see. Glanceable (number + label; full "why" on hover); tap any ring to open the Insights
// tab. Fetches /api/report?period=day once on mount (off the dashboard poll). Hidden until there's
// data to celebrate, so the home never shows empty rings.
import { useEffect, useState } from "react";

import { ScoreCard } from "./ScoreCard";
import { homeSummary } from "./scoreCopy";

type Score = { key: string; label: string; value: number | null; explanation: string };
type Report = { partial: boolean; flows: { has_data: boolean }; scores: Score[] };

export function HomeScores({ onOpenDetail }: { onOpenDetail: () => void }) {
  const [report, setReport] = useState<Report | null>(null);

  useEffect(() => {
    let alive = true;
    fetch("/api/report?period=day")
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error("report"))))
      .then((v: Report) => {
        if (alive) setReport(v);
      })
      .catch(() => {
        /* stay hidden on error — the dashboard carries its own error banner */
      });
    return () => {
      alive = false;
    };
  }, []);

  if (!report || !report.flows?.has_data) return null;

  const summary = homeSummary(report.scores);

  return (
    <section className="home-scores" data-testid="home-scores" aria-label="Today's energy scores">
      <div className="home-scores-head">
        <div className="home-scores-heading">
          <span className="home-scores-title">Today{report.partial ? " so far" : ""}</span>
          {summary && (
            <span
              className={`home-scores-summary tone-${summary.tone}`}
              data-testid="home-scores-summary"
              data-tone={summary.tone}
            >
              {summary.tone === "great" && (
                <span className="home-scores-spark" aria-hidden="true">
                  ☀️
                </span>
              )}
              {summary.text}
            </span>
          )}
        </div>
        <button
          type="button"
          className="home-scores-more"
          data-testid="home-scores-more"
          onClick={onOpenDetail}
        >
          Insights →
        </button>
      </div>
      <div className="home-scores-cards">
        {report.scores.map((s) => (
          <ScoreCard key={s.key} score={s} onOpen={onOpenDetail} />
        ))}
      </div>
    </section>
  );
}
