import { useEffect, useState, type FormEvent } from "react";
import { apiFetch } from "./auth";
import { eur } from "./format";
import "./SavingsAdvice.css";

type Evidence = { available: boolean; reason: string };
type Advice = {
  reserve: Evidence & { target_soc_pct?: number; hard_floor_soc_pct?: number;
    extra_buffer_kwh?: number; extra_buffer_cost_eur?: number; capacity_limited?: boolean };
  calibration: Evidence & { usable_kwh?: number; round_trip_efficiency?: number; standby_reason: string };
  load_accuracy: Evidence & { hours_scored: number; baseline_mae_w?: number; enhanced_mae_w?: number };
  consumption: { kind: string; reason: string; action: string; extra_kwh_per_day: number;
    estimated_extra_eur_per_day: number | null }[];
  solar: { n_daytime_slots: number; actual_solar_kwh: number; forecast_p50_kwh: number } | null;
};
type WindowResult = Evidence & { start?: string; end?: string; cost_eur?: number; saving_vs_now_eur?: number };

function tomorrowEvening(): string {
  const d = new Date();
  d.setDate(d.getDate() + 1);
  d.setHours(18, 0, 0, 0);
  return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
}

export function SavingsAdvice() {
  const [advice, setAdvice] = useState<Advice | null>(null);
  const [error, setError] = useState(false);
  const [duration, setDuration] = useState("60");
  const [energy, setEnergy] = useState("1");
  const [deadline, setDeadline] = useState(tomorrowEvening);
  const [result, setResult] = useState<WindowResult | null>(null);
  const [runError, setRunError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    apiFetch("/api/bill-advice", { signal: controller.signal })
      .then(r => { if (!r.ok) throw new Error(); return r.json(); })
      .then(setAdvice)
      .catch(e => { if (e.name !== "AbortError") setError(true); });
    return () => controller.abort();
  }, []);

  async function schedule(e: FormEvent) {
    e.preventDefault();
    setBusy(true); setRunError(null); setResult(null);
    try {
      const r = await apiFetch("/api/advisor/appliance", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ duration_minutes: Number(duration), energy_kwh: Number(energy),
          deadline: new Date(deadline).toISOString() }),
      });
      if (!r.ok) throw new Error(r.status === 422 ? "Check the duration, energy and deadline (within 48 hours)." :
        "We couldn’t calculate a window. Please try again.");
      setResult(await r.json());
    } catch (e) { setRunError(e instanceof Error ? e.message : "Couldn’t calculate a window."); }
    finally { setBusy(false); }
  }
  const solar = advice?.solar;
  const lowSolar = solar && solar.n_daytime_slots >= 48 && solar.forecast_p50_kwh > 0 &&
    solar.actual_solar_kwh < solar.forecast_p50_kwh * .8;
  return (
    <section className="whatif savings-advice" data-testid="bill-advice" aria-labelledby="bill-advice-title">
      <h3 id="bill-advice-title" className="card-title">Ways to lower your bill</h3>
      <p className="advisor-hint">Advice only — these recommendations never change settings or operate devices.</p>
      {!advice && !error && <p role="status">Checking household evidence…</p>}
      {error && <p data-testid="bill-advice-error" role="status">We couldn’t load the advice. Please try again later.</p>}
      {advice && (
        <div className="savings-evidence">
          <article>
            <h4>Battery operating target</h4>
            {advice.reserve.available && <p><strong>{advice.reserve.target_soc_pct}%</strong> target,
              above your {advice.reserve.hard_floor_soc_pct}% hard floor.
              {" "}The extra {advice.reserve.extra_buffer_kwh?.toFixed(2)} kWh buffer has an estimated
              purchase cost of {eur(advice.reserve.extra_buffer_cost_eur ?? 0)}.</p>}
            <p>{advice.reserve.reason}</p>
            {advice.reserve.capacity_limited && <p>The projected demand exceeds available battery capacity.</p>}
          </article>
          <article data-testid="battery-calibration">
            <h4>Battery calibration</h4>
            {advice.calibration.available && <p>Estimated usable capacity:
              {" "}<strong>{advice.calibration.usable_kwh} kWh</strong>. Round-trip efficiency:
              {" "}<strong>{((advice.calibration.round_trip_efficiency ?? 0) * 100).toFixed(1)}%</strong>.</p>}
            <p>{advice.calibration.reason}</p>
            <p className="advisor-hint">{advice.calibration.standby_reason}</p>
          </article>
          <article>
            <h4>Demand prediction</h4>
            {advice.load_accuracy.available && <p>Across {advice.load_accuracy.hours_scored} held-out hours,
              average error was {advice.load_accuracy.enhanced_mae_w} W with weekday/weekend learning,
              versus {advice.load_accuracy.baseline_mae_w} W with the existing hourly average.</p>}
            <p>{advice.load_accuracy.reason}</p>
          </article>
          <article>
            <h4>Consumption checks</h4>
            {advice.consumption.length === 0 && <p>No sustained increase established from sufficient comparable days.</p>}
            {advice.consumption.map(item => <div key={item.kind}>
              <p>{item.reason} About {item.extra_kwh_per_day.toFixed(2)} extra kWh/day
                {item.estimated_extra_eur_per_day != null && ` (≈ ${eur(item.estimated_extra_eur_per_day)}/day)`}.</p>
              <p className="advisor-hint">{item.action}</p>
            </div>)}
            {lowSolar && <p>Recorded solar was more than 20% below its forecast. Check shading,
              meter readings and forecast calibration before assuming a panel fault.</p>}
          </article>
        </div>
      )}
      <details className="savings-appliance" open>
        <summary>Find a cheaper appliance window</summary>
        <p>For a dishwasher, washing machine or another flexible load. Estimates include the value
          of solar you could otherwise export; battery response is excluded.</p>
        <form className="savings-form" onSubmit={schedule}>
          <label>Run duration (minutes)<input type="number" min="15" max="1440" step="15" required
            value={duration} onChange={e => { setDuration(e.target.value); setResult(null); }} /></label>
          <label>Energy per run (kWh)<input type="number" min="0.01" max="50" step="0.01" required
            value={energy} onChange={e => { setEnergy(e.target.value); setResult(null); }} /></label>
          <label>Finish by<input type="datetime-local" required value={deadline}
            onChange={e => { setDeadline(e.target.value); setResult(null); }} /></label>
          <button className="whatif-chip" type="submit" disabled={busy}>
            {busy ? "Calculating…" : "Find a cheaper window"}</button>
        </form>
        {runError && <p role="alert">{runError}</p>}
        {result && <div data-testid="appliance-result" role="status">
          {result.available && <p><strong>{new Date(result.start!).toLocaleString()} – {new Date(result.end!).toLocaleTimeString([], {hour:"2-digit",minute:"2-digit"})}</strong>.
            {" "}Estimated cost {eur(result.cost_eur!)}; saving {eur(result.saving_vs_now_eur!)} against the earliest available window.</p>}
          <p>{result.reason}</p>
        </div>}
      </details>
    </section>
  );
}
