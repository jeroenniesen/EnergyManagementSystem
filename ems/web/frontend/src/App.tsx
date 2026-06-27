import { useEffect, useState } from "react";

import { Settings } from "./Settings";

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
type PlanSlot = { start: string; intent: string; reason: string };
type Plan = {
  created_at: string | null;
  current_intent: string | null;
  current_reason: string | null;
  slots: PlanSlot[];
};

type Decision = {
  intent: string | null;
  desired_mode: string | null;
  applied: boolean;
  outcome: string;
  reason: string;
  plan_reason?: string | null;
};

type AlertItem = { key: string; severity: string; message: string };
type AlertsResp = { data_quality: string; alerts: AlertItem[] };

type Battery = {
  current_mode: string | null;
  capabilities: {
    services: string[];
    p1_paired: boolean;
    max_charge_w: number;
    max_discharge_w: number;
  } | null;
};

const INTENT_LABEL: Record<string, string> = {
  allow_self_consumption: "Self-consume",
  grid_charge_to_target: "Charge",
  hold_reserve: "Hold",
  discharge_for_load: "Discharge",
};

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

function PlanTimeline({ plan }: { plan: Plan }) {
  const slots = plan.slots.slice(0, 96);
  const currentLabel = plan.current_intent
    ? INTENT_LABEL[plan.current_intent] ?? plan.current_intent
    : "—";
  return (
    <section className="prices" data-testid="plan">
      <div className="prices-head">
        <span className="metric-label">Plan — next 24h</span>
        <span className="price-now" data-testid="current-intent">
          {currentLabel}
        </span>
      </div>
      <div
        className="timeline"
        role="img"
        aria-label={`Battery plan timeline for the next 24 hours; current action: ${currentLabel}`}
      >
        {slots.map((s, i) => (
          <span
            key={i}
            className={`seg seg-${s.intent}`}
            title={`${s.start.substring(11, 16)} — ${s.reason}`}
          />
        ))}
      </div>
      {plan.current_reason && <p className="plan-reason">{plan.current_reason}</p>}
    </section>
  );
}

function PriceCurve({ prices }: { prices: Prices }) {
  const slots = prices.slots.slice(0, 96); // show ~today
  const max = Math.max(0.01, ...slots.map((s) => s.eur_per_kwh));
  const min = Math.min(...slots.map((s) => s.eur_per_kwh));
  const cur = prices.current_eur_per_kwh;
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
      <div
        className="bars"
        role="img"
        aria-label={`Electricity price curve; current ${
          cur != null ? `€${cur.toFixed(2)}` : "—"
        }/kWh, range €${min.toFixed(2)}–€${max.toFixed(2)}`}
      >
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
      <div
        className="bars"
        role="img"
        aria-label={`Solar forecast (P50); ${
          forecast.today_kwh_p50 != null ? forecast.today_kwh_p50.toFixed(1) : "—"
        } kWh expected today, peak ~${Math.round(max)} W`}
      >
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
  const [plan, setPlan] = useState<Plan | null>(null);
  const [battery, setBattery] = useState<Battery | null>(null);
  const [decision, setDecision] = useState<Decision | null>(null);
  const [alertsData, setAlertsData] = useState<AlertsResp | null>(null);
  const [savings, setSavings] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [view, setView] = useState<"dashboard" | "settings">("dashboard");

  useEffect(() => {
    let alive = true;
    async function getJson(url: string) {
      const r = await fetch(url);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      return r.json();
    }
    async function poll() {
      try {
        const [s, ser, fr, pr, fc, pl, bat, dec, al, sv] = await Promise.all([
          getJson("/api/status"),
          getJson("/api/series?limit=50"),
          getJson("/api/freshness"),
          getJson("/api/prices"),
          getJson("/api/forecast"),
          getJson("/api/plan"),
          getJson("/api/battery"),
          getJson("/api/decision"),
          getJson("/api/alerts"),
          getJson("/api/savings"),
        ]);
        if (!alive) return;
        setStatus(s);
        setSeries(ser);
        setFreshness(fr);
        setPrices(pr);
        setForecast(fc);
        setPlan(pl);
        setBattery(bat);
        setDecision(dec);
        setAlertsData(al);
        setSavings(sv?.today_eur ?? null);
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
        {status && (
          <span className="badge badge-muted" data-testid="data-source">
            {status.dev_mode === "live" ? "live data" : "simulated data"}
          </span>
        )}
        {alertsData && (
          <span className={`badge badge-dq dq-${alertsData.data_quality}`} data-testid="data-quality">
            {alertsData.data_quality}
          </span>
        )}
        <nav className="nav" aria-label="Views">
          <button
            className={`nav-btn${view === "dashboard" ? " nav-active" : ""}`}
            onClick={() => setView("dashboard")}
            data-testid="nav-dashboard"
            aria-current={view === "dashboard"}
          >
            Dashboard
          </button>
          <button
            className={`nav-btn${view === "settings" ? " nav-active" : ""}`}
            onClick={() => setView("settings")}
            data-testid="nav-settings"
            aria-current={view === "settings"}
          >
            Settings
          </button>
        </nav>
      </header>

      {alertsData && alertsData.alerts.length > 0 && (
        <section className="alerts" data-testid="alerts">
          {alertsData.alerts.map((a) => (
            <span key={a.key} className={`chip alert-${a.severity}`}>
              {a.message}
            </span>
          ))}
        </section>
      )}

      {error && <div className="error" data-testid="error">Cannot reach EMS API: {error}</div>}

      {view === "settings" && <Settings />}

      {view === "dashboard" && status && (
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
          {battery?.current_mode && (
            <Metric
              label="Battery mode"
              value={battery.current_mode}
              hint={battery.capabilities?.p1_paired ? "P1 paired" : "P1 not paired"}
            />
          )}
          {savings != null && (
            <Metric label="Est. savings today" value={`€${savings.toFixed(2)}`} hint="arbitrage" />
          )}
        </section>
      )}

      {view === "dashboard" && decision && decision.outcome !== "unconfigured" && (
        <section className="decision" data-testid="decision">
          <span className="metric-label">Controller</span>
          <p className="decision-line">
            <span className="decision-outcome">{decision.outcome}</span>
            {" — "}
            {decision.reason}
          </p>
          {decision.plan_reason && <p className="plan-reason">plan: {decision.plan_reason}</p>}
        </section>
      )}

      {view === "dashboard" && plan && plan.slots.length > 0 && <PlanTimeline plan={plan} />}

      {view === "dashboard" && prices && prices.slots.length > 0 && <PriceCurve prices={prices} />}

      {view === "dashboard" && forecast && forecast.slots.length > 0 && (
        <ForecastCurve forecast={forecast} />
      )}

      {view === "dashboard" && freshness && Object.keys(freshness).length > 0 && (
        <section className="freshness" data-testid="freshness">
          <span className="freshness-title">Signal freshness</span>
          {Object.entries(freshness).map(([sig, st]) => (
            <span key={sig} className={`chip chip-${st}`} data-signal={sig}>
              {sig}: {st}
            </span>
          ))}
        </section>
      )}

      {view === "dashboard" && !status && !error && <div className="loading">Loading…</div>}

      <footer className="footer" data-testid="series-count">
        history samples: {series ? series.raw.length : 0}
      </footer>
    </div>
  );
}
