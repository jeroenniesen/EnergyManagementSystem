import { useEffect, useState } from "react";

import { authHeaders, getToken, setToken } from "./auth";

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
};

// The `ev.car_id` setting (SettingField.type "text", a stable slug or "" for custom) is rendered
// as brand/model pickers backed by GET /api/cars — see `CarPicker` below.
type CarModel = {
  id: string;
  brand: string;
  model: string;
  battery_net_kwh: number;
  max_ac_kw: number;
  years: string;
};
type CarsResp = { brands: string[]; cars: CarModel[] };

// The `ev.schedule` setting is a JSON string under the hood (SettingField.type "text"), but is
// rendered as a dedicated 7-day editor rather than a raw textbox — see `EvScheduleEditor` below.
type DayKey = "mon" | "tue" | "wed" | "thu" | "fri" | "sat" | "sun";
type ScheduleDay = { enabled: boolean; min_pct: number; ready_by: string };
type Schedule = Record<DayKey, ScheduleDay>;
const DAY_ORDER: DayKey[] = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"];
const DAY_LABEL: Record<DayKey, string> = {
  mon: "Monday", tue: "Tuesday", wed: "Wednesday", thu: "Thursday",
  fri: "Friday", sat: "Saturday", sun: "Sunday",
};
const DEFAULT_SCHEDULE_DAY: ScheduleDay = { enabled: false, min_pct: 80, ready_by: "07:30" };
const TIME_RE = /^([01]\d|2[0-3]):([0-5]\d)$/;

function defaultSchedule(): Schedule {
  const out = {} as Schedule;
  for (const day of DAY_ORDER) out[day] = { ...DEFAULT_SCHEDULE_DAY };
  return out;
}

// Mirrors ems/ev_schedule.py's tolerant `parse_schedule` closely enough for the editor: any
// garbage collapses to the default shape rather than ever throwing mid-render.
function parseScheduleClient(raw: string): Schedule {
  let data: unknown;
  try {
    data = JSON.parse(raw);
  } catch {
    return defaultSchedule();
  }
  if (typeof data !== "object" || data === null) return defaultSchedule();
  const out = defaultSchedule();
  for (const day of DAY_ORDER) {
    const rawDay = (data as Record<string, unknown>)[day];
    if (!rawDay || typeof rawDay !== "object") continue;
    const d = rawDay as Record<string, unknown>;
    const minPctNum = Number(d.min_pct);
    const min_pct = Number.isFinite(minPctNum)
      ? Math.max(0, Math.min(100, Math.round(minPctNum)))
      : 80;
    const ready_by = typeof d.ready_by === "string" && TIME_RE.test(d.ready_by)
      ? d.ready_by
      : "07:30";
    out[day] = { enabled: Boolean(d.enabled), min_pct, ready_by };
  }
  return out;
}

function EvScheduleEditor({
  value,
  disabled,
  onChange,
}: {
  value: string;
  disabled: boolean;
  onChange: (v: string) => void;
}) {
  const schedule = parseScheduleClient(value);
  function updateDay(day: DayKey, patch: Partial<ScheduleDay>) {
    onChange(JSON.stringify({ ...schedule, [day]: { ...schedule[day], ...patch } }));
  }
  return (
    <div className="ev-schedule" data-testid="ev-schedule-editor">
      <div className="ev-schedule-grid">
        <div className="ev-schedule-row ev-schedule-head" aria-hidden="true">
          <span />
          <span>Day</span>
          <span>Min %</span>
          <span>Ready by</span>
        </div>
        {DAY_ORDER.map((day) => {
          const d = schedule[day];
          return (
            <div className="ev-schedule-row" key={day} data-testid={`ev-schedule-row-${day}`}>
              <input
                type="checkbox"
                checked={d.enabled}
                disabled={disabled}
                aria-label={`Enable ${DAY_LABEL[day]}`}
                data-testid={`ev-schedule-${day}-enabled`}
                onChange={(e) => updateDay(day, { enabled: e.target.checked })}
              />
              <span className="ev-schedule-day">{DAY_LABEL[day]}</span>
              <input
                type="number"
                min={0}
                max={100}
                step={5}
                value={d.min_pct}
                disabled={disabled || !d.enabled}
                aria-label={`${DAY_LABEL[day]} minimum percent`}
                data-testid={`ev-schedule-${day}-min-pct`}
                onChange={(e) => {
                  const n = Number(e.target.value);
                  const clamped = Number.isFinite(n) ? Math.max(0, Math.min(100, Math.round(n))) : 0;
                  updateDay(day, { min_pct: clamped });
                }}
              />
              <input
                type="time"
                value={d.ready_by}
                disabled={disabled || !d.enabled}
                aria-label={`${DAY_LABEL[day]} ready by`}
                data-testid={`ev-schedule-${day}-ready-by`}
                onChange={(e) => updateDay(day, { ready_by: e.target.value || "07:30" })}
              />
            </div>
          );
        })}
      </div>
    </div>
  );
}

