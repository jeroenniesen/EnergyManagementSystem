// B-33 / #85 slice 1: disclosure that shows a 1–2 sentence "waarom" for the current
// battery action (laden / vasthouden / ontladen) after one tik. Hidden by default.
// B-74 / #84 slice 2: same disclosure also renders the structured reason fields so web,
// logs and diagnostics tell one story from the /api/battery-plan reason object.
import { DecisionReasonDetails } from "./DecisionReasonDetails";
import {
  formatBatteryActionWhy,
  isSlice1BatteryAction,
  slice1ActionLabel,
  type DecisionReason,
  type Slice1BatteryAction,
} from "./decisionWhy";

export function BatteryActionWhy({
  currentAction,
  reason,
  dryRun,
}: {
  currentAction: string | null | undefined;
  reason: DecisionReason | null | undefined;
  dryRun: boolean;
}) {
  if (!reason) return null;

  const slice1 = currentAction != null && isSlice1BatteryAction(currentAction);
  const waarom = slice1
    ? formatBatteryActionWhy(reason, currentAction, { dryRun })
    : null;
  const actionLabel = slice1
    ? slice1ActionLabel(currentAction as Slice1BatteryAction)
    : currentAction
      ? currentAction.replace(/_/g, " ")
      : "plan";

  return (
    <section
      className="battery-action-why"
      data-testid="battery-action-why"
      data-action={currentAction ?? "unknown"}
      data-dry-run={dryRun ? "true" : "false"}
    >
      <div className="battery-action-why-row">
        <span className="battery-action-why-label" data-testid="battery-action-label">
          Nu: {actionLabel}
        </span>
        <details className="battery-action-why-details" data-testid="battery-action-why-details">
          <summary
            className="battery-action-why-toggle"
            data-testid="battery-action-why-toggle"
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
