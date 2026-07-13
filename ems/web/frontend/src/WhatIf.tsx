// "What if?" scenario simulator (BACKLOG B-73) + the counterfactual savings header line (B-69).
// Sits on the Insights tab near FinanceSection (see Insights.tsx): a read-only "laboratory" for a
// handful of preset planner/battery tweaks, replayed against the SAME engine (ems/replay.py, B-77)
// the planner itself is graded against. Never touches real settings — POST /api/whatif is a pure
// simulation (see its "simulation": true field + ems/web/routes/whatif.py's module docstring), so
// this panel is clearly badged and never offers to "apply" anything.
import { useEffect, useState } from "react";

type ScenarioTotals = { cost_eur: number | null; import_kwh: number; export_kwh: number };

type Counterfactual = {
  window: { start: string; end: string; days_requested: number } | null;
  days_used: number;
  days_skipped: number;
  scenarios: Record<string, ScenarioTotals>;
  deltas: { planner_vs_no_battery: number | null; planner_vs_auto: number | null };
  note: string;
};

type PerDay = {
  date: string;
  baseline_eur: number | null;
  variant_eur: number | null;
  delta_eur: number | null;
};

type WhatIfResult = {
  simulation: true;
  days: number;
  days_used: number;
  days_skipped: number;
  overrides: Record<string, unknown>;
  baseline: { cost_eur: number | null };
  variant: { cost_eur: number | null };
  delta_eur: number | null;
  per_day: PerDay[];
  note: string;
};

type Preset = { key: string; label: string; overrides: Record<string, unknown> };

// Four presets from the backlog card (BACKLOG B-73), each mapped onto ONE allow-listed knob
// (ems/web/routes/whatif.py's WHATIF_ALLOWED_KEYS) so every chip is a single, legible change.
const PRESETS: Preset[] = [
  {
    key: "cautious-forecast", label: "More cautious forecast (60%)",
    overrides: { "planner.solar_confidence": 60 },
  },
  {
    key: "bigger-reserve", label: "Bigger reserve (30%)",
    overrides: { "battery.min_reserve_soc": 30 },
  },
  {
    key: "negative-prices", label: "Charge on negative prices",
    overrides: { "planner.negative_price_soak": true },
  },
  {
    key: "post-2027-export", label: "Post-2027 export model",
    overrides: { "prices.export_price_model": "spot_minus_tax" },
  },
];

const DAY_OPTIONS = [7, 14, 30] as const;

const eur = (v: number) => `${v < 0 ? "−" : ""}€${Math.abs(v).toFixed(2)}`;

function verdict(preset: Preset, delta: number | null, daysUsed: number): string {
  if (delta == null || daysUsed === 0) {
    return `Not enough recorded history yet to simulate "${preset.label}".`;
  }
  const window = `over the last ${daysUsed} measured day${daysUsed === 1 ? "" : "s"}`;
  if (delta > 0.005) return `${preset.label} would have saved ≈ ${eur(delta)} ${window}.`;
  if (delta < -0.005) return `${preset.label} would have cost ≈ ${eur(Math.abs(delta))} more ${window}.`;
  return `${preset.label} would have made almost no difference ${window}.`;
}

