// The headline control: pick how the battery is run. Auto follows the season; Summer fills from
// the panels and runs the night on the battery; Winter charges cheap and discharges the peaks.

export type Strategy = {
  mode: string; // auto | summer | winter (the user's choice)
  active: string; // summer | winter (what's actually running)
  auto: boolean;
  summary: string;
  grid_topup: boolean;
  max_topup_price: number;
};

const OPTIONS = [
  { key: "auto", label: "Auto", icon: "◐" },
  { key: "summer", label: "Summer", icon: "☀" },
  { key: "winter", label: "Winter", icon: "❄" },
];

export function StrategyCard({
  strategy,
  onChange,
}: {
  strategy: Strategy | null;
  onChange: (mode: string) => void;
}) {
  if (!strategy) return null;
  return (
    <section className="strategy-card" data-testid="strategy-card">
      <div className="prices-head">
        <span className="metric-label">Strategy</span>
        {strategy.auto && (
          <span className="badge badge-muted" data-testid="strategy-active">
            Auto · running {strategy.active}
          </span>
        )}
      </div>
      <div className="seg" role="radiogroup" aria-label="Energy strategy">
        {OPTIONS.map((o) => (
          <button
            key={o.key}
            type="button"
            role="radio"
            aria-checked={strategy.mode === o.key}
            className={`seg-btn${strategy.mode === o.key ? " seg-active" : ""}`}
            data-testid={`strategy-${o.key}`}
            onClick={() => onChange(o.key)}
          >
            <span className="seg-icon" aria-hidden="true">
              {o.icon}
            </span>
            {o.label}
          </button>
        ))}
      </div>
      <p className="strategy-summary" data-testid="strategy-summary">
        {strategy.summary}
      </p>
    </section>
  );
}
