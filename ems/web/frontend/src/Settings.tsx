import { useEffect, useState } from "react";

import { AccountTokens } from "./AccountTokens";
import { AdminAccess } from "./Admin";
import { apiFetch, clearToken } from "./auth";
import { type CarModel, type CarsResp } from "./ev";
import { humanize } from "./labels";
import { SectionIcon } from "./settingsIcons";

export type SettingField = {
  key: string;
  label: string;
  type: "number" | "int" | "bool" | "enum" | "text" | "secret";
  default: number | boolean | string;
  group: string;
  help: string;
  min: number | null;
  max: number | null;
  options: string[] | null;
  step: number | null;
  unit: string;
  advanced: boolean;
  applies: "live" | "restart";
  slider?: boolean;
};
type SettingsResp = { schema: SettingField[]; values: Record<string, number | boolean | string> };
type Values = Record<string, number | boolean | string>;
type PlanMetrics = {
  summary: string;
  savings_eur: number;
  charge_slots: number;
  discharge_slots: number;
};
type Impact = { current: PlanMetrics | null; proposed: PlanMetrics | null };
// Evidence-based advisory for `planner.solar_confidence` (GET /api/advisor/solar-confidence) —
// never applied automatically; rendered as a hint under that field, the user decides.
type SolarConfidenceAdvice = {
  recommended_pct: number;
  n_slots: number;
  median_ratio_pct: number;
  p25_ratio_pct: number;
  current_pct: number | null;
  delta_pct: number | null;
  // Production hardening: the p25 suggestion flips day-to-day when the forecast bias is noisy.
  // When the backend reports `stable === false` the hint HOLDS — it explains it's still settling
  // and offers no Apply, so the user isn't sent chasing a number that changes next week. Absent
  // (older payload) reads as "not explicitly unstable" and the Apply flow is unchanged.
  stable?: boolean;
  spread_pp?: number | null;
  window_days?: number;
};

// Two-pane menu: sidebar sections grouped under three intent headers. This order REPLACES the old
// flat GROUP_ORDER for navigation; any unknown/future group appends under "App" (see below).
const NAV_GROUPS: { header: string; sections: string[] }[] = [
  { header: "Your setup", sections: ["connection", "meters", "battery", "prices", "site"] },
  { header: "How it runs", sections: ["strategy", "planner", "control", "ev"] },
  { header: "App", sections: ["ai", "reporting", "notify", "access", "ui"] },
];
const GROUP_TITLE: Record<string, string> = {
  strategy: "Strategy",
  connection: "Connection",
  meters: "Energy meters (HomeWizard)",
  battery: "Battery (Indevolt)",
  prices: "Electricity prices (Tibber)",
  site: "Solar & location",
  control: "Control & safety",
  planner: "Planner economics",
  ai: "AI explanations & chat",
  access: "Access & security",
  ui: "Appearance",
  reporting: "Insights & reporting",
  notify: "Notifications",
  ev: "Car",
};
const GROUP_HINT: Record<string, string> = {
  strategy: "How the battery is run. The rest is fine on defaults — tune only if you want to.",
  connection: "Read your real devices, or run the built-in simulator.",
  meters: "Local IP addresses of your HomeWizard meters.",
  battery: "Battery address, capacity and reserves.",
  prices: "Your Tibber token for live day-ahead prices.",
  site: "Location & array — these drive the solar forecast.",
  control: "Safety limits applied to the battery mode controller.",
  planner: "The arbitrage maths — the plan recomputes from these immediately.",
  ai: "Optional. Off by default. Turn on to get natural-language explanations and the chat — a tiny, "
    + "redacted summary is sent to MiniMax; never your address, history or tokens.",
  access: "Optional. Set a token to require it for saving/control. Then enter the same token in the "
    + "Access box at the top of this page to authorise this browser. Blank = open on your LAN.",
  ui: "How the dashboard looks.",
  reporting: "CO₂ accounting factors and the gas price used by the Insights tab.",
  notify: "Optional. Off by default. Get a real push to your phone via ntfy (ntfy.sh or "
    + "self-hosted) — no Apple/Google account, no cloud subscription. The bell in the header always "
    + "shows notifications in-app regardless.",
  ev: "Optional. Off by default. Shows a dashboard card suggesting the cheapest window to plug "
    + "in the car — advisory only, the EMS never controls the car. Schedule & car choice live in "
    + "the Car tab.",
};

