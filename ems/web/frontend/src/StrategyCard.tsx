// The headline control: pick how the battery is run. Auto follows the season; Summer fills from
// the panels and runs the night on the battery; Winter charges cheap and discharges the peaks.
import { useRef } from "react";

import { Icon, type IconName } from "./icons";

export type Strategy = {
  mode: string; // auto | summer | winter (the user's choice)
  active: string; // summer | winter (what's actually running)
  auto: boolean;
  summary: string;
  reason?: string;
  grid_topup: boolean;
  max_topup_price: number;
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
}: {
  strategy: Strategy | null;
  onChange: (mode: string) => void;
  onSetGridTopup: (on: boolean) => void;
  onTune: () => void;
}) {
  const refs = useRef<(HTMLButtonElement | null)[]>([]);
  if (!strategy) return null;

  const idx = Math.max(0, OPTIONS.findIndex((o) => o.key === strategy.mode));
  // Arrow keys move through the segmented control like a native radio group.
  function onKeyDown(e: React.KeyboardEvent) {
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
      {strategy.auto && strategy.reason && (
        <p className="strategy-why" data-testid="strategy-why">
          {strategy.reason}
        </p>
      )}

      <div className="strategy-tune">
        {strategy.active === "summer" && (
          <label className="switch-row">
            <button
              type="button"
              role="switch"
              aria-checked={strategy.grid_topup}
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
    </section>
  );
}
