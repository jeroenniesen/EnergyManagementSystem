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
};
type SettingsResp = { schema: SettingField[]; values: Record<string, number | boolean | string> };
type Values = Record<string, number | boolean | string>;

// Group display order + titles. Connection-type groups first (what most people need), tuning last.
const GROUP_ORDER = ["connection", "meters", "battery", "prices", "site", "control", "planner", "ui"];
const GROUP_TITLE: Record<string, string> = {
  connection: "Connection",
  meters: "Energy meters (HomeWizard)",
  battery: "Battery (Indevolt)",
  prices: "Electricity prices (Tibber)",
  site: "Solar & location",
  control: "Control & safety",
  planner: "Planner economics",
  ui: "Appearance",
};
const GROUP_HINT: Record<string, string> = {
  connection: "Read your real devices, or run the built-in simulator.",
  meters: "Local IP addresses of your HomeWizard meters.",
  battery: "Battery address, capacity and reserves.",
  prices: "Your Tibber token for live day-ahead prices.",
  site: "Location & array — these drive the solar forecast.",
  control: "Safety limits applied to the battery mode controller.",
  planner: "The arbitrage maths — the plan recomputes from these immediately.",
  ui: "How the dashboard looks.",
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

  function set(key: string, v: number | boolean | string) {
    setEdited((prev) => ({ ...prev, [key]: v }));
    setStatus("idle");
  }

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
          <h2 className="settings-group-title">Access</h2>
          <p className="settings-group-hint">
            Saving is protected. Enter the access token to authorise writes.{" "}
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

      {groups.map((g) => (
        <div className="settings-group" key={g}>
          <h2 className="settings-group-title">{GROUP_TITLE[g] ?? g}</h2>
          {GROUP_HINT[g] && <p className="settings-group-hint">{GROUP_HINT[g]}</p>}
          <div className="settings-fields">
            {visible
              .filter((f) => f.group === g)
              .map((f) => (
                <Field
                  key={f.key}
                  field={f}
                  value={edited[f.key]}
                  error={errors[f.key]}
                  disabled={status === "saving"}
                  secretSet={Boolean(values[`${f.key}.__set`])}
                  onChange={(v) => set(f.key, v)}
                />
              ))}
          </div>
        </div>
      ))}

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
