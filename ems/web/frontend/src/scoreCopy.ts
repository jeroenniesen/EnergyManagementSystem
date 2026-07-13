// The reflective layer of the score rings (Don Norman's third level): the number tells you "how
// much", this warm, band-aware copy tells you "how you're doing" — the bit that motivates. Kept
// separate from the presentational ScoreRing so the wording can evolve without touching the gauge.
import { type Band, scoreBand } from "./ScoreRing";

// A short status line under each ring — Oura-style ("Optimal" / "Pay attention"), but concrete to
// the metric and always honest. "low" copy nudges without scolding.
const CAPTIONS: Record<string, Record<Band, string>> = {
  self_consumption: {
    good: "Mostly your own sun",
    ok: "A good share self-made",
    low: "Room to use more solar",
    na: "No sun logged yet",
  },
  co2: {
    good: "Barely any fossil power",
    ok: "Cleaner than the grid",
    low: "Leaning on the grid",
    na: "Nothing logged yet",
  },
  best_price: {
    good: "Bought at the right times",
    ok: "Decent buying",
    low: "Bought at pricier hours",
    na: "No prices yet",
  },
};

export function scoreCaption(key: string, value: number | null): string | undefined {
  return CAPTIONS[key]?.[scoreBand(value)];
}

// A fuller sentence for the home score cards (the line above the caption) — plain-language,
// band-aware, and warm on a good day.
const HEADLINES: Record<string, Record<Band, string>> = {
  self_consumption: {
    good: "You're using the energy you generate.",
    ok: "A good share is your own energy.",
    low: "Most power came from the grid today.",
    na: "No energy recorded yet.",
  },
  co2: {
    good: "Low emissions today.",
    ok: "Cleaner than the average grid.",
    low: "Leaning on grid power today.",
    na: "Nothing recorded yet.",
  },
  best_price: {
    good: "Great timing! You bought at the best price.",
    ok: "Reasonable buying today.",
    low: "You bought during pricier hours.",
    na: "No price data yet.",
  },
};

export function scoreHeadline(key: string, value: number | null): string | undefined {
  return HEADLINES[key]?.[scoreBand(value)];
}

export type SummaryTone = "great" | "good" | "grow" | "neutral"; // neutral: day-just-starting, no verdict yet

type ScoreLike = { value: number | null };

// One reflective line for the whole day — the "how did we do, overall?" feeling that greets you.
// Celebrate a clean day; stay gently encouraging when there's headroom. Null when nothing's logged.
export function homeSummary(scores: ScoreLike[]): { tone: SummaryTone; text: string } | null {
  const vals = scores.map((s) => s.value).filter((v): v is number => v != null);
  if (vals.length === 0) return null;
  if (vals.every((v) => v >= 80)) return { tone: "great", text: "A brilliant day for clean energy" };
  if (vals.some((v) => v < 50)) return { tone: "grow", text: "A little room to do better today" };
  return { tone: "good", text: "A solid energy day — keep it up" };
}
