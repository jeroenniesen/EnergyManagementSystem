import { useEffect, useRef, useState } from "react";

import { type Battery, BatteryChips } from "./BatteryChips";
import { EnergyDistribution } from "./EnergyDistribution";
import { type EnergyStoryData, EnergyStory } from "./EnergyStory";
import { BatteryPlan, type BatteryPlanData, type PlanConfidence, type SavedToday } from "./BatteryPlan";
import { Icon, type IconName } from "./icons";
import {
  CAR_BADGE_SUFFIX,
  CAR_BADGE_SUFFIX_DEFAULT,
  DATA_QUALITY,
  DATA_SOURCE,
  FRESHNESS_STATE,
  humanize,
  OUTCOME_LABEL,
  PHYSICAL_MODE,
  RUN_MODE,
  SIGNAL_NAME,
} from "./labels";
import { NotificationBell } from "./Notifications";
import { OverrideCard } from "./Override";
import { CarCard } from "./CarCard";
import { CarView } from "./Car";
import { Manage, type ManageTab } from "./Manage";
import { type Strategy, StrategyCard } from "./StrategyCard";
import { AiValidationCard } from "./AiValidationCard";
import { ChatPanel } from "./ChatPanel";
import { Insights } from "./Insights";
import { HomeScores, type Report } from "./HomeScores";
import { homeSummary } from "./scoreCopy";
import { SkyBackdrop } from "./SkyBackdrop";
import { Advanced } from "./Advanced";
import { applyTheme, readStoredTheme, storeTheme, type Theme } from "./theme";
import { authHeaders } from "./auth";
import { DetailDrawer } from "./DetailDrawer";

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
  target_soc?: number | null;
  override_active?: boolean;
  home_state?: { headline: string; tone: string; simulated: boolean };
};

type ChargeNeed = {
  current_soc_pct: number;
  target_soc_pct: number;
  deficit_kwh: number;
  on_track: boolean;
  reason: string;
};

// Just enough of GET /api/car/plan (CarCard.tsx owns the full shape) to overlay the car's PLANNED
// charging windows on the main battery-plan chart (feat/ux-batch-3) — "when should the car
// charge" answered right on the dashboard, not only inside the Car tab/compact card.
type CarPlanWindow = { start: string; end: string };
type CarPlanSummary = { enabled: boolean; windows: CarPlanWindow[] };

// `safe` and `action` are optional, structured sub-lines (B-37): "is my home safe" + "what can I
// do". The backend adds them incrementally; the UI renders them only when present, else falls back
// to the bare message — so an alert without the fields still renders exactly as before.
type AlertItem = { key: string; severity: string; message: string; safe?: string; action?: string };
type AlertsResp = { data_quality: string; alerts: AlertItem[] };
// The nav restructure (feat/ux-batch-3): five top-level views. Settings/System/Audit are no longer
// top-level — they are sub-tabs of "manage" (see Manage.tsx + `ManageTab`). "car" is a first-class
// view because the weekly schedule changes often and car config/insight was scattered before.
type ViewName = "dashboard" | "insights" | "car" | "chat" | "manage";
// A route is the top-level view plus, for "manage", which sub-tab is showing. The manage tab is
// carried even on other views so returning to Manage can be deterministic, and so the hash router
// (below) can round-trip `#manage/system` etc.
type Route = { view: ViewName; tab: ManageTab };

// A contextual dashboard drawer (2026-07-15 plan). Each kind is one "tell me more" surface; the
// drawer is a peer of the top-level route so it can be deep-linked (`#dashboard/<kind>`) in Task 2.
type DrawerRoute =
  | { kind: "now" }
  | { kind: "savings" }
  | { kind: "confidence" }
  | { kind: "battery" }
  | { kind: "decision"; id: string };