// Fields moved OUT of Settings into the Car tab: the weekly schedule, the car picker, and the
// battery capacity the picker autofills (feat/ux-batch-3); plus, since feat/car-charge-modes, the
// "while the car charges" battery-mode master switch, mode picker and discharge wattage — now their
// own dedicated radio-card section there (see Car.tsx), immediate-save rather than the sticky
// save-bar draft flow the rest of Settings uses. Settings still loads their VALUES (the AC hint
// below reads the chosen car), it just doesn't render them as editable fields here.
const CAR_TAB_KEYS = new Set([
  "ev.schedule", "ev.car_id", "ev.battery_kwh",
  "control.hold_battery_when_car_charging", "control.car_charging_battery_mode",
  "control.car_discharge_w",
]);

// Legacy shared-token knobs, DEPRECATED once identity auth (users/roles) is active: require_auth is
// implicitly always-on and the shared token is migrated into an access token at onboarding (design
// §8). They stay in the backend schema for compat, but the UI hides them from the "access" section
// so they aren't mistaken for live controls. They are filtered at RENDER (not out of `schema`), so
// the "access" section itself stays in the nav (its admin Users/Invites panel + logout live there).
const LEGACY_SHARED_TOKEN_KEYS = new Set(["web.auth_token", "web.require_auth"]);

// First sentence of the help (always shown) + the remainder (behind a "More" disclosure). Splits
// on the FIRST sentence-ending punctuation that is followed by whitespace, so mid-word dots
// (developer.tibber.com, MiniMax-M2.7) never split the text.
function splitHelp(help: string): { first: string; rest: string } {
  const m = help.match(/^([\s\S]*?[.!?])(\s+)([\s\S]+)$/);
  if (!m) return { first: help.trim(), rest: "" };
  return { first: m[1].trim(), rest: m[3].trim() };
}

function FieldHelp({ help }: { help: string }) {
  const { first, rest } = splitHelp(help);
  const [open, setOpen] = useState(false);
  if (!rest) return <p className="field-help">{first}</p>;
  return (
    <p className="field-help">
      {first}
      {open ? ` ${rest}` : ""}{" "}
      <button
        type="button"
        className="help-more"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        {open ? "Less" : "More"}
      </button>
    </p>
  );
}

function NumberInput({
  field,
  value,
  disabled,
  onChange,
}: {
  field: SettingField;
  value: number;
  disabled: boolean;
  onChange: (v: number) => void;
}) {
  // Hold the raw text locally so the user can transiently clear/retype without it snapping to 0.
  const [raw, setRaw] = useState(String(value));
  useEffect(() => {
    setRaw(String(value));
  }, [value]);
  // A drag slider (opt-in via field.slider, needs a min+max) — easier to dial than typing, with a
  // live numeric read-out beside it since a range input shows no value of its own.
  if (field.slider && field.min != null && field.max != null) {
    return (
      <div className="slider-row">
        <input
          id={`set-${field.key}`}
          type="range"
          className="slider"
          value={value}
          disabled={disabled}
          min={field.min}
          max={field.max}
          step={field.step ?? 1}
          onChange={(e) => onChange(Number(e.target.value))}
        />
        <output className="slider-value" htmlFor={`set-${field.key}`}>
          {value}
          {field.unit ? ` ${field.unit}` : ""}
        </output>
      </div>
    );
  }
  return (
    <input
      id={`set-${field.key}`}
      type="number"
      value={raw}
      disabled={disabled}
      min={field.min ?? undefined}
      max={field.max ?? undefined}
      step={field.step ?? (field.type === "int" ? 1 : "any")}
      onChange={(e) => setRaw(e.target.value)}
      onBlur={(e) => {
        const n = e.target.value === "" ? (field.min ?? 0) : Number(e.target.value);
        const coerced = field.type === "int" ? Math.round(n) : n;
        setRaw(String(coerced));
        onChange(coerced);
      }}
    />
  );
}

