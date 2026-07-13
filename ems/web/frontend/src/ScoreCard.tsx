// A home-screen score pill (B-32 "score pills"): a small ring on the left, the plain-language label
// + a short caption on the right. Compact — roughly a third the height of the old ring card — so the
// three scores read as one calm row rather than three competing hero cards. The whole pill is a
// button that opens the Insights tab; its accessible name carries the score value + copy.
import { ScoreRing } from "./ScoreRing";
import { scoreBand } from "./ScoreRing";
import { scoreCaption, scoreHeadline } from "./scoreCopy";

type Score = { key: string; label: string; value: number | null; explanation: string };

export function ScoreCard({
  score,
  onOpen,
  early = false,
}: {
  score: Score;
  onOpen: () => void;
  early?: boolean; // day-just-starting: a night reading isn't a failure — show a calm dash, not a red 0
}) {
  const value = early ? null : score.value;
  const headline = early ? "" : scoreHeadline(score.key, score.value);
  const caption = early ? "The day's just starting" : scoreCaption(score.key, score.value);
  const band = scoreBand(value);
  const spoken = early
    ? `${score.label}: the day's just starting — scores build as the sun comes up. Open Insights`
    : `${score.label} score: ${score.value == null ? "not available" : `${Math.round(score.value)} out of 100`}` +
      `${headline ? `. ${headline}` : ""}${caption ? ` ${caption}` : ""} — open Insights`;

  return (
    <button
      type="button"
      className={`score-pill score-${band}`}
      data-state={early ? "early" : undefined}
      data-testid={`score-card-${score.key}`}
      aria-label={spoken}
      title={headline}
      onClick={onOpen}
    >
      <ScoreRing value={value} label={score.label} size={54} testId={`ring-${score.key}`} />
      <span className="sc-copy">
        {caption && <span className="sc-caption">{caption}</span>}
        {headline && <span className="sc-headline">{headline}</span>}
      </span>
    </button>
  );
}
