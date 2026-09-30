// B-74 / #84 slice 2: render the structured /api/battery-plan `reason` object as factual
// detail rows (chosen window, rejected alternative, benefit, risk, safety, gates).
// Same object as diagnostics.decision_reason and the export-package manifest — no parallel copy.
import type { DecisionReason } from "./decisionWhy";
import { eur } from "./format";

function clockRange(start: string | null | undefined, end: string | null | undefined): string | null {
  if (!start && !end) return null;
  const fmt = (iso: string) => {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso;
    return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  };
  if (start && end) return `${fmt(start)}–${fmt(end)}`;
  return fmt(start || end || "");
}

function gateChips(gates: DecisionReason["gates"]): string[] {
  const chips: string[] = [];
  if (gates.validator_code) chips.push(`validator: ${gates.validator_code}`);
  if (gates.failsafe) chips.push("failsafe");
  if (gates.dwell) chips.push("dwell");
  if (gates.cap_reached) chips.push("cap reached");
  if (gates.unconfirmed) chips.push("unconfirmed");
  return chips;
}

/** Structured reason detail — facts only from the DecisionReason object. */
export function DecisionReasonDetails({
  reason,
}: {
  reason: DecisionReason | null | undefined;
}) {
  if (!reason) return null;
  const chosen = reason.chosen_window;
  const alt = reason.rejected_alternative;
  const benefit = reason.expected_benefit;
  const risk = reason.risk;
  const safety = reason.safety_constraint;
  const chips = gateChips(reason.gates);
  const windowClock = chosen ? clockRange(chosen.start, chosen.end) : null;
  const priceHint =
    chosen?.eur_per_kwh_min != null && Number.isFinite(chosen.eur_per_kwh_min)
      ? `${eur(chosen.eur_per_kwh_min)}/kWh`
      : null;

  return (
    <dl className="decision-reason-details" data-testid="decision-reason-details">
      {reason.summary && (
        <div className="decision-reason-row" data-testid="decision-reason-summary">
          <dt>Summary</dt>
          <dd>{reason.summary}</dd>
        </div>
      )}
      {chosen && (
        <div className="decision-reason-row" data-testid="decision-reason-chosen">
          <dt>Chosen window</dt>
          <dd>
            {chosen.label || chosen.intent || "—"}
            {windowClock ? ` · ${windowClock}` : ""}
            {priceHint ? ` · from ${priceHint}` : ""}
          </dd>
        </div>
      )}
      {alt && (
        <div className="decision-reason-row" data-testid="decision-reason-rejected">
          <dt>Rejected alternative</dt>
          <dd>{alt.reason}</dd>
        </div>
      )}
      {benefit && (
        <div className="decision-reason-row" data-testid="decision-reason-benefit">
          <dt>Expected benefit</dt>
          <dd>
            {typeof benefit.eur === "number" && Number.isFinite(benefit.eur)
              ? `≈ ${eur(benefit.eur)}`
              : benefit.summary}
          </dd>
        </div>
      )}
      {risk && (
        <div className="decision-reason-row" data-testid="decision-reason-risk">
          <dt>Risk</dt>
          <dd>{risk.summary}</dd>
        </div>
      )}
      <div className="decision-reason-row" data-testid="decision-reason-safety">
        <dt>Safety</dt>
        <dd>
          {safety.action === "paused"
            ? safety.message
              ? `${safety.message}${safety.code ? ` (${safety.code})` : ""}`
              : safety.code || "Paused safely"
            : "Proceed — no safety hold"}
        </dd>
      </div>
      {chips.length > 0 && (
        <div className="decision-reason-row" data-testid="decision-reason-gates">
          <dt>Gates</dt>
          <dd className="decision-reason-gates">
            {chips.map((c) => (
              <span key={c} className="decision-reason-chip">
                {c}
              </span>
            ))}
          </dd>
        </div>
      )}
    </dl>
  );
}
