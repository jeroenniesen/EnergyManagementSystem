export type PlanDetailSlot = {
  start: string;
  intent: string;
  label: string;
  reason: string;
  eur_per_kwh: number | null;
  solar_w: number | null;
};
export type PlanDetailData = {
  current_intent: string | null;
  summary: string;
  slots: PlanDetailSlot[];
};

const LEGEND = [
  { intent: "grid_charge_to_target", label: "Charge" },
  { intent: "discharge_for_load", label: "Discharge" },
  { intent: "hold_reserve", label: "Hold" },
  { intent: "allow_self_consumption", label: "Self-consume" },
];

function hhmm(iso: string): string {
  return iso.substring(11, 16);
}

// One aligned view: price (cheap = short bar), the planned action band, and the solar forecast all
// share the SAME slot columns, so a charge band always sits under the cheap price bars.
export function PlanDetail({ detail }: { detail: PlanDetailData }) {
  const slots = detail.slots;
  if (!slots.length) return null;
  const prices = slots.map((s) => s.eur_per_kwh ?? 0);
  const maxP = Math.max(0.01, ...prices);
  const solars = slots.map((s) => s.solar_w ?? 0);
  const maxS = Math.max(1, ...solars);
  const everyN = Math.max(1, Math.round(slots.length / 6)); // ~6 time labels across the axis

  return (
    <section className="plandetail" data-testid="plan-detail">
      <div className="prices-head">
        <span className="metric-label">Next 24 hours — what the plan will do</span>
      </div>
      <p className="plan-summary" data-testid="plan-summary">
        {detail.summary}
      </p>

      <div className="track-label">Electricity price (cheap = short)</div>
      <div className="track track-price" role="img" aria-label="Electricity price over the next 24h">
        {slots.map((s, i) => (
          <span
            key={i}
            className="tbar tbar-price"
            style={{ height: `${((s.eur_per_kwh ?? 0) / maxP) * 100}%` }}
            title={`${hhmm(s.start)} · €${(s.eur_per_kwh ?? 0).toFixed(2)}/kWh · ${s.label}`}
          />
        ))}
      </div>

      <div className="track-label">Planned action</div>
      <div className="track track-plan" role="img" aria-label={`Planned actions: ${detail.summary}`}>
        {slots.map((s, i) => (
          <span
            key={i}
            className={`pseg seg-${s.intent}`}
            title={`${hhmm(s.start)} — ${s.reason}`}
          />
        ))}
      </div>

      <div className="track-label">Solar forecast</div>
      <div className="track track-solar" role="img" aria-label="Solar forecast over the next 24h">
        {slots.map((s, i) => (
          <span
            key={i}
            className="tbar tbar-solar"
            style={{ height: `${((s.solar_w ?? 0) / maxS) * 100}%` }}
            title={`${hhmm(s.start)} · ${Math.round(s.solar_w ?? 0)} W`}
          />
        ))}
      </div>

      <div className="time-axis" aria-hidden="true">
        {slots.map((s, i) => (
          <span key={i} className="tick">
            {i % everyN === 0 ? hhmm(s.start) : ""}
          </span>
        ))}
      </div>

      <div className="legend" data-testid="plan-legend">
        {LEGEND.map((l) => (
          <span key={l.intent} className="legend-item">
            <span className={`legend-swatch seg-${l.intent}`} />
            {l.label}
          </span>
        ))}
      </div>
    </section>
  );
}