// Dashboard refresh cadence. Device reads (battery cluster, meters) are coalesced server-side to
// at most once per ~30 s regardless of this, so a snappy poll no longer floods the hardware; 10 s
// keeps the UI lively while halving HTTP chatter vs. the old 5 s.
const POLL_MS = 10000;
// Local-calendar "today" (matches the same convention Insights/EnergyDistribution use for their
// day anchors) — used to ask /api/finance for B-03b's measured "Saved today" figure.
function todayStr(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(
    d.getDate(),
  ).padStart(2, "0")}`;
}
// Alert hierarchy: control-blocking (critical) above degraded (warning) above info (energy review).
const SEVERITY_RANK: Record<string, number> = { critical: 3, warning: 2, info: 1 };
const VIEWS: ViewName[] = ["dashboard", "insights", "car", "chat", "manage"];
const MANAGE_TABS: ManageTab[] = ["settings", "system", "audit"];
// B-68: plain-language chip label for the plan-confidence score, keyed by the backend's level.
const CONFIDENCE_CHIP_LABEL: Record<PlanConfidence["level"], string> = {
  high: "High confidence",
  medium: "Medium confidence",
  low: "Low confidence",
};
// Homeowner-first drawer headings, keyed by drawer kind (content lands in Tasks 3–5).
const DRAWER_TITLE: Record<DrawerRoute["kind"], string> = {
  now: "What EMS is doing now",
  savings: "Your savings",
  confidence: "How much to trust this",
  battery: "Battery detail",
  decision: "Why EMS acted",
};

// Hash → route. Canonical hashes: #dashboard #insights #car #chat #manage #manage/system
// #manage/audit. LEGACY hashes still work so old bookmarks / deep-links don't break: bare
// #settings|#system|#audit redirect to the matching Manage sub-tab. Anything unknown falls back to
// the dashboard (loop-2's finding — a mistyped hash must never blank the app).
function routeFromHash(hash: string): Route {
  const raw = hash.replace(/^#\/?/, "");
  // Legacy top-level Settings/System/Audit → the Manage sub-tab they became.
  if (raw === "settings") return { view: "manage", tab: "settings" };
  if (raw === "system") return { view: "manage", tab: "system" };
  if (raw === "audit") return { view: "manage", tab: "audit" };
  // Manage + its sub-tabs. Bare #manage (and #manage/settings) open the Settings tab.
  if (raw === "manage") return { view: "manage", tab: "settings" };
  const m = raw.match(/^manage\/(.+)$/);
  if (m) {
    const tab = m[1] as ManageTab;
    return { view: "manage", tab: MANAGE_TABS.includes(tab) ? tab : "settings" };
  }
  return {
    view: VIEWS.includes(raw as ViewName) ? (raw as ViewName) : "dashboard",
    tab: "settings",
  };
}

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
  const [batteryPlan, setBatteryPlan] = useState<BatteryPlanData | null>(null);
  const [storyWindow, setStoryWindow] = useState<"past" | "next">("next");
  const [strategy, setStrategy] = useState<Strategy | null>(null);
  const [battery, setBattery] = useState<Battery | null>(null);
  // Which per-tower breakdown is open (if any): "soc" from the Battery level tile, "power" from the
  // Battery (power) tile. Both open the same modal, emphasising the metric you clicked.
  const [batteryDetail, setBatteryDetail] = useState<"soc" | "power" | null>(null);
  const [decision, setDecision] = useState<Decision | null>(null);
  const [alertsData, setAlertsData] = useState<AlertsResp | null>(null);
  const [chargeNeed, setChargeNeed] = useState<ChargeNeed | null>(null);
  // The car's planned charging windows, threaded down to BatteryPlan's chart overlay (see
  // CarPlanSummary above) — null until the first fetch resolves, same best-effort convention as
  // the other polled cards below.
  const [carPlan, setCarPlan] = useState<CarPlanSummary | null>(null);
  // B-03b: MEASURED (from /api/finance), not a plan estimate — null until the first successful
  // fetch (then the footer stat stays hidden; a later failure just keeps the last-known value,
  // same best-effort convention as the other polled cards below).
  const [savedToday, setSavedToday] = useState<SavedToday | null>(null);
  const [report, setReport] = useState<Report | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [route, setRoute] = useState<Route>(() => routeFromHash(window.location.hash));
  const view = route.view;
  const manageTab = route.tab;
  // The open contextual drawer, or null. Held in App state (Task 1); wired to the hash in Task 2.
  const [drawer, setDrawer] = useState<DrawerRoute | null>(null);
  const closeDrawer = () => setDrawer(null);
  // "See the full plan" disclosure — collapsed by default, choice remembered across visits so a
  // homeowner who wants the detail keeps it, and one who doesn't never sees it re-expand.
  const [planOpen, setPlanOpen] = useState<boolean>(() => {
    try {
      return localStorage.getItem("ems.dash.planOpen") === "1";
    } catch {
      return false;
    }
  });
  // The demo-home nudge dismisses for the session only (sessionStorage) — gone for this visit, back
  // next time, so it can't be permanently lost while the app is still running on demo data.
  const [demoDismissed, setDemoDismissed] = useState<boolean>(() => {
    try {
      return sessionStorage.getItem("ems.demoCtaDismissed") === "1";
    } catch {
      return false;
    }
  });
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
  useEffect(() => {
    const onHash = () => setRoute(routeFromHash(window.location.hash));
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);
  // Keep <html data-theme> in sync and cache the choice for the next load's first paint.
  useEffect(() => {
    storeTheme(theme);
    return applyTheme(theme);
  }, [theme]);

  // Today's scores — fetched once here (off the fast poll) so BOTH the score pills and the hero's
  // synthesis line read the SAME summary; no second source, no chance of two different verdicts.
  useEffect(() => {
    let alive = true;
    fetch("/api/report?period=day")
      .then((r) => (r.ok ? r.json() : null))
      .then((v: Report | null) => {
        if (alive && v) setReport(v);
      })
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, []);

  // Persist the disclosure choice for next visit.
  useEffect(() => {
    try {
      localStorage.setItem("ems.dash.planOpen", planOpen ? "1" : "0");
    } catch {
      /* private-mode / storage-disabled: the toggle still works in-session */
    }
  }, [planOpen]);

  function dismissDemoCta() {
    setDemoDismissed(true);
    try {
      sessionStorage.setItem("ems.demoCtaDismissed", "1");
    } catch {
      /* best-effort; the dismiss still holds in memory for this render */
    }
  }

  // Navigate to a view (and, for "manage", a sub-tab). Writes the canonical hash so the URL always
  // round-trips (bookmarks, back/forward); the hashchange listener also fires and re-derives the
  // route, but setting state here too keeps the switch instant. `tab` defaults to "settings" so a
  // bare `navigate("manage")` opens the Settings sub-tab, matching #manage.
  function navigate(next: ViewName, tab: ManageTab = "settings") {
    const hash = next === "manage" && tab !== "settings" ? `manage/${tab}` : next;
    if (window.location.hash.replace(/^#\/?/, "") !== hash) {
      window.location.hash = hash;
    }
    setRoute({ view: next, tab: next === "manage" ? tab : route.tab });
  }

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
      fill("/api/battery-plan", setBatteryPlan);
      fill("/api/strategy", (v: Strategy) => { if (!strategyPending.current) setStrategy(v); });
      fill("/api/battery", setBattery);
      fill("/api/decision", setDecision);
      fill("/api/alerts", setAlertsData);
      // B-03b: the measured figure, not the old plan-estimate tile — never a fake €0.00. finance's
      // totals.saved_eur is null until a day of prices has been recorded, in which case the footer
      // shows "measuring" instead of inventing a number.
      fill(
        `/api/finance?period=day&date=${todayStr()}`,
        (v: { totals?: { saved_eur: number | null } }) => {
          const eurVal = v?.totals?.saved_eur;
          setSavedToday(eurVal == null ? { status: "measuring" } : { status: "measured", eur: eurVal });
        },
      );
      fill("/api/charge-need", (v: ChargeNeed) => setChargeNeed(v ?? null));
      // Planned car-charging windows for the chart overlay (feat/ux-batch-3) — `enabled:false`
      // (feature off) carries no `plan`, so windows are only ever read when the feature is on.
      fill(
        "/api/car/plan",
        (v: { enabled?: boolean; plan?: { windows?: CarPlanWindow[] } | null }) => {
          setCarPlan({
            enabled: Boolean(v?.enabled),
            windows: v?.enabled ? v.plan?.windows ?? [] : [],
          });
        },
      );
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
        // /api/settings is always write-gated once a token is set — send it, or the write 401s
        // and silently reverts (matches Settings.tsx). Empty object when no token is configured.
        headers: { "Content-Type": "application/json", ...authHeaders() },
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

  // --- Hero synthesis (B-32): one verdict, not three fragments. ---------------------------------
  const home = decision?.home_state ?? null;
  const summary = report ? homeSummary(report.scores) : null;
  // B-68: the plan-confidence score rides on the already-polled /api/battery-plan response — no
  // extra fetch. Calm stays calm: the reason sub-line only renders when confidence isn't high.
  const confidence = batteryPlan?.confidence ?? null;
  // The synthesis line stitches the existing on-track verdict and the existing day-score summary
  // into ONE sentence — reusing the exact strings, inventing no number. Trailing punctuation is
  // trimmed so the middot join reads cleanly ("…88% target · A solid energy day — keep it up").
  const trimEnd = (s: string) => s.replace(/[.\s]+$/, "");
  const synthesis = [story?.on_track?.message, summary?.text]
    .filter((s): s is string => !!s)
    .map(trimEnd)
    .join(" · ");

  // "Do I need to act?" — answered explicitly. Nothing to do unless an override is running, the
  // system has fallen back to safe mode (unsafe data), or a warning/critical alert is live. Info
  // notes (e.g. watch-only) are calm by design and never raise the act line.
  const alerts = alertsData?.alerts ?? [];
  const topActionable = [...alerts]
    .filter((a) => a.severity === "warning" || a.severity === "critical")
    .sort((a, b) => (SEVERITY_RANK[b.severity] ?? 0) - (SEVERITY_RANK[a.severity] ?? 0))[0];
  const actLine: { text: string; calm: boolean } = decision?.override_active
    ? { text: "You're in manual control — it ends on its own, or clear it below.", calm: false }
    : alertsData?.data_quality === "unsafe"
      ? {
          text:
            "EMS paused control and fell back to the battery's own safe mode — nothing to do; it " +
            "resumes on its own once the data is trustworthy again.",
          calm: false,
        }
      : topActionable
        ? { text: topActionable.action || topActionable.message, calm: false }
        : { text: "Nothing needed from you.", calm: true };

  const batteryModeLabel = battery?.current_mode
    ? PHYSICAL_MODE[battery.current_mode] ?? humanize(battery.current_mode)
    : null;

  // B-57: on demo/mock data, a persistent friendly nudge into real onboarding (Settings opens on
  // the Connection section by default). Dismissible for the session; back on next visit.
  const demoActive = !!home?.simulated && !demoDismissed;

  return (
    <>
      <SkyBackdrop compact={view !== "dashboard"} />
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
        <NotificationBell />
        <nav className="nav" aria-label="Views">
          <button
            className={`nav-btn${view === "dashboard" ? " nav-active" : ""}`}
            onClick={() => navigate("dashboard")}
            data-testid="nav-dashboard"
            aria-current={view === "dashboard" ? "page" : undefined}
          >
            Dashboard
          </button>
          <button
            className={`nav-btn${view === "insights" ? " nav-active" : ""}`}
            onClick={() => navigate("insights")}
            data-testid="nav-insights"
            aria-current={view === "insights" ? "page" : undefined}
          >
            Insights
          </button>
          <button
            className={`nav-btn nav-btn-car${view === "car" ? " nav-active" : ""}`}
            onClick={() => navigate("car")}
            data-testid="nav-car"
            aria-current={view === "car" ? "page" : undefined}
          >
            <span className="car-dot" aria-hidden="true" />
            Car
          </button>
          <button
            className={`nav-btn${view === "chat" ? " nav-active" : ""}`}
            onClick={() => navigate("chat")}
            data-testid="nav-chat"
            aria-current={view === "chat" ? "page" : undefined}
          >
            Chat
          </button>
          {/* Manage folds the three ops surfaces (Settings · System · Audit) — "often used
              together and eat menu space" — behind one item with its own segmented sub-nav. */}
          <button
            className={`nav-btn${view === "manage" ? " nav-active" : ""}`}
            onClick={() => navigate("manage")}
            data-testid="nav-manage"
            aria-current={view === "manage" ? "page" : undefined}
          >
            Manage
          </button>
        </nav>
      </header>

      {alertsData && alertsData.alerts.length > 0 && (
        <section className="alerts" data-testid="alerts">
          {/* Sort by severity so a control-blocking issue never sits below a watch-only note. */}
          {[...alertsData.alerts]
            .sort((a, b) => (SEVERITY_RANK[b.severity] ?? 0) - (SEVERITY_RANK[a.severity] ?? 0))
            .map((a) => (
              <div
                key={a.key}
                className={`alert-item alert-${a.severity}`}
                data-severity={a.severity}
                data-testid={`alert-${a.key}`}
              >
                <span className="alert-message">{a.message}</span>
                {/* B-37 structured sub-lines — only for warning/critical. Info-level notices
                    (watch-only, dry-run) stay one calm line; their reassurance would otherwise
                    out-shout the hero's "Nothing needed from you." */}
                {a.severity !== "info" && a.safe && (
                  <span className="alert-safe" data-testid="alert-safe">
                    <Icon name="check" /> {a.safe}
                  </span>
                )}
                {a.severity !== "info" && a.action && (
                  <span className="alert-action" data-testid="alert-action">
                    → {a.action}
                  </span>
                )}
              </div>
            ))}
        </section>
      )}

      {view === "dashboard" && error && (
        <div className="error" data-testid="error">Cannot reach EMS API: {error}</div>
      )}

      {/* The hero: one verdict, one synthesis line, one explicit answer to "do I need to act?".
          Absorbs the old status banner + the scattered on-track/score copy into a single read. */}
      {view === "dashboard" && home && (
        <section
          className={`hero home-${home.tone}`}
          data-testid="home-state"
          data-tone={home.tone}
        >
          <div className="hero-verdict-row">
            <p className="hero-verdict" data-testid="hero-verdict">
              {home.headline}
            </p>
            {confidence && (
              <span
                className={`badge confidence-chip confidence-${confidence.level}`}
                data-testid="confidence-chip"
                data-level={confidence.level}
                title={confidence.reasons.join(" ")}
              >
                {CONFIDENCE_CHIP_LABEL[confidence.level]}
              </span>
            )}
          </div>
          {confidence && confidence.level !== "high" && (
            <p className="hero-confidence-reason" data-testid="hero-confidence-reason">
              {confidence.reasons[0]}
            </p>
          )}
          {synthesis && (
            <p className="hero-synthesis" data-testid="hero-synthesis">
              {synthesis}
            </p>
          )}
          <p
            className={`hero-act ${actLine.calm ? "hero-act-calm" : "hero-act-attention"}`}
            data-testid="hero-act"
          >
            {!actLine.calm && <Icon name="alert" className="hero-act-icon" />}
            {actLine.text}
          </p>
          <button
            type="button"
            className="hero-details-link"
            data-testid="dashboard-now-trigger"
            onClick={() => setDrawer({ kind: "now" })}
          >
            What is EMS doing? →
          </button>
          {demoActive && (
            <div className="hero-demo-cta" data-testid="demo-cta">
              <span>
                This is a demo home.{" "}
                <button
                  type="button"
                  className="hero-demo-link"
                  data-testid="demo-cta-link"
                  onClick={() => navigate("manage", "settings")}
                >
                  Use my real home →
                </button>
              </span>
              <button
                type="button"
                className="hero-demo-dismiss"
                data-testid="demo-cta-dismiss"
                aria-label="Dismiss for now"
                onClick={dismissDemoCta}
              >
                ×
              </button>
            </div>
          )}
        </section>
      )}

      {view === "manage" && (
        <Manage
          tab={manageTab}
          onTab={(t) => navigate("manage", t)}
          onSettingsSaved={(v) => setTheme((v["ui.theme"] as Theme) ?? "auto")}
        />
      )}

      {view === "insights" && <Insights />}

      {view === "chat" && <ChatPanel />}

      {view === "car" && (
        <CarView onOpenSettings={() => navigate("manage", "settings")} />
      )}

      {view === "dashboard" && (
        <HomeScores report={report} onOpenDetail={() => navigate("insights")} />
      )}

      {/* The ONE today-story: the single narrative + chart. Its footer carries the live snapshot
          (saved / battery / mode) the old stat-tile row used to. */}
      {view === "dashboard" && (
        <BatteryPlan
          plan={batteryPlan}
          savedToday={savedToday}
          socPct={status?.soc_pct ?? null}
          batteryMode={batteryModeLabel}
          onBatteryClick={batteryHasDetail ? () => setBatteryDetail("soc") : undefined}
          carWindows={carPlan?.windows ?? []}
          carWindowsEnabled={carPlan?.enabled ?? false}
        />
      )}

      {/* The full plan (the past/next toggle + tiles + charts) lives one tap deeper — collapsed by
          default so the story card's headline is the only narrative on screen. When opened, the
          plan renders WITHOUT its own headline sentence (hideHeadline), so the two never duplicate. */}
      {view === "dashboard" && (
        <section className="plan-disclosure" data-testid="plan-disclosure">
          <button
            type="button"
            className={`advanced-toggle${planOpen ? " open" : ""}`}
            data-testid="plan-disclosure-toggle"
            aria-expanded={planOpen}
            onClick={() => setPlanOpen((o) => !o)}
          >
            <span className="advanced-chevron" aria-hidden="true">›</span>
            <span>{planOpen ? "Hide the full plan" : "See the full plan"}</span>
          </button>
          {planOpen && (
            <div className="plan-disclosure-body" data-testid="plan-disclosure-body">
              <EnergyStory
                story={story}
                window={storyWindow}
                onWindow={setStoryWindow}
                hideHeadline
              />
            </div>
          )}
        </section>
      )}

      {view === "dashboard" && strategy && (
        <StrategyCard
          strategy={strategy}
          onChange={setStrategyMode}
          onSetGridTopup={setGridTopup}
          onTune={() => navigate("manage", "settings")}
        />
      )}

      {/* A manual override sits with the strategy control, above the detail. */}
      {view === "dashboard" && status && (
        <OverrideCard dataQuality={alertsData?.data_quality} />
      )}

      {/* Opt-in, advisory-only car-charging plan — the COMPACT variant here (SoC + next window +
          advice + "Open Car →"); the full plan lives in the dedicated Car view. */}
      {view === "dashboard" && status && (
        <CarCard compact onOpenCar={() => navigate("car")} />
      )}

      {/* Advanced — the full detail, tucked away by default (opens on demand). */}
      {view === "dashboard" && status && (
        <Advanced>
          <section className="grid" data-testid="detail-grid">
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
            <Metric label="Solar" value={fmtW(status.solar_power_w)} hint="from your panels" icon="solar" />
            <Metric
              label="Battery"
              value={fmtW(status.battery_power_w)}
              hint={
                batteryHasDetail
                  ? "see each battery →"
                  : status.battery_power_w >= 0
                    ? "powering the house"
                    : "charging up"
              }
              title={
                batteryHasDetail
                  ? "Power leaving the battery to run the house, or going in to charge it — click to see each battery."
                  : "Power leaving the battery to run the house, or going in to charge it."
              }
              icon="bolt"
              onClick={batteryHasDetail ? () => setBatteryDetail("power") : undefined}
              testId="battery-power-tile"
            />
            <Metric
              label="Home use"
              value={fmtW(status.non_ev_load_w)}
              hint="excluding the car"
              title="What the home uses, not counting car charging."
              icon="bulb"
            />
          </section>

          <EnergyDistribution />

          {chargeNeed && <ChargeTarget n={chargeNeed} />}

          {decision && decision.outcome !== "unconfigured" && (
            <section className="decision" data-testid="decision">
              <div className="decision-head">
                <span className="metric-label">Controller</span>
                {decision.car_charging && (
                  <span className="badge badge-car" data-testid="car-charging">
                    <Icon name="car" /> Car charging —{" "}
                    {CAR_BADGE_SUFFIX[decision.desired_mode ?? ""] ?? CAR_BADGE_SUFFIX_DEFAULT}
                  </span>
                )}
              </div>
              <p className="decision-line">
                <span className="decision-outcome">
                  {OUTCOME_LABEL[decision.outcome] ?? humanize(decision.outcome)}
                </span>
                {" — "}
                {decision.reason}
                {decision.target_soc != null && (
                  <span className="decision-target" data-testid="decision-target">
                    {" "}· aiming for {Math.round(decision.target_soc)}%
                  </span>
                )}
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

          <AiValidationCard />

          {freshness && Object.keys(freshness).length > 0 && (
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
        </Advanced>
      )}

      {view === "dashboard" && batteryDetail && batteryHasDetail && (
        <Modal
          title={batteryDetail === "power" ? "Battery power — per tower" : "Battery — per tower"}
          onClose={() => setBatteryDetail(null)}
          testId="battery-modal"
        >
          <BatteryChips battery={battery} metric={batteryDetail} />
        </Modal>
      )}

      {/* One reusable contextual drawer for the dashboard "tell me more" surfaces. Content per
          kind is filled in across Tasks 3–5; Task 1 establishes the shell + the Now trigger. */}
      <DetailDrawer
        open={drawer !== null}
        title={drawer ? DRAWER_TITLE[drawer.kind] : ""}
        eyebrow="Dashboard detail"
        onClose={closeDrawer}
      >
        {drawer?.kind === "now" && (
          <p data-testid="drawer-placeholder">{home?.headline ?? "EMS status"}</p>
        )}
      </DetailDrawer>
      </div>
    </>
  );
}
