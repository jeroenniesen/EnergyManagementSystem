/**
 * B-100 — calm framing for negative / uncertain measured savings.
 *
 * Early-day measuring and a modest €-negative figure are expected honesty, not failure.
 * Never paint them with alarm styling; use quiet Dutch copy (B-98) + a muted semantic icon.
 * Positive measured savings stay a plain € amount (B-03b measured-vs-estimated honesty).
 */
import type { SavedToday } from "./EnergyStory";
import { eur } from "./format";
import type { IconName } from "./icons";
import { HOME_TILE } from "./labels";

export type SavingsFrameTone = "positive" | "none" | "measuring" | "unavailable";

export type SavingsFrame = {
  /** Visible tile value (calm phrase or € amount). */
  value: string;
  /** Hover / a11y title — keeps the measured figure when reframed. */
  title: string;
  tone: SavingsFrameTone;
  /** Semantic icon only for calm measuring / no-benefit states (not alarm). */
  icon: IconName | null;
  /** Open finance detail only when a measured figure exists. */
  openFinance: boolean;
};

/** Measured savings with no benefit yet (≤ €0) — quiet honesty, not failure. */
export function isCalmNoBenefit(eurVal: number): boolean {
  return eurVal <= 0;
}

export function frameSavedToday(savedToday: SavedToday | null): SavingsFrame {
  if (savedToday == null) {
    return {
      value: "—",
      title: HOME_TILE.savings.unavailable,
      tone: "unavailable",
      icon: null,
      openFinance: false,
    };
  }

  if (savedToday.status === "measuring") {
    return {
      value: HOME_TILE.savings.measuringShort,
      title: HOME_TILE.savings.measuring,
      tone: "measuring",
      icon: "euro",
      openFinance: false,
    };
  }

  const amount = savedToday.eur;
  if (isCalmNoBenefit(amount)) {
    const measured = eur(amount);
    return {
      value: HOME_TILE.savings.noBenefit,
      title: `${HOME_TILE.savings.title}: ${measured}`,
      tone: "none",
      icon: "euro",
      openFinance: true,
    };
  }

  return {
    value: eur(amount),
    title: HOME_TILE.savings.title,
    tone: "positive",
    // B-102: quiet € glyph on every savings tile (semantic recognition, not calm-only).
    icon: "euro",
    openFinance: true,
  };
}
