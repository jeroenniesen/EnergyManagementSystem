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

export function App() {
  const [status, setStatus] = useState<Status | null>(null);
  const [series, setSeries] = useState<Series | null>(null);
  const [freshness, setFreshness] = useState<FreshnessMap | null>(null);
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
        const [s, ser, fr] = await Promise.all([
          getJson("/api/status"),
          getJson("/api/series?limit=50"),
          getJson("/api/freshness"),
        ]);
        if (!alive) return;
        setStatus(s);
        setSeries(ser);
        setFreshness(fr);
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
