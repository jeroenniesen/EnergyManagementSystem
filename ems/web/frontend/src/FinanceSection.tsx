// What the window cost and saved (spec 2026-07-03 B / B-36 #80): measured grid cost, battery
// wear, and the € saved vs the no-battery baseline — from /api/finance (recorded samples + stored
// prices, never the plan). Honest by construction: totals are absent until price history exists,
// days without figures are chart gaps (never €0), and a coverage caveat names price/sample holes.
import { useEffect, useState } from "react";

import { apiFetch } from "./auth";
import { eur } from "./format";

type DayFin = {
  day: string;
  has_data: boolean;
  price_coverage: number;
  sample_coverage?: number;
  grid_cost_eur: number | null;
  battery_cost_eur: number | null;
  baseline_cost_eur: number | null;
  saved_eur: number | null;
  solar_self_use_eur?: number | null;
  avoided_expensive_eur?: number | null;
  battery_contribution_eur?: number | null;
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
    solar_self_use_eur?: number | null;
    avoided_expensive_eur?: number | null;
    battery_contribution_eur?: number | null;
    days_with_prices: number;
    days_with_data: number;
    days_with_coverage_gap?: number;
    days_without_data?: number;
  };
};

const BW = 720;
const BH = 150;
const BPAD = { l: 46, r: 10, t: 12, b: 20 };

function SavedBars({ days }: { days: DayFin[] }) {
  const [hover, setHover] = useState<number | null>(null);
  // Gaps stay gaps: never treat missing saved_eur as €0 when scaling the chart.
  const known = days.map((d) => d.saved_eur).filter((v): v is number => v != null);
  const maxAbs = Math.max(0.5, ...known.map(Math.abs));
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
          const isGap = v == null;
          return (
            <g key={d.day}
              onMouseEnter={() => setHover(i)}>
              <rect x={BPAD.l + i * bw} y={BPAD.t} width={bw} height={plotH} fill="transparent" />
              {isGap ? (
                // Explicit gap marker — not a €0 bar.
                <line
                  x1={cx + colW / 2} x2={cx + colW / 2}
                  y1={y0 - 6} y2={y0 + 6}
                  stroke="var(--muted)" strokeWidth={1.5} strokeDasharray="2 2"
                  data-testid={`fin-bar-gap-${d.day}`}
                />
              ) : h > 0.5 ? (
                <rect x={cx} y={v! >= 0 ? y0 - h : y0 + 2} width={colW}
                  height={Math.max(1, v! >= 0 ? h : h - 2)} rx={2}
                  fill={v! >= 0 ? "var(--green)" : "var(--amber)"} />
              ) : null}
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
              {days[hover].saved_eur == null ? "no data" : eur(days[hover].saved_eur!)}
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

function coverageCaveat(fin: FinResp): string | null {
  const t = fin.totals;
  const gapDays = t.days_with_coverage_gap ?? 0;
  const missingDays = t.days_without_data ?? 0;
  const partialPrices = t.days_with_prices > 0 && t.days_with_prices < t.days_with_data;
  // Also catch per-day partial coverage the totals counter may already include.
  const dayGap = fin.days.some(
    (d) =>
      d.has_data &&
      (d.price_coverage < 1 - 1e-9 || (d.sample_coverage != null && d.sample_coverage < 1 - 1e-9)),
  );

  if (t.saved_eur == null) return null;
  if (gapDays > 0 || dayGap || partialPrices) {
    if (partialPrices && !dayGap) {
      return (
        `Prices are known for ${t.days_with_prices} of ${t.days_with_data} recorded days — ` +
        "the € figures cover that part."
      );
    }
    const bits: string[] = [];
    if (gapDays > 0 || dayGap) {
      bits.push(
        gapDays > 0
          ? `${gapDays} day${gapDays === 1 ? "" : "s"} with incomplete prices or meter samples`
          : "some days have incomplete prices or meter samples",
      );
    }
    if (missingDays > 0) {
      bits.push(`${missingDays} day${missingDays === 1 ? "" : "s"} with no meter data (shown as gaps)`);
    }
    if (bits.length === 0) {
      return "Some days have incomplete coverage — those € figures cover only the measured part.";
    }
    return `Coverage gap: ${bits.join("; ")}. Missing days are gaps, not €0.`;
  }
  if (missingDays > 0) {
    return (
      `${missingDays} day${missingDays === 1 ? "" : "s"} had no meter data — ` +
      "shown as gaps, not €0."
    );
  }
  return null;
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
  const multiDay = fin.days.length > 1;
  const caveat = coverageCaveat(fin);
  const showBreakdown =
    t.saved_eur != null &&
    (period === "month" || period === "week" || period === "year") &&
    (t.solar_self_use_eur != null ||
      t.avoided_expensive_eur != null ||
      t.battery_contribution_eur != null);

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
              <div className="fin-name">saved — measured, after wear</div>
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
          <p className="fin-caveat" data-testid="fin-baseline">
            Compared with the same home without a battery.
          </p>
          {showBreakdown && (
            <div className="fin-tiles fin-breakdown" data-testid="fin-breakdown">
              <div className="fin-tile" data-testid="fin-solar-self-use">
                <div className="fin-val">{eur(t.solar_self_use_eur ?? 0)}</div>
                <div className="fin-name">your own solar used</div>
              </div>
              <div className="fin-tile" data-testid="fin-avoided-expensive">
                <div className="fin-val">{eur(t.avoided_expensive_eur ?? 0)}</div>
                <div className="fin-name">not bought at expensive hours</div>
              </div>
              <div className="fin-tile" data-testid="fin-battery-contribution">
                <div className="fin-val">{eur(t.battery_contribution_eur ?? 0)}</div>
                <div className="fin-name">battery contribution</div>
              </div>
            </div>
          )}
          {caveat && (
            <p className="fin-caveat" data-testid="fin-caveat">{caveat}</p>
          )}
          {multiDay && <SavedBars days={fin.days} />}
        </>
      )}
    </div>
  );
}
