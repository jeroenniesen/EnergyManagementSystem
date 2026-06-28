import { useEffect, useRef, useState } from "react";

import { type Battery, BatteryChips } from "./BatteryChips";
import { type EnergyStoryData, EnergyStory } from "./EnergyStory";
import { Icon, type IconName } from "./icons";
import {
  DATA_QUALITY,
  DATA_SOURCE,
  FRESHNESS_STATE,
  humanize,
  OUTCOME_LABEL,
  PHYSICAL_MODE,
  RUN_MODE,
  SIGNAL_NAME,
} from "./labels";
import { OverrideCard } from "./Override";
import { Settings } from "./Settings";
import { type Strategy, StrategyCard } from "./StrategyCard";
import { AuditView } from "./AuditView";
import { ChatPanel } from "./ChatPanel";
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

type FreshnessMap = Record<string, string>;

type Decision = {
  intent: string | null;
  desired_mode: string | null;
  applied: boolean;
  outcome: string;
  reason: string;
  plan_reason?: string | null;
  plan_reason_explained?: string | null;
  explanation_source?: string;
  car_charging?: boolean;
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

function Metric({
  label,
  value,
  hint,
  title,
  icon,
  accent,
  onClick,
  testId,
}: {
  label: string;
  value: string;
  hint?: string;
  title?: string;
  icon?: IconName;
  accent?: boolean;
  onClick?: () => void;
  testId?: string;
}) {
  const inner = (
    <>
      <span className="metric-label-row">
        {icon && <Icon name={icon} className="metric-icon" />}
        <span className="metric-label">{label}</span>
      </span>
      <span className="metric-value">{value}</span>
      {hint && <span className="metric-hint">{hint}</span>}
    </>
  );
  const cls = `metric${accent ? " metric-accent" : ""}${onClick ? " metric-clickable" : ""}`;
  if (onClick) {
    return (
      <button type="button" className={cls} title={title} onClick={onClick} data-testid={testId}>
        {inner}
      </button>
    );
  }
  return (
    <div className={cls} title={title} data-testid={testId}>
      {inner}
    </div>
  );
}

function Modal({
  title,
  onClose,
  children,
  testId,
}: {
  title: string;
  onClose: () => void;
  children: React.ReactNode;
  testId?: string;
}) {
  const closeRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    closeRef.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div
        className="modal"
        role="dialog"
        aria-modal="true"
        aria-label={title}
        onClick={(e) => e.stopPropagation()}
        data-testid={testId}
      >
        <div className="modal-head">
          <span className="metric-label">{title}</span>
          <button
            ref={closeRef}
            type="button"
            className="modal-close"
            onClick={onClose}
            aria-label="Close"
            data-testid="modal-close"
          >
            ×
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}

function SkeletonGrid() {
  // Shown for the brief moment before the first /api/status resolves (a live read can take a
  // couple of seconds). A shimmer placeholder reads as "loading" far better than a bare word.
  return (
    <section className="grid" data-testid="status-skeleton" aria-hidden="true">
      {Array.from({ length: 6 }).map((_, i) => (
        <div className="metric skel" key={i}>
          <span className="skel-line skel-line-sm" />
          <span className="skel-line skel-line-lg" />
          <span className="skel-line skel-line-sm" />
        </div>
      ))}
    </section>
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
  const [freshness, setFreshness] = useState<FreshnessMap | null>(null);
  const [story, setStory] = useState<EnergyStoryData | null>(null);
  const [storyWindow, setStoryWindow] = useState<"past" | "next">("next");
  const [strategy, setStrategy] = useState<Strategy | null>(null);
  const [battery, setBattery] = useState<Battery | null>(null);
  const [showBattery, setShowBattery] = useState(false);
  const [decision, setDecision] = useState<Decision | null>(null);
  const [alertsData, setAlertsData] = useState<AlertsResp | null>(null);
  const [chargeNeed, setChargeNeed] = useState<ChargeNeed | null>(null);
  const [savings, setSavings] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [view, setView] =
    useState<"dashboard" | "chat" | "audit" | "settings" | "system">("dashboard");
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

  // The battery tile opens a per-tower breakdown only when there's a cluster to break down.
  const batteryHasDetail = !!(battery && (battery.aggregate || battery.towers.length > 0));

  return (
    <div className="app">
      <header className="topbar">
        <h1>Smart Energy Manager</h1>
        {status && (
          <span
            className={`badge ${status.dry_run ? "badge-dryrun" : "badge-live"}`}
            data-testid="run-mode-badge"
            title={status.dry_run ? RUN_MODE.dry.title : RUN_MODE.live.title}
          >
            {status.dry_run ? RUN_MODE.dry.label : RUN_MODE.live.label}
          </span>
        )}
        {status && (
          <span
            className="badge badge-muted"
            data-testid="data-source"
            title={status.dev_mode === "live" ? DATA_SOURCE.live.title : DATA_SOURCE.sim.title}
          >
            {status.dev_mode === "live" ? DATA_SOURCE.live.label : DATA_SOURCE.sim.label}
          </span>
        )}
        {alertsData && (
          <span
            className={`badge badge-dq dq-${alertsData.data_quality}`}
            data-testid="data-quality"
            title={
              DATA_QUALITY[alertsData.data_quality]?.title ??
              "How fresh and complete the data behind the plan is."
            }
          >
            {DATA_QUALITY[alertsData.data_quality]?.label ?? humanize(alertsData.data_quality)}
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
            className={`nav-btn${view === "chat" ? " nav-active" : ""}`}
            onClick={() => setView("chat")}
            data-testid="nav-chat"
            aria-current={view === "chat" ? "page" : undefined}
          >
            Chat
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
            className={`nav-btn${view === "audit" ? " nav-active" : ""}`}
            onClick={() => setView("audit")}
            data-testid="nav-audit"
            aria-current={view === "audit" ? "page" : undefined}
          >
            Audit
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

      {view === "chat" && <ChatPanel />}

      {view === "audit" && <AuditView />}

      {view === "system" && <SystemView />}

      {view === "dashboard" && status && (
        <section className="grid" data-testid="status-grid">
          {savings != null && (
            <Metric
              label="Saved today"
              value={`€${savings.toFixed(2)}`}
              hint="vs. no smart control"
              title="Rough estimate of what smart charging saved today compared with leaving the battery on its own."
              icon="euro"
              accent
            />
          )}
          <Metric
            label="Battery level"
            value={`${status.soc_pct.toFixed(0)} %`}
            hint={batteryHasDetail ? "see each battery →" : "how full it is"}
            title={
              batteryHasDetail
                ? "How full the home battery is — click to see each battery."
                : "How much charge is in the home battery right now."
            }
            icon="battery-level"
            onClick={batteryHasDetail ? () => setShowBattery(true) : undefined}
            testId="battery-tile"
          />
          <Metric
            label="House load"
            value={fmtW(status.house_load_w)}
            hint="what your home is using now"
            title="Everything your home is drawing right now, from solar, battery and the grid combined."
            icon="home"
          />
          <Metric
            label="Grid"
            value={fmtW(status.grid_power_w)}
            hint={status.grid_power_w >= 0 ? "from the grid" : "to the grid"}
            title="Power flowing in from the grid (buying) or back out to it (selling)."
            icon="grid"
          />
          <Metric
            label="Solar"
            value={fmtW(status.solar_power_w)}
            hint="from your panels"
            icon="solar"
          />
          <Metric
            label="Battery"
            value={fmtW(status.battery_power_w)}
            hint={status.battery_power_w >= 0 ? "powering the house" : "charging up"}
            title="Power leaving the battery to run the house, or going in to charge it."
            icon="bolt"
          />
          <Metric
            label="Home use"
            value={fmtW(status.non_ev_load_w)}
            hint="excluding the car"
            title="What the home uses, not counting car charging."
            icon="bulb"
          />
          {battery?.current_mode && (
            <Metric
              label="Battery mode"
              value={PHYSICAL_MODE[battery.current_mode] ?? humanize(battery.current_mode)}
              hint={battery.capabilities?.p1_paired ? "balancing to your meter" : "standalone"}
              title="The mode the battery is currently running in."
              icon="sliders"
            />
          )}
        </section>
      )}

      {view === "dashboard" && !status && !error && <SkeletonGrid />}

      {view === "dashboard" && strategy && (
        <StrategyCard
          strategy={strategy}
          onChange={setStrategyMode}
          onSetGridTopup={setGridTopup}
          onTune={() => setView("settings")}
        />
      )}

      {view === "dashboard" && (
        <EnergyStory story={story} window={storyWindow} onWindow={setStoryWindow} />
      )}

      {view === "dashboard" && chargeNeed && <ChargeTarget n={chargeNeed} />}

      {view === "dashboard" && status && <OverrideCard />}

      {view === "dashboard" && decision && decision.outcome !== "unconfigured" && (
        <section className="decision" data-testid="decision">
          <div className="decision-head">
            <span className="metric-label">Controller</span>
            {decision.car_charging && (
              <span className="badge badge-car" data-testid="car-charging">
                <Icon name="car" /> Car charging — battery held
              </span>
            )}
          </div>
          <p className="decision-line">
            <span className="decision-outcome">
              {OUTCOME_LABEL[decision.outcome] ?? humanize(decision.outcome)}
            </span>
            {" — "}
            {decision.reason}
          </p>
          {decision.plan_reason && (
            <p className="plan-reason">
              plan: {decision.explanation_source === "external_llm" && decision.plan_reason_explained
                ? decision.plan_reason_explained
                : decision.plan_reason}
              {decision.explanation_source === "external_llm" && (
                <span className="ai-tag" title="Phrased by AI from the system's own decision — the numbers are the real ones.">
                  AI
                </span>
              )}
            </p>
          )}
        </section>
      )}

      {view === "dashboard" && freshness && Object.keys(freshness).length > 0 && (
        <section className="freshness" data-testid="freshness">
          <span className="freshness-title" title="Whether each piece of live data is current.">
            Data status
          </span>
          {Object.entries(freshness).map(([sig, st]) => (
            <span
              key={sig}
              className={`chip chip-${st}`}
              data-signal={sig}
              title={`${SIGNAL_NAME[sig] ?? humanize(sig)} — ${FRESHNESS_STATE[st] ?? st}`}
            >
              {SIGNAL_NAME[sig] ?? humanize(sig)}: {FRESHNESS_STATE[st] ?? st}
            </span>
          ))}
        </section>
      )}

      {view === "dashboard" && showBattery && batteryHasDetail && (
        <Modal title="Battery — per tower" onClose={() => setShowBattery(false)} testId="battery-modal">
          <BatteryChips battery={battery} />
        </Modal>
      )}
    </div>
  );
}
