// What the window cost and saved (spec 2026-07-03 B): measured grid cost, battery wear, and the
// € saved vs the no-battery baseline — from /api/finance (recorded samples + stored prices,
// never the plan). Honest by construction: totals are absent until price history exists, and a
// partial-coverage caveat says exactly how much is covered.
import { useEffect, useState } from "react";

import { apiFetch } from "./auth";
import { eur } from "./format";

type DayFin = {
  day: string;
  has_data: boolean;
  price_coverage: number;
  grid_cost_eur: number | null;
  battery_cost_eur: number | null;
  baseline_cost_eur: number | null;
  saved_eur: number | null;
  grid_import_kwh: number;
  grid_export_kwh: number;
};

type FinResp = {
  period: string;
  label: string;
  partial: boolean;
  days: DayFin[];
  totals: {
    grid_cost_eur: number | null;
    battery_cost_eur: number | null;
    saved_eur: number | null;
    days_with_prices: number;
    days_with_data: number;
  };
};

const BW = 720;
const BH = 150;
const BPAD = { l: 46, r: 10, t: 12, b: 20 };

function SavedBars({ days }: { days: DayFin[] }) {
  const [hover, setHover] = useState<number | null>(null);
  const vals = days.map((d) => d.saved_eur ?? 0);
  const maxAbs = Math.max(0.5, ...vals.map(Math.abs));
  const plotW = BW - BPAD.l - BPAD.r;
  const plotH = BH - BPAD.t - BPAD.b;
  const y0 = BPAD.t + plotH / 2;
  const scale = plotH / 2 / maxAbs;
  const bw = plotW / days.length;
  const colW = Math.min(18, bw - 2);
  const label = (d: DayFin) =>
    new Date(`${d.day}T00:00:00`).toLocaleDateString([], { day: "numeric", month: "short" });

  return (
    <div className="behavior-wrap">
      <svg viewBox={`0 0 ${BW} ${BH}`} className="behavior-svg" role="img"
        aria-label="Money saved per day" data-testid="fin-bars"
        onMouseLeave={() => setHover(null)}>
        <line x1={BPAD.l} x2={BW - BPAD.r} y1={y0} y2={y0} stroke="var(--line)" strokeWidth={1.4} />
        <text x={BPAD.l - 6} y={BPAD.t + 4} textAnchor="end" className="behavior-tick">
          {eur(maxAbs)}
        </text>
        <text x={BPAD.l - 6} y={BH - BPAD.b} textAnchor="end" className="behavior-tick">
          {eur(-maxAbs)}
        </text>
        {days.map((d, i) => {
          const v = d.saved_eur;
          const cx = BPAD.l + i * bw + (bw - colW) / 2;
          const h = v == null ? 0 : Math.abs(v) * scale;
          return (
            <g key={d.day}
              onMouseEnter={() => setHover(d.has_data ? i : null)}>
              <rect x={BPAD.l + i * bw} y={BPAD.t} width={bw} height={plotH} fill="transparent" />
              {v != null && h > 0.5 && (
                <rect x={cx} y={v >= 0 ? y0 - h : y0 + 2} width={colW}
                  height={Math.max(1, v >= 0 ? h : h - 2)} rx={2}
                  fill={v >= 0 ? "var(--green)" : "var(--amber)"} />
              )}
              {(days.length <= 12 || i % 7 === 0) && (
                <text x={BPAD.l + i * bw + bw / 2} y={BH - 5} textAnchor="middle"
                  className="behavior-tick">
                  {label(d)}
                </text>
              )}
            </g>
          );
        })}
      </svg>
      {hover != null && days[hover] && (
        <div className="chart-tip" style={{ left: `${((BPAD.l + hover * bw + bw / 2) / BW) * 100}%` }}>
          <div className="chart-tip-title">{label(days[hover])}</div>
          <div className="chart-tip-row">Saved
            <span className="chart-tip-val">
              {days[hover].saved_eur == null ? "no price data" : eur(days[hover].saved_eur!)}
            </span>
          </div>
          {days[hover].grid_cost_eur != null && (
            <div className="chart-tip-row">Grid
              <span className="chart-tip-val">{eur(days[hover].grid_cost_eur!)}</span>
            </div>
          )}
          {days[hover].battery_cost_eur != null && (
            <div className="chart-tip-row">Battery wear
              <span className="chart-tip-val">{eur(days[hover].battery_cost_eur!)}</span>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export function FinanceSection({ period, anchor }: { period: string; anchor: string }) {
  const [fin, setFin] = useState<FinResp | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    let alive = true;
    setError(false);
    apiFetch(`/api/finance?period=${period}&date=${anchor}`)
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      .then((v: FinResp) => alive && setFin(v))
      .catch(() => alive && setError(true));
    return () => {
      alive = false;
    };
  }, [period, anchor]);

  if (error) {
    return (
      <div className="fin" data-testid="finance-section">
        <h3 className="card-title flow-title">What it cost &amp; saved</h3>
        <p className="fin-caveat" data-testid="fin-error">
          Money history could not be loaded. The energy scores above are still available.
        </p>
      </div>
    );
  }
  if (!fin) return null;
  const t = fin.totals;
  if (t.days_with_data === 0) return null;
  const soFar = fin.partial ? " (so far)" : "";
  const multiDay = fin.days.filter((d) => d.has_data).length > 1;
  const partialPrices = t.days_with_prices > 0 && t.days_with_prices < t.days_with_data;

  return (
    <div className="fin" data-testid="finance-section">
      <h3 className="card-title flow-title">What it cost &amp; saved{soFar}</h3>
      {t.saved_eur == null ? (
        <p className="fin-caveat" data-testid="fin-caveat">
          No price history recorded yet — money figures start appearing after the app has stored a
          day of prices alongside your meter data.
        </p>
      ) : (
        <>
          <div className="fin-tiles">
            <div className="fin-tile" data-testid="fin-saved">
              <div className={`fin-val${t.saved_eur >= 0 ? " fin-good" : ""}`}>{eur(t.saved_eur)}</div>
              <div className="fin-name">saved by the battery — measured, after wear</div>
            </div>
            <div className="fin-tile" data-testid="fin-grid">
              <div className="fin-val">{eur(t.grid_cost_eur ?? 0)}</div>
              <div className="fin-name">grid energy cost</div>
            </div>
            <div className="fin-tile" data-testid="fin-wear">
              <div className="fin-val">{eur(t.battery_cost_eur ?? 0)}</div>
              <div className="fin-name">battery wear</div>
            </div>
          </div>
          {partialPrices && (
            <p className="fin-caveat" data-testid="fin-caveat">
              Prices are known for {t.days_with_prices} of {t.days_with_data} recorded days —
              the € figures cover that part.
            </p>
          )}
          {multiDay && <SavedBars days={fin.days} />}
        </>
      )}
    </div>
  );
}