// Brand → Model picker for `ev.car_id`, backed by GET /api/cars (fetched once, lazily, by the
// parent when the Car group first renders). "Custom" (empty brand) clears `ev.car_id` — the user
// then enters battery_kwh/charger_kw themselves. Picking a model hands the full CarModel back to
// the caller, which autofills battery_kwh but deliberately leaves charger_kw alone (the wallbox
// is a separate physical thing — see the inline AC-limit hint rendered next to that field).
function CarPicker({
  carId,
  cars,
  disabled,
  onPick,
}: {
  carId: string;
  cars: CarModel[];
  disabled: boolean;
  onPick: (car: CarModel | null) => void;
}) {
  const selected = cars.find((c) => c.id === carId) ?? null;
  const brands = [...new Set(cars.map((c) => c.brand))].sort();
  // Local brand selection: normally mirrors the selected car's brand, but must survive being set
  // ahead of a model choice (brand picked, no model yet) — so it isn't derived every render.
  const [brand, setBrand] = useState<string>(selected?.brand ?? "");
  useEffect(() => {
    setBrand(selected?.brand ?? "");
  }, [carId, selected?.brand]);
  const models = brand ? cars.filter((c) => c.brand === brand) : [];

  return (
    <div className="car-picker" data-testid="car-picker">
      <div className="car-picker-row">
        <select
          aria-label="Car brand"
          data-testid="car-brand-select"
          disabled={disabled}
          value={brand}
          onChange={(e) => {
            const b = e.target.value;
            setBrand(b);
            if (!b) onPick(null); // "Custom" — clear the car, keep any manually-set capacity
          }}
        >
          <option value="">Custom</option>
          {brands.map((b) => (
            <option key={b} value={b}>{b}</option>
          ))}
        </select>
        <select
          aria-label="Car model"
          data-testid="car-model-select"
          disabled={disabled || !brand}
          value={selected?.id ?? ""}
          onChange={(e) => {
            const car = cars.find((c) => c.id === e.target.value) ?? null;
            onPick(car);
          }}
        >
          <option value="">{brand ? "Choose a model…" : "—"}</option>
          {models.map((c) => (
            <option key={c.id} value={c.id}>{c.model}</option>
          ))}
        </select>
      </div>
      {selected && (
        <p className="car-picker-specs" data-testid="car-picker-specs">
          {selected.battery_net_kwh} kWh usable · {selected.max_ac_kw} kW AC max
        </p>
      )}
    </div>
  );
}

