// The home-screen score row: today's three energy scores as one compact row of pills (B-32). Same
// data as the Insights rings, ~a third the height — glanceable, and each pill taps through to the
// Insights tab. The report is fetched once by App (so the hero can synthesise the same summary line)
// and passed in; the row stays hidden until there's data to show, so the home never renders empty.
import { ScoreCard } from "./ScoreCard";
import { homeSummary } from "./scoreCopy";

export type Score = { key: string; label: string; value: number | null; explanation: string };
export type Report = { partial: boolean; flows: { has_data: boolean }; scores: Score[] };

export function HomeScores({
  report,
  onOpenDetail,
}: {
  report: Report | null;
  onOpenDetail: () => void;
}) {
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
      <div className="home-scores-pills">
        {report.scores.map((s) => (
          <ScoreCard key={s.key} score={s} onOpen={onOpenDetail} />
        ))}
      </div>
    </section>
  );
}
