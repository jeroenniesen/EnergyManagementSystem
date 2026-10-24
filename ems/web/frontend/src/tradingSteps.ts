/** F-30 / #339 — trading arm steps (advice → dry-run review → live arm). */

export type TradingStep = {
  id: "advice" | "dry_run" | "arm";
  ok: boolean;
  label: string;
  missing: string | null;
};

/** Accessible status text for a step (not glyph-only). */
export function tradingStepStatusText(step: TradingStep): string {
  const state = step.ok ? "voltooid" : "ontbreekt";
  const detail = step.missing ? ` (${step.missing})` : "";
  return `${step.label}: ${state}${detail}`;
}

export function tradingDryRunStepOk(steps: TradingStep[] | undefined): boolean {
  const dry = steps?.find((s) => s.id === "dry_run");
  return dry?.ok === true;
}