// Group display order + titles. Connection-type groups first (what most people need), tuning last.
const GROUP_ORDER = ["strategy", "connection", "meters", "battery", "prices", "site", "control",
  "planner", "ai", "access", "ui", "reporting", "ev"];
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
  ev: "Optional. Off by default. Shows a dashboard card suggesting the cheapest window to plug "
    + "in the car — advisory only, the EMS never controls the car.",
};

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
  let control;
  if (field.type === "bool") {
    control = (
      <input id={id} type="checkbox" checked={Boolean(value)} disabled={disabled}
        onChange={(e) => onChange(e.target.checked)} />
    );
  } else if (field.type === "enum") {
    control = (
      <select id={id} value={String(value)} disabled={disabled}
        onChange={(e) => onChange(e.target.value)}>
        {(field.options ?? []).map((o) => (
          <option key={o} value={o}>{o}</option>
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
      <label htmlFor={id} className="field-label">
        {field.label}
        {field.unit && <span className="field-unit"> ({field.unit})</span>}
        {field.applies === "restart" && <span className="field-badge">restart</span>}
      </label>
      {control}
      {field.help && <p className="field-help">{field.help}</p>}
      {error && (
        <p className="field-err" data-testid={`err-${field.key}`}>{error}</p>
      )}
    </div>
  );
}

// Rendered under `planner.solar_confidence` only, when 14 days of forecast-vs-actual evidence
// support a recommendation. Purely informational — it never touches the field's value.
function SolarConfidenceHint({ advice }: { advice: SolarConfidenceAdvice }) {
  return (
    <p className="advisor-hint" data-testid="advisor-solar-confidence">
      Based on {advice.n_slots} matched daytime slots over the last 14 days, forecasts delivered{" "}
      <strong>{advice.median_ratio_pct}%</strong> (typical) / <strong>{advice.p25_ratio_pct}%</strong>{" "}
      (disappointing quarter). Suggestion: <strong>{advice.recommended_pct}%</strong>. You decide —
      this is never applied automatically.
    </p>
  );
}

export function Settings({ onSaved }: { onSaved?: (values: Values) => void } = {}) {
  const [schema, setSchema] = useState<SettingField[] | null>(null);
  const [values, setValues] = useState<Values>({});
  const [edited, setEdited] = useState<Values>({});
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [status, setStatus] = useState<"idle" | "saving" | "saved" | "error">("idle");
  const [loadError, setLoadError] = useState<string | null>(null);
  const [auth, setAuth] = useState<{ required: boolean; authenticated: boolean } | null>(null);
  const [tokenInput, setTokenInput] = useState(getToken());
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [impact, setImpact] = useState<Impact | null>(null);
  const [solarAdvice, setSolarAdvice] = useState<SolarConfidenceAdvice | null>(null);
  const [cars, setCars] = useState<CarModel[] | null>(null);
  // Collapsible groups (tames a long, dense page). Strategy — the headline — is open by default;
  // the rest start collapsed and expand on demand. A group with a save error auto-expands.
  const [openGroups, setOpenGroups] = useState<Set<string>>(new Set(["strategy"]));
  function toggleGroup(g: string) {
    setOpenGroups((prev) => {
      const next = new Set(prev);
      if (next.has(g)) next.delete(g);
      else next.add(g);
      return next;
    });
  }

  async function refreshAuth() {
    try {
      const r = await fetch("/api/auth", { headers: authHeaders() });
      if (r.ok) setAuth(await r.json());
    } catch {
      /* leave auth as-is */
    }
  }

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const r = await fetch("/api/settings");
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

  // Best-effort advisory fetch — hide the hint entirely on error or when there's not yet enough
  // evidence (null), never surface it as a load error.
  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const r = await fetch("/api/advisor/solar-confidence");
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
  // group is expanded (not on every Settings load, since most homes never open it).
  useEffect(() => {
    if (!openGroups.has("ev") || cars !== null) return;
    let alive = true;
    (async () => {
      try {
        const r = await fetch("/api/cars");
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
  }, [openGroups, cars]);
  const selectedCar = (cars ?? []).find((c) => c.id === String(edited["ev.car_id"] ?? "")) ?? null;

  // Only send schema keys whose value actually changed (skip the "<key>.__set" secret flags).
  const schemaKeys = new Set((schema ?? []).map((f) => f.key));
  const changed: Values = {};
  for (const k of Object.keys(edited)) {
    if (schemaKeys.has(k) && edited[k] !== values[k]) changed[k] = edited[k];
  }
  const dirty = Object.keys(changed).length > 0;
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
        const r = await fetch("/api/plan-preview", {
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

  // A save error in a collapsed group would be invisible — auto-expand any group that has one.
  useEffect(() => {
    const errKeys = Object.keys(errors).filter((k) => k !== "_");
    if (!errKeys.length || !schema) return;
    const withErrors = schema.filter((f) => errKeys.includes(f.key)).map((f) => f.group);
    if (withErrors.length) setOpenGroups((prev) => new Set([...prev, ...withErrors]));
  }, [errors, schema]);

  async function save() {
    setStatus("saving");
    setErrors({});
    try {
      const r = await fetch("/api/settings", {
        method: "POST",
        headers: { "content-type": "application/json", ...authHeaders() },
        body: JSON.stringify(changed),
      });
      if (r.status === 401) {
        setErrors({ _: "Unauthorized — set a valid access token below." });
        setStatus("error");
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
      setStatus("saved");
      onSaved?.(b.values);
    } catch (e) {
      setErrors({ _: String(e) });
      setStatus("error");
    }
  }

  if (loadError) {
    return (
      <div className="error" data-testid="settings-error">
        Cannot load settings: {loadError}
      </div>
    );
  }
  if (!schema) return <div className="loading">Loading settings…</div>;

  const visible = schema.filter((f) => showAdvanced || !f.advanced);
  const groups = GROUP_ORDER.filter((g) => visible.some((f) => f.group === g)).concat(
    [...new Set(visible.map((f) => f.group))].filter((g) => !GROUP_ORDER.includes(g)),
  );
  const hiddenAdvanced = schema.filter((f) => f.advanced).length;

  return (
    <section data-testid="settings">
      <div className="settings-top">
        <label className="adv-toggle">
          <input
            type="checkbox"
            checked={showAdvanced}
            onChange={(e) => setShowAdvanced(e.target.checked)}
            data-testid="advanced-toggle"
          />
          Show advanced settings{hiddenAdvanced ? ` (${hiddenAdvanced})` : ""}
        </label>
      </div>

      {auth?.required && (
        <div className="settings-group" data-testid="settings-access">
          <h2 className="settings-group-title">Authorise this browser</h2>
          <p className="settings-group-hint">
            Saving is protected. Enter the access token to authorise writes from this browser.{" "}
            {auth.authenticated ? (
              <span className="settings-msg-ok">authorised</span>
            ) : (
              <span className="settings-msg-err">not authorised</span>
            )}
          </p>
          <div className="settings-fields">
            <div className="field">
              <label className="field-label" htmlFor="set-access-token">Access token</label>
              <input id="set-access-token" type="password" value={tokenInput}
                onChange={(e) => setTokenInput(e.target.value)} data-testid="access-token" />
            </div>
          </div>
          <div className="settings-actions">
            <button className="btn-ghost" data-testid="access-token-save"
              onClick={() => { setToken(tokenInput); refreshAuth(); }}>
              Save token
            </button>
          </div>
        </div>
      )}

      <div className="settings-expand-row">
        <button
          type="button"
          className="settings-expand-all"
          data-testid="settings-expand-all"
          onClick={() =>
            setOpenGroups(openGroups.size >= groups.length ? new Set() : new Set(groups))
          }
        >
          {openGroups.size >= groups.length ? "Collapse all" : "Expand all"}
        </button>
      </div>

      {groups.map((g) => {
        const groupFields = visible.filter((f) => f.group === g);
        const isOpen = openGroups.has(g);
        const groupDirty = groupFields.some((f) => f.key in changed);
        return (
          <div className={`settings-group settings-collapsible${isOpen ? " open" : ""}`} key={g}>
            <button
              type="button"
              className="settings-group-head"
              aria-expanded={isOpen}
              onClick={() => toggleGroup(g)}
              data-testid={`group-${g}`}
            >
              <span className="settings-group-title">{GROUP_TITLE[g] ?? g}</span>
              {groupDirty && <span className="group-dot" title="unsaved changes" />}
              <span className="settings-group-count">{groupFields.length}</span>
              <span className="settings-group-chevron" aria-hidden="true">▾</span>
            </button>
            {isOpen && (
              <div className="settings-group-body">
                {GROUP_HINT[g] && <p className="settings-group-hint">{GROUP_HINT[g]}</p>}
                <div className="settings-fields">
                  {groupFields.map((f) => (
                    <div
                      className={`field-with-hint${
                        f.key === "ev.schedule" || f.key === "ev.car_id" ? " field-full-width" : ""
                      }`}
                      key={f.key}
                    >
                      {f.key === "ev.schedule" ? (
                        <div className="field" data-testid={`field-${f.key}`}>
                          <label className="field-label">{f.label}</label>
                          <EvScheduleEditor
                            value={String(edited[f.key] ?? f.default)}
                            disabled={status === "saving"}
                            onChange={(v) => set(f.key, v)}
                          />
                          {f.help && <p className="field-help">{f.help}</p>}
                        </div>
                      ) : f.key === "ev.car_id" ? (
                        <div className="field" data-testid={`field-${f.key}`}>
                          <label className="field-label">{f.label}</label>
                          <CarPicker
                            carId={String(edited[f.key] ?? f.default)}
                            cars={cars ?? []}
                            disabled={status === "saving"}
                            onPick={(car) => {
                              if (car) {
                                set("ev.car_id", car.id);
                                set("ev.battery_kwh", car.battery_net_kwh);
                              } else {
                                set("ev.car_id", "");
                              }
                            }}
                          />
                          {f.help && <p className="field-help">{f.help}</p>}
                        </div>
                      ) : (
                        <Field
                          field={f}
                          value={edited[f.key]}
                          error={errors[f.key]}
                          disabled={status === "saving"}
                          secretSet={Boolean(values[`${f.key}.__set`])}
                          onChange={(v) => set(f.key, v)}
                        />
                      )}
                      {f.key === "planner.solar_confidence" && solarAdvice && (
                        <SolarConfidenceHint advice={solarAdvice} />
                      )}
                      {f.key === "ev.charger_kw" && selectedCar && (
                        <p className="advisor-hint" data-testid="car-ac-hint">
                          {selectedCar.model}'s onboard AC charger tops out at{" "}
                          <strong>{selectedCar.max_ac_kw} kW</strong> — the car, not the wallbox,
                          caps the charge speed above that. This field is your wallbox and is left
                          as-is.
                        </p>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        );
      })}

      {impact?.current && impact?.proposed && (
        <div className="impact" data-testid="settings-impact">
          <span className="metric-label">Impact on the plan (next 24h)</span>
          <div className="impact-row">
            <span className="impact-col">
              <span className="impact-tag">now</span>
              <span className="impact-text">{impact.current.summary}</span>
              <span className="impact-savings">
                ~€{impact.current.savings_eur.toFixed(2)}/day · {impact.current.charge_slots} charge /{" "}
                {impact.current.discharge_slots} discharge
              </span>
            </span>
            <span className="impact-arrow">→</span>
            <span className="impact-col">
              <span className="impact-tag impact-tag-new">after save</span>
              <span className="impact-text" data-testid="impact-proposed">
                {impact.proposed.summary}
              </span>
              <span className="impact-savings">
                ~€{impact.proposed.savings_eur.toFixed(2)}/day · {impact.proposed.charge_slots} charge
                / {impact.proposed.discharge_slots} discharge
              </span>
            </span>
          </div>
        </div>
      )}

      <div className="settings-actions">
        <button className="btn-primary" onClick={save} disabled={!dirty || status === "saving"}
          data-testid="settings-save">
          {status === "saving" ? "Saving…" : "Save changes"}
        </button>
        <button className="btn-ghost" onClick={() => { setEdited(values); setErrors({}); setStatus("idle"); }}
          disabled={!dirty}>
          Reset
        </button>
        {status === "saved" && (
          <span className="settings-msg-ok" data-testid="settings-saved">
            Saved{restartChanged ? " — restart to apply connection changes" : ""}
          </span>
        )}
        {status === "error" && errors._ && <span className="settings-msg-err">{errors._}</span>}
        {dirty && status !== "saved" && (
          <span className="settings-dirty">
            unsaved changes{restartChanged ? " (some apply on restart)" : ""}
          </span>
        )}
      </div>
    </section>
  );
}
