// A home-screen score card (matches the visual designer's mockup): the score ring on the left, a
// plain-language headline + motivating caption on the right, a soft hills illustration along the
// bottom and a themed emblem in the corner. The whole card is a button that opens Insights.
import type { ReactNode } from "react";

import { ScoreRing } from "./ScoreRing";
import { scoreBand } from "./ScoreRing";
import { scoreCaption, scoreHeadline } from "./scoreCopy";

type Score = { key: string; label: string; value: number | null; explanation: string };

// Corner emblem per score — a white glyph on a soft badge. Decorative (the copy carries meaning).
const EMBLEM: Record<string, ReactNode> = {
  self_consumption: (
    <>
      <path d="M3.5 11 L11 5 L18.5 11" />
      <path d="M5.5 10 V18.5 H16.5 V10" />
      <circle cx="18.5" cy="5.2" r="2.1" fill="currentColor" stroke="none" />
      <path d="M18.5 1.4 V2.4 M22.3 5.2 H21.3 M21.2 2.5 l-.7 .7" />
    </>
  ),
  co2: (
    <>
      <path d="M12 21 V11.5" />
      <path d="M12 12.5 C7.5 12.5 5.5 9.5 5.5 5.8 C10 5.8 12 8.5 12 12.5 Z" fill="currentColor" stroke="none" />
      <path d="M12.4 13.6 C16.5 13.6 18.4 10.8 18.4 7.4 C14.6 7.4 12.4 9.8 12.4 13.6 Z" fill="currentColor" stroke="none" />
    </>
  ),
  best_price: (
    <>
      <path d="M20.4 3.6 H12.6 L3.6 12.6 L11.4 20.4 L20.4 11.4 Z" />
      <circle cx="16.4" cy="7.6" r="1.5" fill="currentColor" stroke="none" />
    </>
  ),
};

function Emblem({ kind }: { kind: string }) {
  return (
    <span className="sc-badge" aria-hidden="true">
      <svg
        viewBox="0 0 24 24" width="22" height="22" fill="none"
        stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round"
      >
        {EMBLEM[kind] ?? EMBLEM.self_consumption}
      </svg>
    </span>
  );
}

// A soft layered-hills silhouette that hugs the bottom edge of the card.
function Hills() {
  return (
    <svg className="sc-hills" viewBox="0 0 320 70" preserveAspectRatio="none" aria-hidden="true">
      <path className="sc-hill sc-hill-3" d="M0 40 Q70 24 150 36 T320 32 V70 H0 Z" />
      <path className="sc-hill sc-hill-2" d="M0 52 Q90 36 180 48 T320 46 V70 H0 Z" />
      <path className="sc-hill sc-hill-1" d="M0 60 Q100 50 200 58 T320 56 V70 H0 Z" />
    </svg>
  );
}

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
      className={`score-card-home score-${band}`}
      data-testid={`score-card-${score.key}`}
      aria-label={spoken}
      onClick={onOpen}
    >
      <ScoreRing value={score.value} label={score.label} size={104} testId={`ring-${score.key}`} />
      <span className="sc-copy">
        {headline && <span className="sc-headline">{headline}</span>}
        {caption && <span className="sc-caption">{caption}</span>}
      </span>
      <Hills />
      <Emblem kind={score.key} />
    </button>
  );
}
