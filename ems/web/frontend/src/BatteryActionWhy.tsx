// B-33 / #85 slice 1: disclosure that shows a 1–2 sentence "waarom" for the current
// battery action (laden / vasthouden / ontladen) after one tap. Hidden by default.
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
  if (!currentAction || !isSlice1BatteryAction(currentAction)) return null;
  const text = formatBatteryActionWhy(reason, currentAction, { dryRun });
  if (!text) return null;
  const action = currentAction as Slice1BatteryAction;

  return (
    <section
      className="battery-action-why"
      data-testid="battery-action-why"
      data-action={action}
      data-dry-run={dryRun ? "true" : "false"}
    >
      <div className="battery-action-why-row">
        <span className="battery-action-why-label" data-testid="battery-action-label">
          Nu: {slice1ActionLabel(action)}
        </span>
        <details className="battery-action-why-details" data-testid="battery-action-why-details">
          <summary
            className="battery-action-why-toggle"
            data-testid="battery-action-why-toggle"
          >
            Waarom?
          </summary>
          <p className="battery-action-why-text" data-testid="battery-action-why-text">
            {text}
          </p>
        </details>
      </div>
    </section>
  );
}
