import { useCallback, useEffect, useRef, useState } from "react";

import { AcceptInvite } from "./AcceptInvite";
import { apiFetch, setUnauthorizedHandler } from "./auth";
import { BatteryActionWhy } from "./BatteryActionWhy";
import { type Battery, BatteryChips } from "./BatteryChips";
import {
  EveningPeakCoverageCard,
  applyEveningPeakTrustMarkerPolicy,
  isEveningPeakCovered,
} from "./EveningPeakCoverage";
import { EnergyDistribution } from "./EnergyDistribution";
import type {
  BatteryPlanData,
  EnergyStoryData,
  PlanConfidence,
  SavedToday,
} from "./EnergyStory";
import type { DecisionReason } from "./decisionWhy";
import { buildHeroSynthesis } from "./heroSynthesis";
import { Icon, type IconName } from "./icons";
import { DeviceHealthStrip } from "./DeviceHealth";
import { showTopbarDataQuality, showTopbarDataSource } from "./topbarChips";
import {
  CAR_BADGE_SUFFIX,
  CAR_BADGE_SUFFIX_DEFAULT,
  DATA_QUALITY,
  DATA_SOURCE,
  type DeviceHealth,
  EMS_UNREACHABLE,
  FRESHNESS_STATE,
  formatLaatstBekend,
  HOME_ACT,
  HOME_CONFIDENCE_CHIP,
  HOME_HERO,
  HOME_MORE_NEST,
  HOME_MORE_TOGGLE,
  homeConfidenceReasonNl,
  homeHeadlineNl,
  humanize,
  OUTCOME_LABEL,
  pickDeviceHealthAlert,
  DRY_RUN_CAUSE_LABEL,
  DRY_RUN_CAUSE_FALLBACK,
  RUN_MODE,
  SIGNAL_NAME,
} from "./labels";
import { Login } from "./Login";
import { NotificationBell } from "./Notifications";
import { Onboarding } from "./Onboarding";
import { HomeMoreNest } from "./HomeMoreNest";
import { OverrideCard } from "./Override";
import { CarCard } from "./CarCard";
import { CarView } from "./Car";
import { Manage, type ManageTab } from "./Manage";
import { type Strategy, StrategyCard } from "./StrategyCard";
import { AiValidationCard } from "./AiValidationCard";
import { ChatPanel } from "./ChatPanel";
import { TradingView } from "./Trading";
import { Insights } from "./Insights";
import { HomeScores, type Report } from "./HomeScores";
import { OutcomeTiles, type TileFreshness } from "./OutcomeTiles";
import { PlanStory } from "./PlanStory";
import { homeSummary } from "./scoreCopy";
import { SkyBackdrop } from "./SkyBackdrop";
import { Advanced } from "./Advanced";
import { applyTheme, readStoredTheme, storeTheme, type Theme } from "./theme";

