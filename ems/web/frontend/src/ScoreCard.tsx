// A home-screen score pill (B-32 "score pills"): a small ring on the left, the plain-language label
// + a short caption on the right. Compact — roughly a third the height of the old ring card — so the
// three scores read as one calm row rather than three competing hero cards. The whole pill is a
// button that opens the Insights tab; its accessible name carries the score value + copy.
import { ScoreRing } from "./ScoreRing";
import { scoreBand } from "./ScoreRing";
import { scoreCaption, scoreHeadline } from "./scoreCopy";

type Score = { key: string; label: string; value: number | null; explanation: string };

export function ScoreCard({ score, onOpen }: { score: Score; onOpen: () => void }) {
  const headline = scoreHeadline(score.key, score.value);
  const caption = scoreCaption(score.key, score.value);
  const band = scoreBand(score.value);
  const spoken =
    `${score.label} score: ${score.value == null ? "not available" : `${Math.round(score.value)} out of 100`}` +
    `${headline ? `. ${headline}` : ""}${caption ? ` ${caption}` : ""} — open Insights`;

  return (
    <button
      type="button"
      className={`score-pill score-${band}`}
      data-testid={`score-card-${score.key}`}
      aria-label={spoken}
      title={headline}
      onClick={onOpen}
    >
      <ScoreRing value={score.value} label={score.label} size={54} testId={`ring-${score.key}`} />
      <span className="sc-copy">
        {caption && <span className="sc-caption">{caption}</span>}
        {headline && <span className="sc-headline">{headline}</span>}
      </span>
    </button>
  );
}
