import { useEffect, useRef, useState } from "react";

import { type Battery, BatteryChips } from "./BatteryChips";
import { type EnergyStoryData, EnergyStory } from "./EnergyStory";
import { OverrideCard } from "./Override";
import { Settings } from "./Settings";
import { type Strategy, StrategyCard } from "./StrategyCard";
import { SystemView } from "./System";
import { applyTheme, readStoredTheme, storeTheme, type Theme } from "./theme";

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

type Decision = {
  intent: string | null;
  desired_mode: string | null;
  applied: boolean;
  outcome: string;
  reason: string;
  plan_reason?: string | null;
};

type ChargeNeed = {
  current_soc_pct: number;
  target_soc_pct: number;
  deficit_kwh: number;
  on_track: boolean;
  reason: string;
};

type AlertItem = { key: string; severity: string; message: string };
type AlertsResp = { data_quality: string; alerts: AlertItem[] };

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

function ChargeTarget({ n }: { n: ChargeNeed }) {
  return (
    <section className="charge-need" data-testid="charge-need">
      <div className="override-head">
        <span className="metric-label">Tonight&apos;s charge target</span>
        <span
          className={`badge ${n.on_track ? "badge-live" : "badge-amber"}`}
          data-testid="charge-need-status"
        >
          {n.on_track ? "on track" : `need ${n.deficit_kwh.toFixed(1)} kWh`}
        </span>
      </div>
      <div
        className="charge-bar"
        role="img"
        aria-label={`Battery at ${n.current_soc_pct.toFixed(0)}%, target ${n.target_soc_pct.toFixed(
          0,
        )}%`}
      >
        <div className="charge-bar-fill" style={{ width: `${n.current_soc_pct}%` }} />
        <div
          className="charge-bar-target"
          // Clamp so the 2px marker stays visible inside the clipped bar at a 100% target.
          style={{ left: `min(${n.target_soc_pct}%, calc(100% - 2px))` }}
          title={`target ${n.target_soc_pct.toFixed(0)}%`}
        />
      </div>
      <p className="plan-reason" data-testid="charge-need-reason">
        {n.reason}
      </p>
    </section>
  );
}


