import { useEffect, useState } from "react";

type Status = {
  dry_run: boolean;
  dev_mode: string;
  soc_pct: number;
  grid_power_w: number;
  solar_power_w: number;
  battery_power_w: number;
  house_load_w: number;
  non_ev_load_w: number;
};

type Series = { raw: Record<string, number>[]; derived: Record<string, number>[] };
type FreshnessMap = Record<string, string>;
type PriceSlot = { start: string; eur_per_kwh: number };
type Prices = { currency: string; current_eur_per_kwh: number | null; slots: PriceSlot[] };
type ForecastSlot = { start: string; p10_w: number; p50_w: number; p90_w: number };
type Forecast = { today_kwh_p50: number | null; slots: ForecastSlot[] };

const POLL_MS = 5000;

function fmtW(w: number): string {
  return Math.abs(w) >= 1000 ? `${(w / 1000).toFixed(2)} kW` : `${Math.round(w)} W`;
}

function Metric({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="metric">
      <span className="metric-label">{label}</span>
      <span className="metric-value">{value}</span>
      {hint && <span className="metric-hint">{hint}</span>}
    </div>
  );
}

function PriceCurve({ prices }: { prices: Prices }) {
  const slots = prices.slots.slice(0, 96); // show ~today
  const max = Math.max(0.01, ...slots.map((s) => s.eur_per_kwh));
  return (
    <section className="prices" data-testid="prices">
      <div className="prices-head">
        <span className="metric-label">Electricity price</span>
        <span className="price-now" data-testid="price-now">
          {prices.current_eur_per_kwh != null
            ? `€${prices.current_eur_per_kwh.toFixed(2)} / kWh`
            : "—"}
        </span>
      </div>
      <div className="bars" aria-hidden="true">
        {slots.map((s, i) => (
          <span
            key={i}
            className="bar"
            style={{ height: `${(s.eur_per_kwh / max) * 100}%` }}
            title={`${s.start.substring(11, 16)} — €${s.eur_per_kwh.toFixed(2)}`}
          />
        ))}
      </div>
    </section>
  );
}

function ForecastCurve({ forecast }: { forecast: Forecast }) {
  const slots = forecast.slots.slice(0, 96);
  const max = Math.max(1, ...slots.map((s) => s.p90_w));
  return (
    <section className="prices" data-testid="forecast">
      <div className="prices-head">
        <span className="metric-label">Solar forecast (P50)</span>
        <span className="price-now" data-testid="forecast-today">
          {forecast.today_kwh_p50 != null ? `${forecast.today_kwh_p50.toFixed(1)} kWh today` : "—"}
        </span>
      </div>
      <div className="bars" aria-hidden="true">
        {slots.map((s, i) => (
          <span
            key={i}
            className="bar bar-solar"
            style={{ height: `${(s.p50_w / max) * 100}%` }}
            title={`${s.start.substring(11, 16)} — ${Math.round(s.p50_w)} W (P50)`}
          />
        ))}
      </div>
    </section>
  );
}

export function App() {
  const [status, setStatus] = useState<Status | null>(null);
  const [series, setSeries] = useState<Series | null>(null);
  const [freshness, setFreshness] = useState<FreshnessMap | null>(null);
  const [prices, setPrices] = useState<Prices | null>(null);
  const [forecast, setForecast] = useState<Forecast | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    async function getJson(url: string) {
      const r = await fetch(url);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      return r.json();
    }
    async function poll() {
      try {
        const [s, ser, fr, pr, fc] = await Promise.all([
          getJson("/api/status"),
          getJson("/api/series?limit=50"),
          getJson("/api/freshness"),
          getJson("/api/prices"),
          getJson("/api/forecast"),
        ]);
        if (!alive) return;
        setStatus(s);
        setSeries(ser);
        setFreshness(fr);
        setPrices(pr);
        setForecast(fc);
        setError(null);
      } catch (e) {
        if (alive) setError(String(e));
      }
    }
    poll();
    const id = setInterval(poll, POLL_MS);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, []);

  return (
    <div className="app">
      <header className="topbar">
        <h1>Smart Energy Manager</h1>
        {status && (
          <span
            className={`badge ${status.dry_run ? "badge-dryrun" : "badge-live"}`}
            data-testid="run-mode-badge"
          >
            {status.dry_run ? "DRY-RUN" : "LIVE"}
          </span>
        )}
        {status && <span className="badge badge-muted">source: {status.dev_mode}</span>}
      </header>

      {error && <div className="error" data-testid="error">Cannot reach EMS API: {error}</div>}

      {status && (
        <section className="grid" data-testid="status-grid">
          <Metric label="State of charge" value={`${status.soc_pct.toFixed(0)} %`} />
          <Metric
            label="House load"
            value={fmtW(status.house_load_w)}
            hint="reconstructed: grid + solar + battery"
          />
          <Metric label="Grid" value={fmtW(status.grid_power_w)} hint={status.grid_power_w >= 0 ? "import" : "export"} />
          <Metric label="Solar" value={fmtW(status.solar_power_w)} hint="production" />
          <Metric
            label="Battery"
            value={fmtW(status.battery_power_w)}
            hint={status.battery_power_w >= 0 ? "discharging" : "charging"}
          />
          <Metric label="Non-EV load" value={fmtW(status.non_ev_load_w)} hint="excludes car" />
        </section>
      )}

      {prices && prices.slots.length > 0 && <PriceCurve prices={prices} />}

      {forecast && forecast.slots.length > 0 && <ForecastCurve forecast={forecast} />}

      {freshness && Object.keys(freshness).length > 0 && (
        <section className="freshness" data-testid="freshness">
          <span className="freshness-title">Signal freshness</span>
          {Object.entries(freshness).map(([sig, st]) => (
            <span key={sig} className={`chip chip-${st}`} data-signal={sig}>
              {sig}: {st}
            </span>
          ))}
        </section>
      )}

      {!status && !error && <div className="loading">Loading…</div>}

      <footer className="footer" data-testid="series-count">
        history samples: {series ? series.raw.length : 0}
      </footer>
    </div>
  );
}
