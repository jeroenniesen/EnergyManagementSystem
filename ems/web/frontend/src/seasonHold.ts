// F-39 / #345: read-only season-hold copy. Other seasons stay out of this chip.

export type StrategyHysteresis = {
  committed: string | null;
  pending: string | null;
  count: number;
  days: number;
};

/** 'Winter vasthouden 2/3' while auto is counting toward a switch away from winter. */
export function winterHoldLabel(h: StrategyHysteresis | null | undefined): string | null {
  if (!h || h.committed !== "winter" || h.pending == null || h.pending === h.committed) {
    return null;
  }
  return `Winter vasthouden ${h.count}/${h.days}`;
}

export function seasonHoldActions(
  mode: string,
  hysteresis: StrategyHysteresis | null | undefined,
): { holdLabel: string | null; forceSummer: boolean; backToAuto: boolean } {
  const holdLabel = mode === "auto" ? winterHoldLabel(hysteresis) : null;
  return {
    holdLabel,
    forceSummer: holdLabel != null,
    backToAuto: mode === "summer",
  };
}