function Field({
  field,
  value,
  error,
  disabled,
  secretSet,
  onChange,
}: {
  field: SettingField;
  value: number | boolean | string;
  error?: string;
  disabled: boolean;
  secretSet?: boolean;
  onChange: (v: number | boolean | string) => void;
}) {
  const id = `set-${field.key}`;
  const label = (
    <label htmlFor={id} className="field-label">
      {field.label}
      {field.unit && <span className="field-unit"> ({field.unit})</span>}
      {field.applies === "restart" && <span className="field-badge">restart</span>}
    </label>
  );
  // Booleans render as a proper toggle switch, laid out as a row (label left, switch right). It's
  // still an <input type=checkbox> under the hood (role=switch), so it stays keyboard-togglable.
  if (field.type === "bool") {
    return (
      <div className={`field field-bool${error ? " field-error" : ""}`} data-testid={`field-${field.key}`}>
        <div className="field-bool-row">
          {label}
          <input
            id={id}
            className="switch-input"
            type="checkbox"
            role="switch"
            checked={Boolean(value)}
            disabled={disabled}
            onChange={(e) => onChange(e.target.checked)}
          />
        </div>
        {field.help && <FieldHelp help={field.help} />}
        {error && <p className="field-err" data-testid={`err-${field.key}`}>{error}</p>}
      </div>
    );
  }
  let control;
  if (field.type === "enum") {
    control = (
      <select id={id} value={String(value)} disabled={disabled}
        onChange={(e) => onChange(e.target.value)}>
        {(field.options ?? []).map((o) => (
          // Humanise the raw token for display; the submitted VALUE stays the token.
          <option key={o} value={o}>{humanize(o)}</option>
        ))}
      </select>
    );
  } else if (field.type === "text" || field.type === "secret") {
    control = (
      <input
        id={id}
        type={field.type === "secret" ? "password" : "text"}
        value={String(value)}
        disabled={disabled}
        placeholder={field.type === "secret" && secretSet ? "•••• set (leave blank to keep)" : ""}
        autoComplete={field.type === "secret" ? "new-password" : "off"}
        onChange={(e) => onChange(e.target.value)}
      />
    );
  } else {
    control = (
      <NumberInput field={field} value={Number(value)} disabled={disabled} onChange={onChange} />
    );
  }
  return (
    <div className={`field${error ? " field-error" : ""}`} data-testid={`field-${field.key}`}>
      {label}
      {control}
      {field.help && <FieldHelp help={field.help} />}
      {error && (
        <p className="field-err" data-testid={`err-${field.key}`}>{error}</p>
      )}
    </div>
  );
}

// Rendered under `planner.solar_confidence` only, when 14 days of forecast-vs-actual evidence
// support a recommendation. The sentence itself never touches the field's value (B-60): adopting
// the suggestion is an explicit "Apply" tap that calls the SAME set() the field's own control
// uses — it makes the section dirty and raises the existing sticky save bar, so Save is the one
// confirm and Discard cancels it, exactly like any other settings edit (audit-logged server-side,
// reversible, never automatic).
function SolarConfidenceHint({
  advice,
  currentPct,
  applied,
  disabled,
  onApply,
}: {
  advice: SolarConfidenceAdvice;
  currentPct: number;
  applied: boolean;
  disabled: boolean;
  onApply: (pct: number) => void;
}) {
  if (advice.stable === false) {
    // Still moving day-to-day — hold and explain, no Apply (matches the weekly digest gate).
    const spread =
      typeof advice.spread_pp === "number"
        ? `spread ${Math.round(advice.spread_pp)}pp over ${advice.window_days ?? 7} days`
        : "not enough daily history yet";
    return (
      <p className="advisor-hint" data-testid="advisor-solar-confidence">
        Based on {advice.n_slots} matched daytime slots over the last 14 days, the suggestion is
        still settling ({spread}) — holding until it settles rather than nudging you toward a number
        that keeps moving. You never need to act on this.
      </p>
    );
  }
  const matches = Math.abs(currentPct - advice.recommended_pct) < 0.01;
  return (
    <>
      <p className="advisor-hint" data-testid="advisor-solar-confidence">
        Based on {advice.n_slots} matched daytime slots over the last 14 days, forecasts delivered{" "}
        <strong>{advice.median_ratio_pct}%</strong> (typical) / <strong>{advice.p25_ratio_pct}%</strong>{" "}
        (disappointing quarter). Suggestion: <strong>{advice.recommended_pct}%</strong>. You decide —
        this is never applied automatically.
      </p>
      <div className="advisor-hint-action">
        {applied ? (
          <span className="advisor-hint-applied" data-testid="advisor-solar-confidence-applied">
            applied — save to confirm
          </span>
        ) : matches ? (
          <span className="advisor-hint-match" data-testid="advisor-solar-confidence-match">
            ✓ matches your setting
          </span>
        ) : (
          <button
            type="button"
            className="advisor-hint-apply"
            data-testid="advisor-solar-confidence-apply"
            aria-label={`Apply suggested solar confidence ${advice.recommended_pct} percent`}
            disabled={disabled}
            onClick={() => onApply(advice.recommended_pct)}
          >
            Apply {advice.recommended_pct}%
          </button>
        )}
      </div>
    </>
  );
}

