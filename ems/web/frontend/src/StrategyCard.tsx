// The headline control: pick how the battery is run. Auto follows the season; Summer fills from
// the panels and runs the night on the battery; Winter charges cheap and discharges the peaks.
import { useRef } from "react";

import { Icon, type IconName } from "./icons";
import { batteryFloorFields, type Battery } from "./BatteryChips";
import { HOME_BATTERY_FLOOR, isBelowReserveSoc } from "./labels";
import { seasonHoldActions, type StrategyHysteresis } from "./seasonHold";

export type Strategy = {
  mode: string; // auto | summer | winter (the user's choice)
  active: string; // summer | winter (what's actually running)
  auto: boolean;
  summary: string;
  reason?: string;
  grid_topup: boolean;
  max_topup_price: number;
  hysteresis?: StrategyHysteresis | null;
};

const OPTIONS: { key: string; label: string; icon: IconName }[] = [
  { key: "auto", label: "Auto", icon: "auto" },
  { key: "summer", label: "Summer", icon: "solar" },
  { key: "winter", label: "Winter", icon: "winter" },
];

export function StrategyCard({
  strategy,
  onChange,
  onSetGridTopup,
  onTune,
  canOperate = true,
  battery = null,
  currentSocPct = null,
}: {
  strategy: Strategy | null;
  onChange: (mode: string) => void;
  onSetGridTopup: (on: boolean) => void;
  onTune: () => void;
  battery?: Battery | null;
  currentSocPct?: number | null;
  // Reader read-only mode (auth slice 2 web): the season switch + grid-topup toggle both write
  // via /api/settings, an OPERATE-tier endpoint — disabled (not hidden, so the current choice
  // stays visible) for a reader. Defaults true so every other caller is unaffected.
  canOperate?: boolean;
}) {
  const refs = useRef<(HTMLButtonElement | null)[]>([]);
  if (!strategy) return null;

  const floor = batteryFloorFields(battery);
  const belowReserve =
    floor != null && isBelowReserveSoc(currentSocPct, floor.reserve);

  const idx = Math.max(0, OPTIONS.findIndex((o) => o.key === strategy.mode));
  const hold = seasonHoldActions(strategy.mode, strategy.hysteresis);
  // Arrow keys move through the segmented control like a native radio group.
  function onKeyDown(e: React.KeyboardEvent) {
    if (!canOperate) return;
    const fwd = e.key === "ArrowRight" || e.key === "ArrowDown";
    const back = e.key === "ArrowLeft" || e.key === "ArrowUp";
    if (!fwd && !back) return;
    e.preventDefault();
    const next = (idx + (fwd ? 1 : -1) + OPTIONS.length) % OPTIONS.length;
    onChange(OPTIONS[next].key);
    refs.current[next]?.focus();
  }

  return (
    <section className={`strategy-card season-${strategy.active}`} data-testid="strategy-card">
      <div className="prices-head">
        <span className="metric-label">Strategy</span>
        {strategy.auto && (
          <span className="badge strategy-badge" data-testid="strategy-active">
            Auto · running {strategy.active}
          </span>
        )}
      </div>
      <div className="strategy-seg" role="radiogroup" aria-label="Energy strategy" onKeyDown={onKeyDown}>
        {OPTIONS.map((o, i) => {
          const selected = strategy.mode === o.key;
          return (
            <button
              key={o.key}
              ref={(el) => (refs.current[i] = el)}
              type="button"
              role="radio"
              aria-checked={selected}
              tabIndex={selected ? 0 : -1}
              disabled={!canOperate}
              className={`seg-btn${selected ? ` seg-active seg-${o.key}` : ""}`}
              data-testid={`strategy-${o.key}`}
              onClick={() => onChange(o.key)}
            >
              <Icon name={o.icon} className={`seg-icon seg-icon-${o.key}`} />
              {o.label}
            </button>
          );
        })}
      </div>
      <p className="strategy-summary" data-testid="strategy-summary">
        {strategy.summary}
      </p>
      {floor && (
        <p className="battery-floor-line" data-testid="strategy-battery-floor">
          {HOME_BATTERY_FLOOR.line(floor.effective, floor.reserve, floor.device)}
          {belowReserve && (
            <span data-testid="strategy-below-reserve">
              {" "}
              · {HOME_BATTERY_FLOOR.belowReserve}
            </span>
          )}
        </p>
      )}
      {strategy.auto && strategy.reason && (
        <p className="strategy-why" data-testid="strategy-why">
          {strategy.reason}
        </p>
      )}
      {(hold.holdLabel || hold.backToAuto) && (
        <div className="strategy-hold" role="group" aria-label="Seizoenswissel">
          {hold.holdLabel && (
            <span
              id="strategy-hold-chip"
              className="strategy-hold-chip"
              role="status"
              aria-live="polite"
              data-testid="strategy-hold-chip"
            >
              {hold.holdLabel}
            </span>
          )}
          <div className="strategy-hold-actions">
            {hold.forceSummer && (
              <button
                type="button"
                className="btn-primary strategy-hold-btn"
                data-testid="strategy-force-summer"
                aria-describedby="strategy-hold-chip"
                disabled={!canOperate}
                onClick={() => onChange("summer")}
              >
                Nu solar-first
              </button>
            )}
            {hold.backToAuto && (
              <button
                type="button"
                className="btn-ghost strategy-hold-btn"
                data-testid="strategy-back-auto"
                disabled={!canOperate}
                onClick={() => onChange("auto")}
              >
                Terug naar Auto
              </button>
            )}
          </div>
        </div>
      )}

      <div className="strategy-tune">
        {strategy.active === "summer" && (
          <label className="switch-row">
            <button
              type="button"
              role="switch"
              aria-checked={strategy.grid_topup}
              aria-label="Top up from the grid if the sun falls short"
              disabled={!canOperate}
              className={`switch${strategy.grid_topup ? " switch-on" : ""}`}
              data-testid="strategy-grid-topup"
              onClick={() => onSetGridTopup(!strategy.grid_topup)}
            >
              <span className="switch-knob" />
            </button>
            <span className="switch-label">Top up from the grid if the sun falls short</span>
          </label>
        )}
        <button
          type="button"
          className="strategy-more"
          data-testid="strategy-more"
          onClick={onTune}
        >
          Advanced settings →
        </button>
      </div>
      {!canOperate && (
        <p className="advisor-hint" data-testid="strategy-readonly-hint">
          Changing the strategy needs a &quot;user&quot; or &quot;admin&quot; account.
        </p>
      )}
    </section>
  );
}
