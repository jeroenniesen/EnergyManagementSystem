// B-96: hero synthesis is one plain-language sentence + the act-line.
// Planner jargon (break-even, raw self-consumption:…, EV-load operator prose) stays under
// Waarom? / DecisionReasonDetails — never in the hero body.

const FALLBACK = "Battery is following the current plan.";

/** Operator / planner-internal copy that must not appear in the hero synthesis. */
export function isPlannerJargon(text: string): boolean {
  const t = text.trim();
  if (!t) return false;
  if (/\bbreak-even\b/i.test(t)) return true;
  if (/\bself-consumption\s*:/i.test(t)) return true;
  if (/\bdischarge\s*:/i.test(t)) return true;
  if (/\bno-trade\s*:/i.test(t)) return true;
  if (/\bEV load expected\b/i.test(t)) return true;
  // Price-comparison operator form: "€0.45/kWh > …" / "< break-even"
  if (/€\s*[\d.]+\s*\/\s*kWh\s*[<>]/i.test(t)) return true;
  return false;
}

function pickPlain(candidate: string | null | undefined): string | null {
  if (!candidate) return null;
  const trimmed = candidate.replace(/[.\s]+$/, "").trim();
  if (!trimmed || isPlannerJargon(trimmed)) return null;
  return trimmed;
}

/**
 * One human sentence for the merged hero.
 * Preference: on-track story → non-jargon live reason → day-score summary → calm fallback.
 * Never joins fragments with middots — that rebuilt the pre-B-96 text wall.
 */
export function buildHeroSynthesis(opts: {
  currentReason?: string | null;
  onTrackMessage?: string | null;
  scoreSummary?: string | null;
}): string {
  return (
    pickPlain(opts.onTrackMessage) ??
    pickPlain(opts.currentReason) ??
    pickPlain(opts.scoreSummary) ??
    FALLBACK
  );
}