export function Settings({
  onSaved,
  initialSection,
  canOperate = true,
  isAdmin = false,
  identityAuth = false,
}: {
  onSaved?: (values: Values) => void;
  // Deep-link: open this section on mount (e.g. System's "solar" health action → "planner", or
  // Car's "Manage → Settings → Car" → "ev"). Runtime state only — reuses the same openSection
  // machinery a sidebar click uses, so it needs no hash segment of its own (CLAUDE.md: keep the
  // canonical #manage/settings hash simple).
  initialSection?: string;
  // Reader read-only mode (auth slice 2 web): every field renders disabled and the save bar never
  // appears — mirrors the API's 403 on writes rather than inventing a new client-side rule.
  // Defaults true so every other caller (and every existing test) is unaffected.
  canOperate?: boolean;
  // Admin-only "Users" + "Invites" panel (design §7 Access & security). Defaults false.
  isAdmin?: boolean;
  // When identity auth (users/roles) is active, the legacy shared-token knobs (`web.auth_token`,
  // `web.require_auth`) are DEAD — require_auth is implicitly always-on and the shared token is
  // migrated away (design §8, deprecated). Hide them from the "access" section so they aren't
  // mistaken for live controls; the backend schema keeps them for compat. Defaults false (legacy
  // shared-token deployments and existing tests still show them).
  identityAuth?: boolean;
} = {}) {
  const [schema, setSchema] = useState<SettingField[] | null>(null);
  const [values, setValues] = useState<Values>({});
  const [edited, setEdited] = useState<Values>({});
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [status, setStatus] = useState<"idle" | "saving" | "saved" | "error">("idle");
  const [loadError, setLoadError] = useState<string | null>(null);
  const [auth, setAuth] = useState<{ required: boolean; authenticated: boolean } | null>(null);
  const [impact, setImpact] = useState<Impact | null>(null);
  const [solarAdvice, setSolarAdvice] = useState<SolarConfidenceAdvice | null>(null);
  // True right after the solar-confidence hint's "Apply" is tapped, until: the field is edited
  // manually again, the pending edit is discarded, or it's saved (B-60) — see SolarConfidenceHint.
  const [solarAdviceApplied, setSolarAdviceApplied] = useState(false);
  const [cars, setCars] = useState<CarModel[] | null>(null);
  // --- Two-pane shell navigation state ---
  const [activeSection, setActiveSection] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  // Which sections have their in-place "Advanced" disclosure expanded (collapsed by default).
  const [advancedOpen, setAdvancedOpen] = useState<Set<string>>(new Set());
  // Field flashed after a search-driven jump (brief highlight + scroll-into-view).
  const [highlightKey, setHighlightKey] = useState<string | null>(null);
  // Sections whose saved changes need a restart to take effect (client-side, this session).
  const [restartPending, setRestartPending] = useState<Set<string>>(new Set());
  const [lastSaveRestart, setLastSaveRestart] = useState(false);
  // Mobile drill-in (≤700px): start on the section list, then drill into one section.
  const [mobileList, setMobileList] = useState(true);

  // Keys never rendered as editable fields: the car-tab knobs (owned by Car.tsx) always, plus the
  // deprecated legacy shared-token knobs when identity auth is active (design §8).
  const hiddenFieldKeys = identityAuth
    ? new Set([...CAR_TAB_KEYS, ...LEGACY_SHARED_TOKEN_KEYS])
    : CAR_TAB_KEYS;

  async function refreshAuth() {
    try {
      const r = await apiFetch("/api/auth");
      if (r.ok) setAuth(await r.json());
    } catch {
      /* leave auth as-is */
    }
  }

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const r = await apiFetch("/api/settings");
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const b: SettingsResp = await r.json();
        if (!alive) return;
        setSchema(b.schema);
        setValues(b.values);
        setEdited(b.values);
      } catch (e) {
        if (alive) setLoadError(String(e));
      }
    })();
    refreshAuth();
    return () => {
      alive = false;
    };
  }, []);

  // Deep-link on mount: open the requested section straight away (no extra click), reusing the
  // exact openSection() a sidebar click would call — same mobileList/highlight behaviour. Runs
  // once; later navigation is owned by the user's own clicks, not this prop.
  useEffect(() => {
    if (initialSection) openSection(initialSection);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Best-effort advisory fetch — hide the hint entirely on error or when there's not yet enough
  // evidence (null), never surface it as a load error.
  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const r = await apiFetch("/api/advisor/solar-confidence");
        if (!r.ok) return;
        const b: { advice: SolarConfidenceAdvice | null } = await r.json();
        if (alive) setSolarAdvice(b.advice ?? null);
      } catch {
        /* best-effort — leave the hint hidden */
      }
    })();
    return () => {
      alive = false;
    };
  }, []);

  function set(key: string, v: number | boolean | string) {
    setEdited((prev) => ({ ...prev, [key]: v }));
    setStatus("idle");
  }

  // The car database is static (ems/cars.py) — fetched once, lazily, the first time the Car
  // section is opened (not on every Settings load, since most homes never open it).
  useEffect(() => {
    if (activeSection !== "ev" || cars !== null) return;
    let alive = true;
    (async () => {
      try {
        const r = await apiFetch("/api/cars");
        if (!r.ok) return;
        const b: CarsResp = await r.json();
        if (alive) setCars(b.cars);
      } catch {
        /* best-effort — the picker degrades to "Custom" only */
      }
    })();
    return () => {
      alive = false;
    };
  }, [activeSection, cars]);
  const selectedCar = (cars ?? []).find((c) => c.id === String(edited["ev.car_id"] ?? "")) ?? null;

  // Only send schema keys whose value actually changed (skip the "<key>.__set" secret flags).
  const schemaKeys = new Set((schema ?? []).map((f) => f.key));
  const changed: Values = {};
  for (const k of Object.keys(edited)) {
    if (schemaKeys.has(k) && edited[k] !== values[k]) changed[k] = edited[k];
  }
  const changeCount = Object.keys(changed).length;
  const dirty = changeCount > 0;
  // Did any changed field require a restart to take effect?
  const restartChanged = (schema ?? []).some(
    (f) => f.applies === "restart" && f.key in changed,
  );

  // Live "impact on the algorithm": when planner economics are edited, recompute the plan with the
  // proposed values (debounced, read-only) and show the before/after so the effect is visible.
  const plannerEdits = Object.fromEntries(
    Object.entries(changed).filter(([k]) => k.startsWith("planner.")),
  );
  const plannerKey = JSON.stringify(plannerEdits);
  useEffect(() => {
    if (plannerKey === "{}") {
      setImpact(null);
      return;
    }
    let alive = true; // ignore a stale/in-flight result if the edit changes or we unmount
    const id = setTimeout(async () => {
      try {
        const r = await apiFetch("/api/plan-preview", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: plannerKey,
        });
        const b = r.ok ? await r.json() : null;
        if (alive && b) setImpact(b);
      } catch {
        /* ignore preview errors */
      }
    }, 450);
    return () => {
      alive = false;
      clearTimeout(id);
    };
  }, [plannerKey]);

  // A save error in a collapsed/off-screen section would be invisible — jump to the first section
  // with an error and reveal its Advanced group if the offending field lives there.
  useEffect(() => {
    const errKeys = Object.keys(errors).filter((k) => k !== "_");
    if (!errKeys.length || !schema) return;
    const errField = schema.find((f) => errKeys.includes(f.key));
    if (!errField) return;
    setActiveSection(errField.group);
    setMobileList(false);
    if (errField.advanced) setAdvancedOpen((prev) => new Set([...prev, errField.group]));
  }, [errors, schema]);

  // Scroll the highlighted (search-jumped) field into view once its section + advanced state have
  // rendered.
  useEffect(() => {
    if (!highlightKey) return;
    const el = document.querySelector(`[data-testid="field-${highlightKey}"]`);
    if (el) {
      const reduce = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
      el.scrollIntoView({ block: "center", behavior: reduce ? "auto" : "smooth" });
    }
  }, [highlightKey, activeSection, advancedOpen]);

  function matchedKeys(group: string): string[] {
    const q = search.trim().toLowerCase();
    if (!q) return [];
    return (schema ?? [])
      .filter((f) => f.group === group && !hiddenFieldKeys.has(f.key))
      .filter(
        (f) =>
          f.label.toLowerCase().includes(q) ||
          (f.help ?? "").toLowerCase().includes(q) ||
          f.key.toLowerCase().includes(q),
      )
      .map((f) => f.key);
  }

  function openSection(group: string, highlight: string | null = null) {
    setActiveSection(group);
    setMobileList(false);
    setHighlightKey(highlight);
    if (highlight) {
      const f = (schema ?? []).find((x) => x.key === highlight);
      if (f?.advanced) setAdvancedOpen((prev) => new Set([...prev, group]));
    }
  }

  function handleNavClick(group: string) {
    // In search mode, selecting a result jumps to (and highlights) the first matching field.
    const keys = search.trim() ? matchedKeys(group) : [];
    openSection(group, keys[0] ?? null);
  }

  async function save() {
    setStatus("saving");
    setErrors({});
    const restartGroups = (schema ?? [])
      .filter((f) => f.applies === "restart" && f.key in changed)
      .map((f) => f.group);
    try {
      const r = await apiFetch("/api/settings", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(changed),
      });
      // apiFetch already cleared the (now-invalid) token and triggered the central 401 handler on
      // a 401, which bounces to <Login/> — nothing to show here (Important-1 review fix: this used
      // to point at a paste-token box that no longer exists).
      if (r.status === 401) {
        setStatus("idle");
        return;
      }
      const b = await r.json();
      if (r.status === 422) {
        setErrors(b.errors ?? {});
        setStatus("error");
        return;
      }
      if (!r.ok) throw new Error(b.detail ?? `HTTP ${r.status}`);
      setValues(b.values);
      setEdited(b.values);
      setSolarAdviceApplied(false);
      setStatus("saved");
      if (restartGroups.length) {
        setRestartPending((prev) => new Set([...prev, ...restartGroups]));
        setLastSaveRestart(true);
      } else {
        setLastSaveRestart(false);
      }
      onSaved?.(b.values);
    } catch (e) {
      setErrors({ _: String(e) });
      setStatus("error");
    }
  }

  function reset() {
    setEdited(values);
    setErrors({});
    setStatus("idle");
    setSolarAdviceApplied(false);
  }

  if (loadError) {
    return (
      <div className="error" data-testid="settings-error">
        Cannot load settings: {loadError}
      </div>
    );
  }
  if (!schema) return <div className="loading">Loading settings…</div>;

  // Build the navigation: known sections under their headers + any unknown/future group appended
  // under "App". Only sections actually present in the schema are shown.
  const present = new Set(schema.map((f) => f.group));
  const known = new Set(NAV_GROUPS.flatMap((g) => g.sections));
  const unknown = [...present].filter((g) => !known.has(g));
  const navGroups = NAV_GROUPS.map((g, i) => ({
    header: g.header,
    sections: (i === NAV_GROUPS.length - 1 ? [...g.sections, ...unknown] : g.sections).filter((s) =>
      present.has(s),
    ),
  }));
  const orderedSections = navGroups.flatMap((g) => g.sections);

  const q = search.trim().toLowerCase();
  const matchCount: Record<string, number> = {};
  if (q) for (const s of orderedSections) matchCount[s] = matchedKeys(s).length;
  const visibleSections = q ? orderedSections.filter((s) => matchCount[s] > 0) : orderedSections;

  // Effective active section: keep the selection valid, else fall back to the first section.
  const active =
    activeSection && present.has(activeSection) ? activeSection : orderedSections[0] ?? null;

  function renderField(f: SettingField) {
    const highlighted = f.key === highlightKey ? " field-highlight" : "";
    return (
      <div className={`field-with-hint${highlighted}`} key={f.key}>
        <Field
          field={f}
          value={edited[f.key]}
          error={errors[f.key]}
          disabled={status === "saving" || !canOperate}
          secretSet={Boolean(values[`${f.key}.__set`])}
          onChange={(v) => {
            set(f.key, v);
            // A manual edit (drag/type) after an "Apply" tap returns the hint to normal.
            if (f.key === "planner.solar_confidence") setSolarAdviceApplied(false);
          }}
        />
        {f.key === "planner.solar_confidence" && solarAdvice && (
          <SolarConfidenceHint
            advice={solarAdvice}
            currentPct={Number(edited[f.key] ?? f.default)}
            applied={solarAdviceApplied}
            disabled={status === "saving" || !canOperate}
            onApply={(pct) => {
              set(f.key, pct); // same set() the field's own control uses — dirty ⇒ sticky save bar
              setSolarAdviceApplied(true);
            }}
          />
        )}
        {/* Read-only, honesty-first info callout (CLAUDE.md, feat/ux-batch-3): this dial is what
            the planner ACTUALLY uses today. No toggle here — scenario-based planning
            (ems/intelligence/planning.py) is built but not wired into the live path, so there is
            nothing to switch on. */}
        {f.key === "planner.solar_confidence" && (
          <p className="advisor-hint" data-testid="scenario-intelligence-hint">
            This is the forecast dial the planner actually uses today. Scenario-based planning
            (pessimistic/expected/optimistic futures) will appear here once it is wired in.
          </p>
        )}
        {f.key === "ev.charger_kw" && selectedCar && (
          <p className="advisor-hint" data-testid="car-ac-hint">
            {selectedCar.model}'s onboard AC charger tops out at{" "}
            <strong>{selectedCar.max_ac_kw} kW</strong> — the car, not the wallbox, caps the charge
            speed above that. This field is your wallbox and is left as-is.
          </p>
        )}
      </div>
    );
  }

  const sectionFields = active
    ? schema.filter((f) => f.group === active && !hiddenFieldKeys.has(f.key))
    : [];
  const basicFields = sectionFields.filter((f) => !f.advanced);
  const advancedFields = sectionFields.filter((f) => f.advanced);
  const advIsOpen = active ? advancedOpen.has(active) : false;
  const showSaveBar = canOperate && (dirty || status === "saved");

  return (
    <section
      data-testid="settings"
      className="settings-shell"
      data-mobile={mobileList ? "list" : "section"}
      data-density-surface="manage"
    >
      {auth?.authenticated && (
        <div className="settings-access-bar" data-testid="settings-access">
          <h2 className="settings-group-title">Account</h2>
          <p className="settings-group-hint">
            Signed in. Log out to end this browser&apos;s session — machine/access tokens (widgets,
            scripts) are minted and revoked separately and are unaffected.
          </p>
          <button
            className="btn-ghost"
            data-testid="logout"
            onClick={async () => {
              await apiFetch("/api/auth/logout", { method: "POST" });
              clearToken();
              location.reload();
            }}
          >
            Log out
          </button>
        </div>
      )}

      {/* API tokens (auth slice 3 web, design §5/§7): every logged-in role manages its OWN tokens
          — visible regardless of canOperate/isAdmin (mirrors AdminAccess's admin-only gating
          above, but the opposite: this one is deliberately role-agnostic). The component itself
          renders a session-only manage UI or a quiet sign-in hint (fetches its own kind via
          GET /api/auth/me) — see AccountTokens.tsx. */}
      {auth?.authenticated && <AccountTokens />}

      <div className="settings-panes">
        <aside className="settings-sidebar">
          <div className="settings-search-wrap">
            <input
              className="settings-search"
              data-testid="settings-search"
              type="search"
              value={search}
              placeholder="Search settings…"
              aria-label="Search settings"
              onChange={(e) => setSearch(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Escape") setSearch("");
              }}
            />
          </div>
          <nav className="settings-nav" aria-label="Settings sections">
            {navGroups.map((grp) => {
              const secs = grp.sections.filter((s) => visibleSections.includes(s));
              if (!secs.length) return null;
              return (
                <div className="settings-nav-group" key={grp.header}>
                  <p className="settings-nav-header">{grp.header}</p>
                  {secs.map((s) => {
                    const secDirty = schema.some((f) => f.group === s && f.key in changed);
                    const secRestart = restartPending.has(s);
                    const isActive = s === active;
                    return (
                      <button
                        key={s}
                        type="button"
                        className={`settings-nav-item${isActive ? " active" : ""}`}
                        data-testid={`group-${s}`}
                        aria-current={isActive ? "page" : undefined}
                        onClick={() => handleNavClick(s)}
                      >
                        <SectionIcon group={s} className="settings-nav-icon" />
                        <span className="settings-nav-label">{GROUP_TITLE[s] ?? s}</span>
                        {q && matchCount[s] > 0 && (
                          <span className="settings-nav-count" data-testid={`nav-count-${s}`}>
                            {matchCount[s]}
                          </span>
                        )}
                        {secDirty && <span className="settings-nav-dot" title="unsaved changes" />}
                        {secRestart && (
                          <span className="settings-nav-restart" title="restart to apply">
                            restart
                          </span>
                        )}
                      </button>
                    );
                  })}
                </div>
              );
            })}
            {q && visibleSections.length === 0 && (
              <p className="settings-nav-empty" data-testid="settings-search-empty">
                No settings match “{search}”.
              </p>
            )}
          </nav>
        </aside>

        <div className="settings-content">
          <button
            type="button"
            className="settings-back"
            data-testid="settings-back"
            onClick={() => setMobileList(true)}
          >
            ← All settings
          </button>

          {active && (
            <div className="settings-content-inner" data-density-kind="selected-section">
              <header className="settings-section-head">
                <span className="settings-section-icon">
                  <SectionIcon group={active} />
                </span>
                <div className="settings-section-headtext">
                  <h2 className="settings-section-title">{GROUP_TITLE[active] ?? active}</h2>
                  {/* The legacy "access" hint talks about a shared token + the retired Access box;
                      under identity auth that's dead advice, so show the truthful blurb instead. */}
                  {(() => {
                    const hint =
                      active === "access" && identityAuth
                        ? "Manage who can sign in and the access tokens for widgets and scripts. "
                          + "Every request already requires a signed-in user or an access token."
                        : GROUP_HINT[active];
                    return hint ? <p className="settings-section-hint">{hint}</p> : null;
                  })()}
                </div>
              </header>

              {active === "access" && isAdmin && <AdminAccess />}

              <div className="settings-section-fields">
                {basicFields.map((f) => renderField(f))}
              </div>

              {advancedFields.length > 0 && (
                <div className="settings-advanced">
                  <button
                    type="button"
                    className={`settings-advanced-toggle${advIsOpen ? " open" : ""}`}
                    data-testid="settings-advanced-toggle"
                    aria-expanded={advIsOpen}
                    onClick={() =>
                      setAdvancedOpen((prev) => {
                        const next = new Set(prev);
                        if (next.has(active)) next.delete(active);
                        else next.add(active);
                        return next;
                      })
                    }
                  >
                    <span className="settings-advanced-line" aria-hidden="true" />
                    <span className="settings-advanced-label">Advanced</span>
                    <span className="settings-advanced-count">{advancedFields.length}</span>
                    <span className="settings-advanced-chevron" aria-hidden="true">▾</span>
                  </button>
                  {advIsOpen && (
                    <div className="settings-section-fields" data-testid="settings-advanced-body">
                      {advancedFields.map((f) => renderField(f))}
                    </div>
                  )}
                </div>
              )}

              {active === "planner" && impact?.current && impact?.proposed && (
                <div className="impact" data-testid="settings-impact">
                  <span className="metric-label">Impact on the plan (next 24h)</span>
                  <div className="impact-row">
                    <span className="impact-col">
                      <span className="impact-tag">now</span>
                      <span className="impact-text">{impact.current.summary}</span>
                      <span className="impact-savings">
                        ~€{impact.current.savings_eur.toFixed(2)}/day · {impact.current.charge_slots}{" "}
                        charge / {impact.current.discharge_slots} discharge
                      </span>
                    </span>
                    <span className="impact-arrow">→</span>
                    <span className="impact-col">
                      <span className="impact-tag impact-tag-new">after save</span>
                      <span className="impact-text" data-testid="impact-proposed">
                        {impact.proposed.summary}
                      </span>
                      <span className="impact-savings">
                        ~€{impact.proposed.savings_eur.toFixed(2)}/day ·{" "}
                        {impact.proposed.charge_slots} charge / {impact.proposed.discharge_slots}{" "}
                        discharge
                      </span>
                    </span>
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
      </div>

      {showSaveBar && (
        <div className="settings-savebar" data-testid="settings-savebar">
          <div className="settings-savebar-inner">
            <div className="settings-savebar-msg">
              {dirty && (
                <span className="settings-dirty" data-testid="settings-dirty">
                  {changeCount} unsaved change{changeCount === 1 ? "" : "s"}
                  {restartChanged ? " · some apply on restart" : ""}
                </span>
              )}
              {status === "saved" && (
                <span className="settings-msg-ok" data-testid="settings-saved">
                  Saved{lastSaveRestart ? " — restart to apply connection changes" : ""}
                </span>
              )}
              {status === "error" && errors._ && (
                <span className="settings-msg-err">{errors._}</span>
              )}
            </div>
            <div className="settings-savebar-actions">
              <button className="btn-ghost" data-testid="settings-discard" onClick={reset}
                disabled={!dirty}>
                Discard
              </button>
              <button className="btn-primary" data-testid="settings-save" onClick={save}
                disabled={!dirty || status === "saving"}>
                {status === "saving" ? "Saving…" : "Save"}
              </button>
            </div>
          </div>
        </div>
      )}
    </section>
  );
}