export function App() {
  const [status, setStatus] = useState<Status | null>(null);
  const [series, setSeries] = useState<Series | null>(null);
  const [freshness, setFreshness] = useState<FreshnessMap | null>(null);
  const [story, setStory] = useState<EnergyStoryData | null>(null);
  const [storyWindow, setStoryWindow] = useState<"past" | "next">("next");
  const [strategy, setStrategy] = useState<Strategy | null>(null);
  const [battery, setBattery] = useState<Battery | null>(null);
  const [decision, setDecision] = useState<Decision | null>(null);
  const [alertsData, setAlertsData] = useState<AlertsResp | null>(null);
  const [chargeNeed, setChargeNeed] = useState<ChargeNeed | null>(null);
  const [savings, setSavings] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [view, setView] = useState<"dashboard" | "settings" | "system">("dashboard");
  // Seed from the localStorage cache so the first paint matches the saved theme (no flash);
  // the fetch below reconciles with the server's canonical value.
  const [theme, setTheme] = useState<Theme>(readStoredTheme);
  // While a strategy write is in flight, suppress the background poll's strategy update so the
  // optimistic selection doesn't flicker back to the old value mid-request.
  const strategyPending = useRef(false);

  useEffect(() => {
    let alive = true;
    fetch("/api/settings")
      .then((r) => (r.ok ? r.json() : null))
      .then((b) => {
        if (alive && b?.values?.["ui.theme"]) setTheme(b.values["ui.theme"] as Theme);
      })
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, []);
  // Keep <html data-theme> in sync and cache the choice for the next load's first paint.
  useEffect(() => {
    storeTheme(theme);
    return applyTheme(theme);
  }, [theme]);

  useEffect(() => {
    // Only poll the dashboard endpoints while the dashboard is visible — Settings has no need
    // for them and System runs its own poll. Avoids wasted load and a cross-tab error banner.
    if (view !== "dashboard") return;
    let alive = true;
    async function getJson(url: string) {
      const r = await fetch(url);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      return r.json();
    }
    // Each card updates as its own endpoint resolves — a slow/flaky one (e.g. the battery) never
    // blocks or blanks the rest. Liveness hinges on /api/status: only its failure shows the banner.
    function fill<T>(url: string, apply: (v: T) => void) {
      getJson(url).then((v) => { if (alive) apply(v); }).catch(() => {});
    }
    function poll() {
      getJson("/api/status")
        .then((v) => { if (alive) { setStatus(v); setError(null); } })
        .catch((e) => { if (alive) setError(String(e)); });
      fill("/api/series?limit=50", setSeries);
      fill("/api/freshness", setFreshness);
      fill(`/api/energy-story?window=${storyWindow}`, setStory);
      fill("/api/strategy", (v: Strategy) => { if (!strategyPending.current) setStrategy(v); });
      fill("/api/battery", setBattery);
      fill("/api/decision", setDecision);
      fill("/api/alerts", setAlertsData);
      fill("/api/savings", (v: { today_eur?: number }) => setSavings(v?.today_eur ?? null));
      fill("/api/charge-need", (v: ChargeNeed) => setChargeNeed(v ?? null));
    }
    poll();
    const id = setInterval(poll, POLL_MS);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, [view, storyWindow]);

  // Save a strategy setting live: apply an optimistic patch (instant, self-consistent card), POST
  // it, then reconcile from the server — reverting if the write fails. The pending guard stops the
  // 5 s poll clobbering the change mid-flight.
  async function patchStrategy(body: Record<string, unknown>, optimistic: Partial<Strategy>) {
    const prev = strategy;
    strategyPending.current = true;
    setStrategy((s) => (s ? { ...s, ...optimistic } : s));
    try {
      const res = await fetch("/api/settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const r = await fetch("/api/strategy");
      if (r.ok) setStrategy(await r.json());
      else setStrategy(prev);
    } catch {
      setStrategy(prev); // revert on any failure
    } finally {
      strategyPending.current = false;
    }
  }

  function setStrategyMode(mode: string) {
    // A forced mode runs that season; auto keeps the current season until the refetch confirms.
    const optimistic: Partial<Strategy> = { mode, auto: mode === "auto" };
    if (mode !== "auto") optimistic.active = mode;
    return patchStrategy({ "strategy.mode": mode }, optimistic);
  }

  function setGridTopup(on: boolean) {
    return patchStrategy({ "strategy.summer_grid_topup": on }, { grid_topup: on });
  }

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
            aria-current={view === "dashboard" ? "page" : undefined}
          >
            Dashboard
          </button>
          <button
            className={`nav-btn${view === "settings" ? " nav-active" : ""}`}
            onClick={() => setView("settings")}
            data-testid="nav-settings"
            aria-current={view === "settings" ? "page" : undefined}
          >
            Settings
          </button>
          <button
            className={`nav-btn${view === "system" ? " nav-active" : ""}`}
            onClick={() => setView("system")}
            data-testid="nav-system"
            aria-current={view === "system" ? "page" : undefined}
          >
            System
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

      {view === "dashboard" && error && (
        <div className="error" data-testid="error">Cannot reach EMS API: {error}</div>
      )}

      {view === "settings" && (
        <Settings onSaved={(v) => setTheme((v["ui.theme"] as Theme) ?? "auto")} />
      )}

      {view === "system" && <SystemView />}

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

      {view === "dashboard" && strategy && (
        <StrategyCard
          strategy={strategy}
          onChange={setStrategyMode}
          onSetGridTopup={setGridTopup}
          onTune={() => setView("settings")}
        />
      )}

      {view === "dashboard" && <BatteryChips battery={battery} />}

      {view === "dashboard" && (
        <EnergyStory story={story} window={storyWindow} onWindow={setStoryWindow} />
      )}

      {view === "dashboard" && chargeNeed && <ChargeTarget n={chargeNeed} />}

      {view === "dashboard" && status && <OverrideCard />}

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
