// B-33 / #85: disclosure that shows a 1–2 sentence "waarom" for the current battery action
// after one tap. Hidden by default.
// Slice 1: laden / vasthouden / zelfconsumptie / volle-snelheid.
// Slice 2: reserve, gepauzeerde staten, vermogensgrens-varianten.
// B-74 / #84 slice 2: same disclosure also renders the structured reason fields so web,
// logs and diagnostics tell one story from the /api/battery-plan reason object.
import { DecisionReasonDetails } from "./DecisionReasonDetails";
import {
  batteryActionLabel,
  formatBatteryActionWhy,
  type DecisionReason,
} from "./decisionWhy";

export function BatteryActionWhy({
  currentAction,
  reason,
  dryRun,
  overrideActive = false,
}: {
  currentAction: string | null | undefined;
  reason: DecisionReason | null | undefined;
  dryRun: boolean;
  overrideActive?: boolean;
}) {
  if (!reason) return null;

  const waarom =
    currentAction != null
      ? formatBatteryActionWhy(reason, currentAction, { dryRun, overrideActive })
      : formatBatteryActionWhy(reason, "paused", { dryRun, overrideActive });
  const actionLabel = batteryActionLabel(currentAction, reason);

  return (
    <section
      className="battery-action-why"
      data-testid="battery-action-why"
      data-action={currentAction ?? "unknown"}
      data-dry-run={dryRun ? "true" : "false"}
      aria-label={`Nu: ${actionLabel}`}
    >
      <div className="battery-action-why-row">
        <span className="battery-action-why-label" data-testid="battery-action-label">
          Nu: {actionLabel}
        </span>
        <details className="battery-action-why-details" data-testid="battery-action-why-details">
          <summary
            className="battery-action-why-toggle"
            data-testid="battery-action-why-toggle"
            aria-label={`Waarom ${actionLabel}?`}
          >
            Waarom?
          </summary>
          {waarom && (
            <p className="battery-action-why-text" data-testid="battery-action-why-text">
              {waarom}
            </p>
          )}
          <DecisionReasonDetails reason={reason} />
        </details>
      </div>
    </section>
  );
}
