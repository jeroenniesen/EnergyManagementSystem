// Advisory-only "best time to charge the car" (docs/v2-ev-control.md: EV *control* is out of
// scope for v2 — this card only ever suggests a window; the EMS never commands the car). Off
// unless `ev.advice_enabled` is on; the endpoint returns { advice: null } in that case (or when
// there isn't enough price/forecast data), and the card renders nothing.
import { useEffect, useState } from "react";

type EvAdvice = {
  start: string;
  end: string;
  est_cost_eur: number;
  solar_share_pct: number;
  slots: number;
  reason: string;
} | null;

// A best-effort background refresh — the recommended window barely moves within a few minutes,
// so there's no need to poll as eagerly as the live dashboard tiles.
const REFRESH_MS = 5 * 60 * 1000;

function fmtTime(iso: string): string {
  return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

export function EVAdvice() {
  const [advice, setAdvice] = useState<EvAdvice>(null);

  useEffect(() => {
    let alive = true;
    function load() {
      fetch("/api/advisor/ev-charge")
        .then((r) => (r.ok ? r.json() : null))
        .then((b) => {
          if (alive) setAdvice(b?.advice ?? null);
        })
        .catch(() => {
          /* best-effort — a failed poll just keeps the last known advice (or stays hidden) */
        });
    }
    load();
    const id = setInterval(load, REFRESH_MS);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, []);

  if (!advice) return null; // hidden until enabled AND a real window can be recommended
  return (
    <section className="ev-advice" data-testid="ev-advice">
      <span className="metric-label">🚗 Best time to charge the car</span>
      <p className="ev-advice-window" data-testid="ev-advice-window">
        {fmtTime(advice.start)}–{fmtTime(advice.end)}
        {" "}(≈ €{advice.est_cost_eur.toFixed(2)}, {advice.solar_share_pct}% solar)
      </p>
      <p className="plan-reason">{advice.reason}</p>
      <p className="ev-advice-footer">Advice only — the EMS never controls the car.</p>
    </section>
  );
}
