// B-96: hero synthesis is one plain-language sentence + the act-line.
// Planner jargon (break-even, raw self-consumption:…, EV-load operator prose) stays under
// Waarom? / DecisionReasonDetails — never in the hero body.
// B-98: Dutch-first calm fallback via labels.ts.
// B-98 fix: raw English story/reason text stays out of the hero the same way jargon does —
// prefer Dutch HOME_HERO on-track / behind / fallback phrasing.

import { HOME_HERO } from "./labels";

const FALLBACK = HOME_HERO.fallback;

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

/** Strip trailing punctuation the same way pickPlain always has. */
function stripTrail(text: string): string {
  return text.replace(/[.\s]+$/, "").trim();
}

/**
 * Map known English API / story phrases to Dutch hero lines.
 * Unmapped English returns null so the caller falls through to HOME_HERO.fallback.
 */
export function localizeHeroPhrase(candidate: string | null | undefined): string | null {
  if (!candidate) return null;
  const trimmed = stripTrail(candidate);
  if (!trimmed || isPlannerJargon(trimmed)) return null;

  const lower = trimmed.toLowerCase();

  // Already the Dutch calm fallback (with or without period).
  if (lower === stripTrail(HOME_HERO.fallback).toLowerCase()) {
    return stripTrail(HOME_HERO.fallback);
  }
  if (lower === stripTrail(HOME_HERO.onTrack).toLowerCase()) {
    return stripTrail(HOME_HERO.onTrack);
  }
  if (lower === stripTrail(HOME_HERO.behind).toLowerCase()) {
    return stripTrail(HOME_HERO.behind);
  }
  if (lower === stripTrail(HOME_HERO.runningTonight).toLowerCase()) {
    return stripTrail(HOME_HERO.runningTonight);
  }

  // English on-track / behind story messages from energy-story.
  if (/^on track\b/i.test(trimmed)) return stripTrail(HOME_HERO.onTrack);
  if (/^short of\b/i.test(trimmed)) return stripTrail(HOME_HERO.behind);

  // Plain battery-plan current_reason (EN) → Dutch fallback.
  if (/^battery is following the current plan\b/i.test(trimmed)) {
    return stripTrail(HOME_HERO.fallback);
  }
  if (/^running the house on your battery\b/i.test(trimmed)) {
    return stripTrail(HOME_HERO.runningTonight);
  }

  // Score-summary English ("A brilliant day…") and any other EN API copy stay out.
  if (
    /\b(the|your|for|with|from|battery|track|short|following|learning|safety|fresh|solar|tonight|target|house|running|watching|needs|attention|brilliant|solid|energy|day|keep|clean)\b/i.test(
      trimmed,
    )
  ) {
    return null;
  }

  // Allow unexpected already-Dutch copy through.
  return trimmed;
}

/**
 * One human sentence for the merged hero.
 * Preference: Dutch on-track → Dutch live reason → Dutch score → calm fallback.
 * Never joins fragments with middots — that rebuilt the pre-B-96 text wall.
 * Never surfaces raw English API story/reason text in the fold (B-98 fix).
 */
export function buildHeroSynthesis(opts: {
  currentReason?: string | null;
  onTrackMessage?: string | null;
  scoreSummary?: string | null;
}): string {
  return (
    localizeHeroPhrase(opts.onTrackMessage) ??
    localizeHeroPhrase(opts.currentReason) ??
    localizeHeroPhrase(opts.scoreSummary) ??
    FALLBACK
  );
}