type Status = {
  dry_run: boolean;
  dry_run_reason?: string | null;
  dry_run_cause?: string | null;
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

/** B-67 / #74 — advice-only night-reserve recommendation. Never writes settings. */
type ReserveAdvice = {
  recommended_soc_pct: number;
  min_reserve_soc: number;
  current_night_reserve_kwh: number;
  current_target_soc_pct: number;
  night_demand_kwh: number | null;
  label: string | null;
  reason: string;
  automatic: boolean;
};

/** True when a charge-need payload has the finite fields ChargeTarget.toFixed needs (#134 B2). */
export function isUsableChargeNeed(n: unknown): n is ChargeNeed {
  if (n == null || typeof n !== "object") return false;
  const o = n as Record<string, unknown>;
  return (
    typeof o.current_soc_pct === "number" &&
    typeof o.target_soc_pct === "number" &&
    typeof o.deficit_kwh === "number" &&
    typeof o.on_track === "boolean" &&
    typeof o.reason === "string"
  );
}

// `safe`, `action`, and `ems_doing` are optional structured sub-lines (B-37 / B-09): "is my home
// safe", "what can I do", and "what EMS is doing". The UI renders each only when present, else
// falls back to the bare message — so an alert without the fields still renders as before.
type AlertItem = {
  key: string;
  severity: string;
  message: string;
  safe?: string;
  action?: string;
  ems_doing?: string;
};
type AlertsResp = { data_quality: string; alerts: AlertItem[] };
// The nav restructure (feat/ux-batch-3): five top-level views. Settings/System/Audit are no longer
// top-level — they are sub-tabs of "manage" (see Manage.tsx + `ManageTab`). "car" is a first-class
// view because the weekly schedule changes often and car config/insight was scattered before.
type ViewName = "dashboard" | "insights" | "car" | "chat" | "trading" | "manage";
// A route is the top-level view plus, for "manage", which sub-tab is showing. The manage tab is
// carried even on other views so returning to Manage can be deterministic, and so the hash router
// (below) can round-trip `#manage/system` etc.
type Route = { view: ViewName; tab: ManageTab };

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
const VIEWS: ViewName[] = ["dashboard", "insights", "car", "chat", "trading", "manage"];
const MANAGE_TABS: ManageTab[] = ["settings", "system", "audit"];
// B-68 + B-98: plain-language confidence chip (Dutch-first Home).
const CONFIDENCE_CHIP_LABEL: Record<PlanConfidence["level"], string> = HOME_CONFIDENCE_CHIP;

// Hash → route. Canonical hashes: #dashboard #insights #car #chat #trading #manage #manage/system
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

/** Advice-only night reserve (B-67 / #74) — shown next to the configured buffer; never applied. */
function NightReserveAdvice({ advice }: { advice: ReserveAdvice }) {
  return (
    <section className="charge-need reserve-advice" data-testid="reserve-advice">
      <div className="override-head">
        <span className="metric-label">Night reserve</span>
        {advice.label && (
          <span className="badge badge-amber" data-testid="reserve-advice-label">
            {advice.label}
          </span>
        )}
      </div>
      <p className="reserve-advice-row" data-testid="reserve-advice-current">
        Your setting:{" "}
        <strong>{advice.current_night_reserve_kwh.toFixed(1)} kWh</strong>
        {" · "}
        Advice: <strong>{advice.recommended_soc_pct.toFixed(0)}%</strong> carry
        {advice.recommended_soc_pct !== advice.current_target_soc_pct && (
          <span className="reserve-advice-delta">
            {" "}
            (usual plan {advice.current_target_soc_pct.toFixed(0)}%)
          </span>
        )}
      </p>
      <p className="plan-reason" data-testid="reserve-advice-reason">
        {advice.reason}
      </p>
      <p className="advisor-hint">Advice only — this never changes your settings.</p>
    </section>
  );
}


// Discovery payload shape from GET /api/auth (identity mode) — see docs/superpowers/specs/
// 2026-07-17-auth-users-roles-design.md §5. `null` until the first fetch resolves. `user` (auth
// slice 2 web) carries the signed-in principal's role, which drives both the admin panel
// (Settings → Access & security) and reader read-only mode (`canOperate` below) — `null` when
// unauthenticated.
type AuthState = {
  required: boolean;
  authenticated: boolean;
  onboarding_needed: boolean;
  shared_token_required: boolean;
  user: { username: string; role: string } | null;
} | null;

// Invite-accept route (auth slice 2 web, design §7): `/#/accept-invite?code=<raw>` must be
// reachable while logged out. Checked directly against the raw hash rather than through
// `routeFromHash`/`Route` (below), which only models the five authenticated app views and would
// otherwise fold an unrecognised hash into the dashboard fallback.
function isAcceptInviteHash(hash: string): boolean {
  return /^#\/?accept-invite(\?|$)/.test(hash);
}

export function App() {
  // Auth gate (Task 10). Kept as plain hooks at the top, unconditionally called on every render —
  // the gating itself happens ONLY in the final JSX below (never via an early `return` mid-function),
  // because every other hook in this component is declared further down: an early return here would
  // make this component call a different NUMBER of hooks between the "gated" renders (auth
  // unresolved/onboarding/login) and the "app" render (all hooks below reached) — a Rules-of-Hooks
  // violation ("Rendered more hooks than during the previous render") that would throw exactly when
  // onboarding/login completes and the app tries to render for the first time.
  const [auth, setAuth] = useState<AuthState>(null);
  const refreshAuth = useCallback(async () => {
    try {
      const r = await apiFetch("/api/auth");
      setAuth(await r.json());
    } catch {
      /* leave auth as-is; the next poll tick or user action can retry */
    }
  }, []);
  useEffect(() => {
    refreshAuth();
  }, [refreshAuth]);
  // Central 401 handler (Task 11): apiFetch already clears the (now-invalid) token on any 401 —
  // this just re-resolves auth, which falls back to <Login/> once `auth.authenticated` flips false.
  useEffect(() => {
    setUnauthorizedHandler(() => refreshAuth());
    return () => setUnauthorizedHandler(null);
  }, [refreshAuth]);

  const [status, setStatus] = useState<Status | null>(null);
  const [freshness, setFreshness] = useState<FreshnessMap | null>(null);
  const [deviceHealth, setDeviceHealth] = useState<DeviceHealth | null>(null);
  const [story, setStory] = useState<EnergyStoryData | null>(null);
  const [batteryPlan, setBatteryPlan] = useState<
    BatteryPlanData | null
  >(null);
  const [strategy, setStrategy] = useState<Strategy | null>(null);
  const [battery, setBattery] = useState<Battery | null>(null);
  // Which per-tower breakdown is open (if any): "soc" from the Battery level tile, "power" from the
  // Battery (power) tile. Both open the same modal, emphasising the metric you clicked.
  const [batteryDetail, setBatteryDetail] = useState<"soc" | "power" | null>(null);
  const [decision, setDecision] = useState<Decision | null>(null);
  const [alertsData, setAlertsData] = useState<AlertsResp | null>(null);
  const [chargeNeed, setChargeNeed] = useState<ChargeNeed | null>(null);
  const [reserveAdvice, setReserveAdvice] = useState<ReserveAdvice | null>(null);
  // B-03b: MEASURED (from /api/finance), not a plan estimate — null until the first successful
  // fetch (OutcomeTiles show "—" / measuring; a later failure keeps the last-known value,
  // same best-effort convention as the other polled cards below).
  const [savedToday, setSavedToday] = useState<SavedToday | null>(null);
  const [report, setReport] = useState<Report | null>(null);
  const emptyFreshness = (): TileFreshness => ({ updatedAt: null, stale: true });
  const [tileFreshness, setTileFreshness] = useState({
    report: emptyFreshness(), status: emptyFreshness(), finance: emptyFreshness(),
  });
  const batteryFreshness = useRef<string | null>(null);
  // True while the latest poll concluded EMS is unreachable — blocks late fills of stale alerts/hero.
  const unreachableRef = useRef(false);
  const [error, setError] = useState<string | null>(null);
  // B-09: wall-clock of the last successful dashboard/status contact — drives "Laatst bekend".
  // Null until a successful contact (cold fail → "Laatst bekend —", never a fabricated time).
  const [lastReachableAt, setLastReachableAt] = useState<number | null>(null);
  const [route, setRoute] = useState<Route>(() => routeFromHash(window.location.hash));
  const view = route.view;
  const manageTab = route.tab;
  // Which Settings section a deep-link asked to open (System's "solar" health action → "planner",
  // Car's "Manage → Settings → Car" → "ev") — runtime state only, never part of the hash (the
  // canonical hash stays #manage/settings; CLAUDE.md: keep it simple). Reset whenever navigate()
  // is called without one, so a plain nav click never leaves a stale section behind.
  const [settingsSection, setSettingsSection] = useState<string | undefined>(undefined);
  const [homeMoreOpen, setHomeMoreOpen] = useState<boolean>(() => {
    try {
      return localStorage.getItem("ems.dash.homeMoreOpen") === "1";
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
    apiFetch("/api/settings")
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

  // The day report is deliberately low-frequency: calculating it touches historical storage.
  // Its last successful value remains useful, while /api/freshness supplies the later stale signal.
  useEffect(() => {
    let alive = true;
    // Merge: keep the calm-dashboard's tile-freshness tracking, but route through apiFetch so the
    // read carries the bearer token / central 401 handling (auth slice 1).
    apiFetch("/api/report?period=day")
      .then((r) => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); })
      .then((v: Report) => { if (alive) {
        setReport(v);
        setTileFreshness((current) => ({ ...current, report: { updatedAt: Date.now(), stale: false } }));
      } })
      .catch(() => { if (alive) setTileFreshness((current) => ({
        ...current, report: { ...current.report, stale: true },
      })); });
    return () => { alive = false; };
  }, []);

  useEffect(() => {
    try {
      localStorage.setItem("ems.dash.homeMoreOpen", homeMoreOpen ? "1" : "0");
    } catch {
      /* private-mode / storage-disabled: the toggle still works in-session */
    }
  }, [homeMoreOpen]);

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
  // bare `navigate("manage")` opens the Settings sub-tab, matching #manage. `section` is a Settings
  // deep-link (e.g. "planner", "ev") — runtime-only, never encoded in the hash; omitting it resets
  // any section a previous deep-link left pending.
  function navigate(next: ViewName, tab: ManageTab = "settings", section?: string) {
    const hash = next === "manage" && tab !== "settings" ? `manage/${tab}` : next;
    if (window.location.hash.replace(/^#\/?/, "") !== hash) {
      window.location.hash = hash;
    }
    setRoute({ view: next, tab: next === "manage" ? tab : route.tab });
    setSettingsSection(section);
  }

  useEffect(() => {
    // Only poll the dashboard endpoints while the dashboard is visible — Settings has no need
    // for them and System runs its own poll. Avoids wasted load and a cross-tab error banner.
    if (view !== "dashboard") return;
    let alive = true;
    async function getJson(url: string) {
      // Global 401 handler (Task 11): apiFetch clears the (now-invalid) token and notifies the
      // central handler (registered above), which re-resolves auth and falls back to <Login/>.
      const r = await apiFetch(url);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      return r.json();
    }
    // Each card updates as its own endpoint resolves — a slow/flaky one (e.g. the battery) never
    // blocks or blanks the rest. Liveness hinges on /api/status: only its failure shows the banner.
    function fill<T>(url: string, apply: (v: T) => void, failed?: () => void) {
      getJson(url).then((v) => { if (alive) apply(v); }).catch(() => { if (alive) failed?.(); });
    }
    function poll() {
      const applyCore = (value: {
        status: Status;
        freshness: FreshnessMap;
        alerts: AlertsResp;
        device_health?: DeviceHealth | null;
      }) => {
        unreachableRef.current = false;
        setStatus(value.status);
        setFreshness(value.freshness);
        batteryFreshness.current = value.freshness.battery ?? null;
        setAlertsData(value.alerts);
        if (value.device_health) setDeviceHealth(value.device_health);
        setError(null);
        setLastReachableAt(Date.now());
        setTileFreshness((current) => ({
          ...current,
          status: { updatedAt: Date.now(), stale: batteryFreshness.current === "stale" },
        }));
        if (value.freshness.battery === "stale" || value.freshness.battery === "missing") {
          setTileFreshness((current) => ({ ...current, status: { ...current.status, stale: true } }));
        }
      };
      getJson("/api/dashboard")
        .then((value: {
          status: Status;
          freshness: FreshnessMap;
          alerts: AlertsResp;
          device_health?: DeviceHealth | null;
        }) => {
          if (alive) applyCore(value);
        })
        .catch((snapshotError) => {
          // Older EMS servers do not expose the snapshot yet; retain the proven fan-out path.
          getJson("/api/status")
            .then((v) => {
              if (alive) {
                unreachableRef.current = false;
                setStatus(v);
                setError(null);
                setLastReachableAt(Date.now());
              }
            })
            .catch((e) => { if (alive) {
              unreachableRef.current = true;
              setError(String(e ?? snapshotError));
              // Cold fail: leave lastReachableAt null → "Laatst bekend —" (never invent a time).
              // Demote stale live state so old alerts/hero aren't shown as current under the banner.
              setAlertsData(null);
              setDecision(null);
              setTileFreshness((current) => ({ ...current, status: { ...current.status, stale: true } }));
            } });
          fill("/api/freshness", (value: FreshnessMap) => {
            setFreshness(value);
            batteryFreshness.current = value.battery ?? null;
          });
          fill("/api/device-health", (value: DeviceHealth) => setDeviceHealth(value));
          // Older servers / snapshot-unavailable: still fan out /api/alerts. Skip applying if
          // status also failed (unreachable) so stale alerts never sit under the outage banner.
          fill("/api/alerts", (value: AlertsResp) => {
            if (!unreachableRef.current) setAlertsData(value);
          });
        });
      fill("/api/energy-story?window=next", setStory);
      fill("/api/battery-plan", setBatteryPlan);
      fill("/api/strategy", (v: Strategy) => { if (!strategyPending.current) setStrategy(v); });
      fill("/api/battery", setBattery);
      fill("/api/decision", (v: Decision) => {
        if (!unreachableRef.current) setDecision(v);
      });
      // B-03b: the measured figure, not the old plan-estimate tile — never a fake €0.00. finance's
      // totals.saved_eur is null until a day of prices has been recorded, in which case OutcomeTiles
      // show measuring (—) instead of inventing a number.
      fill(
        `/api/finance?period=day&date=${todayStr()}`,
        (v: { totals?: { saved_eur: number | null } }) => {
          const eurVal = v?.totals?.saved_eur;
          setSavedToday(eurVal == null ? { status: "measuring" } : { status: "measured", eur: eurVal });
          setTileFreshness((current) => ({
            ...current,
            finance: eurVal == null ? current.finance : { updatedAt: Date.now(), stale: false },
          }));
        },
        () => setTileFreshness((current) => ({ ...current, finance: { ...current.finance, stale: true } })),
      );
      fill(
        "/api/charge-need",
        (v: ChargeNeed) => setChargeNeed(isUsableChargeNeed(v) ? v : null),
        () => setChargeNeed(null),
      );
      fill(
        "/api/advisor/reserve",
        (v: { advice?: ReserveAdvice | null }) => {
          const a = v?.advice;
          setReserveAdvice(
            a &&
              typeof a.recommended_soc_pct === "number" &&
              typeof a.current_night_reserve_kwh === "number" &&
              typeof a.reason === "string"
              ? a
              : null,
          );
        },
        () => setReserveAdvice(null),
      );
    }
    poll();
    const id = setInterval(poll, POLL_MS);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, [view]);

  // Save a strategy setting live: apply an optimistic patch (instant, self-consistent card), POST
  // it, then reconcile from the server — reverting if the write fails. The pending guard stops the
  // 5 s poll clobbering the change mid-flight.
  async function patchStrategy(body: Record<string, unknown>, optimistic: Partial<Strategy>) {
    const prev = strategy;
    strategyPending.current = true;
    setStrategy((s) => (s ? { ...s, ...optimistic } : s));
    try {
      const res = await apiFetch("/api/settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const r = await apiFetch("/api/strategy");
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

  // --- Hero synthesis (B-32 + B-96): one verdict, one plain sentence, one act-line. ------------
  const home = decision?.home_state ?? null;
  const summary = report ? homeSummary(report.scores) : null;
  // B-68: the plan-confidence score rides on the already-polled /api/battery-plan response — no
  // extra fetch. Calm stays calm: the reason sub-line only renders when confidence isn't high.
  const confidence = batteryPlan?.confidence ?? null;
  // B-96: one human sentence only — planner jargon stays under Waarom? / DecisionReasonDetails.
  const synthesis = buildHeroSynthesis({
    currentReason: batteryPlan?.current_reason,
    onTrackMessage: story?.on_track?.message,
    scoreSummary: summary?.text,
  });
  // B-98 fix: confidence reason under the chip is Dutch-mapped; hide unmapped EN API copy.
  const confidenceReasonNl = confidence?.reasons?.[0]
    ? homeConfidenceReasonNl(confidence.reasons[0])
    : null;
  const confidenceTitleNl = confidence
    ? confidence.reasons
        .map((r) => homeConfidenceReasonNl(r))
        .filter((r): r is string => !!r)
        .join(" ")
    : "";
  // B-95: one evening-peak surface — covered → hero trust-marker; at-risk → risk banner
  // only (strip the API's discharge-based "covers" chip so it never stacks with amber).
  const eveningPeakCoverage = batteryPlan?.evening_peak_coverage;
  const eveningPeakCovered = isEveningPeakCovered(eveningPeakCoverage?.probability);
  const trustMarkers = applyEveningPeakTrustMarkerPolicy(
    (story?.trust_markers ?? []).filter(
      (marker) =>
        !(story?.on_track?.status === "behind" && marker === "No grid top-up needed"),
    ),
    eveningPeakCoverage,
    { injectCoveredMarker: !!home && eveningPeakCovered },
  );
  // Hero owns the covered marker whenever the hero section can render (home present).
  const heroOwnsEveningPeakMarker = !!home && eveningPeakCovered;

  // "Do I need to act?" — answered explicitly. Nothing to do unless an override is running, the
  // system is on unsafe data (self-use failsafe), or a warning/critical alert is live. Info
  // notes (e.g. watch-only) are calm by design and never raise the act line. Never claim
  // "safe mode" without a confirmed AUTO (B-09 / #73).
  const alerts = alertsData?.alerts ?? [];
  const topActionable = [...alerts]
    .filter((a) => a.severity === "warning" || a.severity === "critical")
    .sort((a, b) => (SEVERITY_RANK[b.severity] ?? 0) - (SEVERITY_RANK[a.severity] ?? 0))[0];
  const actLine: { text: string; calm: boolean } = decision?.override_active
    ? { text: HOME_ACT.override, calm: false }
    : alertsData?.data_quality === "unsafe"
      ? {
          text: HOME_ACT.unsafe,
          calm: false,
        }
      : topActionable
        ? { text: topActionable.action || topActionable.message, calm: false }
        : { text: HOME_ACT.calm, calm: true };

  // B-57: on demo/mock data, a persistent friendly nudge into real onboarding (Settings opens on
  // the Connection section by default). Dismissible for the session; back on next visit.
  const demoActive = !!home?.simulated && !demoDismissed;

  // Auth gate (Task 10). Placed here — AFTER every hook above has already been called
  // unconditionally on every render — rather than at the top of the component: an early return
  // BEFORE the rest of this component's hooks would make React call a different number of hooks
  // between the gated renders and the first "app" render, which throws (Rules of Hooks). Putting
  // the branch here, once all hooks are done, is the safe equivalent.
  if (auth === null) return null; // splash while the first GET /api/auth is in flight
  if (auth.onboarding_needed) {
    return <Onboarding sharedTokenRequired={auth.shared_token_required} onDone={refreshAuth} />;
  }
  // Reachable while logged out (BEFORE the login gate below), like Onboarding — but onboarding
  // still wins when both are true (a fresh install has no invites to accept yet anyway).
  if (isAcceptInviteHash(window.location.hash)) {
    return <AcceptInvite onDone={refreshAuth} />;
  }
  if (!auth.authenticated) return <Login onDone={refreshAuth} />;

  // Reader read-only mode + admin panel gating (auth slice 2 web, design §7): a reader gets every
  // OPERATE control hidden/disabled (mirrors the API's 403); an admin additionally sees the
  // Settings → Access & security panel. An unknown/missing role defaults to `canOperate: true` —
  // permissive, so a role-less legacy shape can't silently lock everyone out of controls that
  // worked before this slice.
  const role = auth.user?.role ?? null;
  const canOperate = role !== "reader";
  const isAdmin = role === "admin";

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
            data-cause={status.dry_run_cause ?? undefined}
            title={
              status.dry_run_reason
                || (status.dry_run ? RUN_MODE.dry.title : RUN_MODE.live.title)
            }
          >
            {status.dry_run ? RUN_MODE.dry.label : RUN_MODE.live.label}
          </span>
        )}
        {status?.dry_run_reason && (
          <span
            className="badge badge-muted run-mode-reason"
            data-testid="run-mode-reason"
            title={status.dry_run_reason}
          >
            {status.dry_run_cause
              ? (DRY_RUN_CAUSE_LABEL[status.dry_run_cause] ?? status.dry_run_cause)
              : DRY_RUN_CAUSE_FALLBACK}
          </span>
        )}
        {/* B-99: demote redundant "Live sensoren"; keep Demo/mock as attention. */}
        {status && showTopbarDataSource(status.dev_mode) && (
          <span
            className="badge badge-muted"
            data-testid="data-source"
            title={status.dev_mode === "live" ? DATA_SOURCE.live.title : DATA_SOURCE.sim.title}
          >
            {status.dev_mode === "live" ? DATA_SOURCE.live.label : DATA_SOURCE.sim.label}
          </span>
        )}
        {/* B-99: data-quality chip only when not complete (DeviceHealth covers "Alles actueel"). */}
        {alertsData && showTopbarDataQuality(alertsData.data_quality) && (
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
        <NotificationBell canOperate={canOperate} />
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
          <button
            className={`nav-btn${view === "trading" ? " nav-active" : ""}`}
            onClick={() => navigate("trading")}
            data-testid="nav-trading"
            aria-current={view === "trading" ? "page" : undefined}
          >
            Trading
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

      {/* Single <main> landmark wrapping all routed view content (a11y: landmark-one-main + region).
          Plain block with no box styling, so the calm-dashboard card spacing is unchanged. */}
      <main className="app-main">

      {/* Server alerts only while reachable — never above the unreachable banner as "live". */}
      {!error && alertsData && alertsData.alerts.length > 0 && (
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
                {/* B-37 / B-09 structured sub-lines — only for warning/critical. Info-level
                    notices (watch-only, dry-run) stay one calm line; their reassurance would
                    otherwise out-shout the hero's "Nothing needed from you." */}
                {a.severity !== "info" && a.ems_doing && (
                  <span className="alert-ems-doing" data-testid="alert-ems-doing">
                    {a.ems_doing}
                  </span>
                )}
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

      {/* B-09 / #73: unreachable EMS uses the same alert-item anatomy (message + failsafe +
          Laatst bekend) — never a bare "Cannot reach EMS API: …" error code. */}
      {view === "dashboard" && error && (
        <section className="alerts" data-testid="error">
          <div
            className="alert-item alert-critical"
            data-severity="critical"
            data-testid="alert-ems_unreachable"
            title={error}
          >
            <span className="alert-message">{EMS_UNREACHABLE.message}</span>
            <span className="alert-ems-doing" data-testid="alert-ems-doing">
              {EMS_UNREACHABLE.ems_doing}
            </span>
            <span className="alert-action" data-testid="alert-laatst-bekend">
              {formatLaatstBekend(lastReachableAt)}
            </span>
          </div>
        </section>
      )}

      {/* The hero: one verdict, one synthesis line, one explicit answer to "do I need to act?".
          Absorbs the old status banner + the scattered on-track/score copy into a single read.
          Hidden while unreachable so a stale "Nothing needed" can't sit under the outage banner. */}
      {view === "dashboard" && !error && home && (
        <section
          className={`hero home-${home.tone}`}
          data-testid="home-state"
          data-density-kind="hero"
          data-tone={home.tone}
        >
          <div className="hero-verdict-row">
            <p className="hero-verdict" data-testid="hero-verdict">
              {homeHeadlineNl(home.headline)}
            </p>
            {confidence && (
              <span
                className={`badge confidence-chip confidence-${confidence.level}`}
                data-testid="confidence-chip"
                data-density-kind="badge"
                data-level={confidence.level}
                title={confidenceTitleNl || undefined}
              >
                {CONFIDENCE_CHIP_LABEL[confidence.level]}
              </span>
            )}
          </div>
          {confidence && confidence.level !== "high" && confidenceReasonNl && (
            <p className="hero-confidence-reason" data-testid="hero-confidence-reason">
              {confidenceReasonNl}
            </p>
          )}
          {synthesis && (
            <p className="hero-synthesis" data-testid="hero-synthesis">
              {synthesis}
            </p>
          )}
          {trustMarkers.length > 0 && (
            <div className="trust-markers" data-testid="trust-markers">
              {trustMarkers.map((marker) => (
                <span key={marker} className="trust-marker">
                  <Icon name="check" /> {marker}
                </span>
              ))}
            </div>
          )}
          <p
            className={`hero-act ${actLine.calm ? "hero-act-calm" : "hero-act-attention"}`}
            data-testid="hero-act"
          >
            {!actLine.calm && <Icon name="alert" className="hero-act-icon" />}
            {actLine.text}
          </p>
          {demoActive && (
            <div className="hero-demo-cta" data-testid="demo-cta">
              <span>
                {HOME_HERO.demoLead}{" "}
                <button
                  type="button"
                  className="hero-demo-link"
                  data-testid="demo-cta-link"
                  onClick={() => navigate("manage", "settings")}
                >
                  {HOME_HERO.demoLink}
                </button>
              </span>
              <button
                type="button"
                className="hero-demo-dismiss"
                data-testid="demo-cta-dismiss"
                aria-label={HOME_HERO.demoDismissAria}
                onClick={dismissDemoCta}
              >
                ×
              </button>
            </div>
          )}
        </section>
      )}

      {/* Issue #79 / B-94: DeviceHealth — full strip when attention needed; quiet one-line
          disclosure when healthy so the first viewport keeps room for tiles + PlanStory. */}
      {view === "dashboard" && !error && (freshness || deviceHealth) && (
        <DeviceHealthStrip
          freshness={freshness}
          deviceHealth={deviceHealth}
          isDemo={status?.dev_mode !== "live" || !!home?.simulated}
          alertsForSource={(key) => {
            const hit = pickDeviceHealthAlert(key, alertsData?.alerts);
            if (!hit) return null;
            return { message: hit.message, ems_doing: hit.ems_doing, action: hit.action };
          }}
        />
      )}

      {view === "manage" && (
        <Manage
          tab={manageTab}
          onTab={(t, section) => navigate("manage", t, section)}
          onSettingsSaved={(v) => setTheme((v["ui.theme"] as Theme) ?? "auto")}
          settingsSection={settingsSection}
          canOperate={canOperate}
          isAdmin={isAdmin}
          identityAuth={role !== null}
        />
      )}

      {view === "insights" && <Insights canOperate={canOperate} />}

      {view === "chat" && <ChatPanel canOperate={canOperate} />}

      {view === "trading" && <TradingView canOperate={canOperate} />}

      {view === "car" && (
        // Lands on the Car section of Settings (feat/ux-batch-3's "ev" group) rather than just the
        // Settings tab in general — the schedule/picker used to live there.
        <CarView onOpenSettings={() => navigate("manage", "settings", "ev")} canOperate={canOperate} />
      )}

      {view === "dashboard" && (
        <>
          <OutcomeTiles
            report={report}
            savedToday={savedToday}
            socPct={status?.soc_pct ?? null}
            onOpenInsights={() => navigate("insights")}
            onOpenFinance={() => navigate("insights")}
            onOpenBattery={batteryHasDetail ? () => setBatteryDetail("soc") : undefined}
            freshness={tileFreshness}
          />
          {/* B-101 / B-87: PlanStory is the first major visual after tiles — Nu/Waarom and
              evening-peak sit below the chart, not between tiles and chart. */}
          {/* B-97: SoC / Saved live in OutcomeTiles only — PlanStory no longer repeats them. */}
          <PlanStory
            story={story?.window === "next" ? story : null}
            provenance={batteryPlan?.provenance}
            recentReview={story?.recent_review?.message ?? null}
          />
          {/* B-33 / #85: waarom voor batterij-acties + pauze/reserve (slice 1–2) — één tik, niet
              standaard open. Tekst uit battery-plan `reason` (#84), dry-run zegt "zou". */}
          <BatteryActionWhy
            currentAction={batteryPlan?.current_action}
            reason={batteryPlan?.reason as DecisionReason | undefined}
            dryRun={status?.dry_run ?? true}
            overrideActive={Boolean(decision?.override_active)}
          />
          {/* B-63 / #88 + B-95: risk banner only when coverage is below the covered
              threshold; covered/100% is the hero trust-marker (one surface). */}
          <EveningPeakCoverageCard
            coverage={batteryPlan?.evening_peak_coverage}
            heroOwnsCoveredMarker={heroOwnsEveningPeakMarker}
          />
        </>
      )}

      {view === "dashboard" && (
        <details
          className="home-more"
          data-testid="home-more"
          data-density-kind="disclosure"
          open={homeMoreOpen}
          onToggle={(event) => setHomeMoreOpen(event.currentTarget.open)}
        >
          <summary className="home-more-toggle" data-testid="home-more-toggle" aria-expanded={homeMoreOpen}>
            {HOME_MORE_TOGGLE}
          </summary>
          <div className="home-more-body" data-testid="home-more-body">
            <HomeScores
              report={report}
              onOpenDetail={() => navigate("insights")}
              scoreKeys={["co2", "best_price"]}
            />
            {/* B-102: nest Strategy / Manual / Car — scannable, not a flat dump. */}
            {strategy && (
              <HomeMoreNest kind="strategy" label={HOME_MORE_NEST.strategy}>
                <StrategyCard
                  strategy={strategy}
                  onChange={setStrategyMode}
                  onSetGridTopup={setGridTopup}
                  onTune={() => navigate("manage", "settings")}
                  canOperate={canOperate}
                />
              </HomeMoreNest>
            )}
            {status && (
              <HomeMoreNest kind="manual" label={HOME_MORE_NEST.manual}>
                <OverrideCard dataQuality={alertsData?.data_quality} canOperate={canOperate} />
              </HomeMoreNest>
            )}
            {status && (
              <HomeMoreNest kind="car" label={HOME_MORE_NEST.car}>
                <CarCard compact onOpenCar={() => navigate("car")} canOperate={canOperate} />
              </HomeMoreNest>
            )}
            {status && (
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
          {reserveAdvice && <NightReserveAdvice advice={reserveAdvice} />}

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

          <AiValidationCard canOperate={canOperate} />

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
          </div>
        </details>
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
      </main>
      </div>
    </>
  );
}
