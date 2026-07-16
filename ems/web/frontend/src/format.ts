// Shared numeric-formatting helpers used across the money-facing panels (FinanceSection,
// WeekDigest, WhatIf) so the sign/symbol convention (€ with a real minus sign, never "-€") lives
// in exactly one place.

/** Format a euro amount with the currency symbol and a real minus sign for negatives (never a
 * bare hyphen), e.g. `eur(-1.2)` → "−€1.20". */
export function eur(v: number): string {
  return `${v < 0 ? "−" : ""}€${Math.abs(v).toFixed(2)}`;
}
