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

// The ring's inner name badge has very little width (see .ring-name) — "Self-consumption" there
// wrapped and got auto-hyphenated into "Self-consump-tion" (a production screenshot finding).
// Rather than fight the layout, the RING gets a short alias for the keys that need one; every
// other surface (aria text, headline, explanation, the Insights card's own label) still uses the
// full label the backend sends (ems/scores.py) — unaffected callers just get their label back.
const RING_LABEL: Record<string, string> = {
  self_consumption: "Self-use",
};

export function ringLabel(key: string, label: string): string {
  return RING_LABEL[key] ?? label;
}

// Day-just-starting: instead of repeating "The day's just starting" on every pill (the section
// summary already says it once), each pill previews WHAT its score measures — so the empty row
// teaches the three scores rather than echoing one line three times.
const EARLY_PREVIEW: Record<string, string> = {
  self_consumption: "Solar you use yourself",
  co2: "How clean your energy is",
  best_price: "How well you time prices",
};
export function earlyPreview(key: string): string {
  return EARLY_PREVIEW[key] ?? "Builds through the day";
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

// Splits a score's explanation into its first sentence (always shown, as the Insights card's ONE
// detail line) + the remainder (behind a "More" disclosure) — the SAME idiom Settings' field help
// uses (splitHelp/FieldHelp there): split on the first sentence-ending punctuation that's followed
// by whitespace, so a mid-number decimal ("60%", "€0.13/kWh") never triggers an early split.
// Single-sentence explanations (most of them) come back with an empty `rest` — no disclosure
// renders, and the one line reads as the whole thought, not a truncation.
export function splitExplanation(explanation: string): { first: string; rest: string } {
  const m = explanation.match(/^([\s\S]*?[.!?])(\s+)([\s\S]+)$/);
  if (!m) return { first: explanation.trim(), rest: "" };
  return { first: m[1].trim(), rest: m[3].trim() };
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