export function WhatIf() {
  const [counterfactual, setCounterfactual] = useState<Counterfactual | null>(null);
  const [days, setDays] = useState<(typeof DAY_OPTIONS)[number]>(14);
  const [activePreset, setActivePreset] = useState<Preset | null>(null);
  const [result, setResult] = useState<WhatIfResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(false);

  // Best-effort, like the other Insights sub-panels (FinanceSection, WeekDigest): the counterfactual
  // header is a bonus fact, never something the rest of the panel waits on or errors over.
  useEffect(() => {
    let alive = true;
    fetch("/api/counterfactual?days=14")
      .then((r) => (r.ok ? r.json() : null))
      .then((v: Counterfactual | null) => alive && setCounterfactual(v))
      .catch(() => alive && setCounterfactual(null));
    return () => {
      alive = false;
    };
  }, []);

  const run = (preset: Preset, runDays: number) => {
    setActivePreset(preset);
    setResult(null);
    setError(false);
    setLoading(true);
    fetch("/api/whatif", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ overrides: preset.overrides, days: runDays }),
    })
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      .then((v: WhatIfResult) => setResult(v))
      .catch(() => setError(true))
      .finally(() => setLoading(false));
  };

  const cf = counterfactual;
  const cfHeader =
    cf &&
    cf.days_used > 0 &&
    cf.deltas.planner_vs_no_battery != null &&
    cf.deltas.planner_vs_auto != null
      ? `Over the last ${cf.days_used} days your setup beat no-battery by ` +
        `${eur(cf.deltas.planner_vs_no_battery)} and vendor-auto by ${eur(cf.deltas.planner_vs_auto)}.`
      : null;

  return (
    <section className="whatif" data-testid="whatif-panel" aria-label="What if scenario simulator">
      <div className="whatif-head">
        <h3 className="card-title flow-title">What if?</h3>
        <span className="whatif-badge" data-testid="whatif-badge">
          simulation — nothing is changed
        </span>
      </div>

      {cfHeader && (
        <p className="whatif-counterfactual" data-testid="whatif-counterfactual">
          {cfHeader}
        </p>
      )}

      <div className="whatif-days" role="group" aria-label="Simulation window">
        {DAY_OPTIONS.map((d) => (
          <button
            key={d}
            type="button"
            className={`period-btn${days === d ? " period-active" : ""}`}
            data-testid={`whatif-days-${d}`}
            aria-pressed={days === d}
            onClick={() => {
              setDays(d);
              if (activePreset) run(activePreset, d);
            }}
          >
            {d}d
          </button>
        ))}
      </div>

      <div className="whatif-presets" role="group" aria-label="Preset scenarios">
        {PRESETS.map((p) => (
          <button
            key={p.key}
            type="button"
            className={`whatif-chip${activePreset?.key === p.key ? " whatif-chip-active" : ""}`}
            data-testid={`whatif-preset-${p.key}`}
            onClick={() => run(p, days)}
          >
            {p.label}
          </button>
        ))}
      </div>

      {loading && (
        <p className="dist-msg" data-testid="whatif-loading">
          Replaying history — this can take a moment…
        </p>
      )}
      {error && !loading && (
        <p className="fin-caveat" data-testid="whatif-error">
          Couldn't run that simulation right now — try again in a moment.
        </p>
      )}

      {result && !loading && !error && activePreset && (
        <div className="whatif-result" data-testid="whatif-result">
          <p className="whatif-verdict" data-testid="whatif-verdict">
            {verdict(activePreset, result.delta_eur, result.days_used)}
          </p>
          <div className="fin-tiles">
            <div className="fin-tile" data-testid="whatif-baseline">
              <div className="fin-val">
                {result.baseline.cost_eur == null ? "—" : eur(result.baseline.cost_eur)}
              </div>
              <div className="fin-name">current settings</div>
            </div>
            <div className="fin-tile" data-testid="whatif-variant">
              <div className="fin-val">
                {result.variant.cost_eur == null ? "—" : eur(result.variant.cost_eur)}
              </div>
              <div className="fin-name">with this change</div>
            </div>
          </div>
          {result.per_day.length > 0 && (
            <details className="chart-table" data-testid="whatif-per-day">
              <summary>Day by day</summary>
              <div className="chart-table-scroll">
                <table>
                  <thead>
                    <tr>
                      <th>Day</th>
                      <th>Current</th>
                      <th>With change</th>
                      <th>Δ</th>
                    </tr>
                  </thead>
                  <tbody>
                    {result.per_day.map((d) => (
                      <tr key={d.date}>
                        <td>{d.date}</td>
                        <td>{d.baseline_eur == null ? "—" : eur(d.baseline_eur)}</td>
                        <td>{d.variant_eur == null ? "—" : eur(d.variant_eur)}</td>
                        <td>{d.delta_eur == null ? "—" : eur(d.delta_eur)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </details>
          )}
        </div>
      )}
    </section>
  );
}
