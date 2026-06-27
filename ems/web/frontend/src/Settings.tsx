import { useEffect, useState } from "react";

export type SettingField = {
  key: string;
  label: string;
  type: "number" | "int" | "bool" | "enum";
  default: number | boolean | string;
  group: string;
  help: string;
  min: number | null;
  max: number | null;
  options: string[] | null;
  step: number | null;
  unit: string;
};
type SettingsResp = { schema: SettingField[]; values: Record<string, number | boolean | string> };
type Values = Record<string, number | boolean | string>;

const GROUP_TITLE: Record<string, string> = {
  planner: "Planner economics",
  control: "Control & safety",
  ui: "Appearance",
};
const GROUP_HINT: Record<string, string> = {
  planner: "Tune the arbitrage maths — the plan recomputes from these immediately.",
  control: "Safety limits applied to the battery mode controller.",
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
  // Hold the raw text locally so the user can transiently clear/retype a number without it
  // snapping back to 0 mid-edit; only commit a coerced value on blur. Re-sync if the parent
  // resets the value (Save/Reset).
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
  onChange,
}: {
  field: SettingField;
  value: number | boolean | string;
  error?: string;
  disabled: boolean;
  onChange: (v: number | boolean | string) => void;
}) {
  const id = `set-${field.key}`;
  let control;
  if (field.type === "bool") {
    control = (
      <input
        id={id}
        type="checkbox"
        checked={Boolean(value)}
        disabled={disabled}
        onChange={(e) => onChange(e.target.checked)}
      />
    );
  } else if (field.type === "enum") {
    control = (
      <select
        id={id}
        value={String(value)}
        disabled={disabled}
        onChange={(e) => onChange(e.target.value)}
      >
        {(field.options ?? []).map((o) => (
          <option key={o} value={o}>
            {o}
          </option>
        ))}
      </select>
    );
  } else {
    control = (
      <NumberInput
        field={field}
        value={Number(value)}
        disabled={disabled}
        onChange={onChange}
      />
    );
  }
  return (
    <div className={`field${error ? " field-error" : ""}`} data-testid={`field-${field.key}`}>
      <label htmlFor={id} className="field-label">
        {field.label}
        {field.unit && <span className="field-unit"> ({field.unit})</span>}
      </label>
      {control}
      {field.help && <p className="field-help">{field.help}</p>}
      {error && (
        <p className="field-err" data-testid={`err-${field.key}`}>
          {error}
        </p>
      )}
    </div>
  );
}

export function Settings() {
  const [schema, setSchema] = useState<SettingField[] | null>(null);
  const [values, setValues] = useState<Values>({});
  const [edited, setEdited] = useState<Values>({});
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [status, setStatus] = useState<"idle" | "saving" | "saved" | "error">("idle");
  const [loadError, setLoadError] = useState<string | null>(null);

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
    return () => {
      alive = false;
    };
  }, []);

  function set(key: string, v: number | boolean | string) {
    setEdited((prev) => ({ ...prev, [key]: v }));
    setStatus("idle");
  }

  // Only send keys whose value actually changed (a true partial update).
  const changed: Values = {};
  for (const k of Object.keys(edited)) {
    if (edited[k] !== values[k]) changed[k] = edited[k];
  }
  const dirty = Object.keys(changed).length > 0;

  async function save() {
    setStatus("saving");
    setErrors({});
    try {
      const r = await fetch("/api/settings", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(changed),
      });
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

  const groups = [...new Set(schema.map((f) => f.group))];
  return (
    <section data-testid="settings">
      {groups.map((g) => (
        <div className="settings-group" key={g}>
          <h2 className="settings-group-title">{GROUP_TITLE[g] ?? g}</h2>
          {GROUP_HINT[g] && <p className="settings-group-hint">{GROUP_HINT[g]}</p>}
          <div className="settings-fields">
            {schema
              .filter((f) => f.group === g)
              .map((f) => (
                <Field
                  key={f.key}
                  field={f}
                  value={edited[f.key]}
                  error={errors[f.key]}
                  disabled={status === "saving"}
                  onChange={(v) => set(f.key, v)}
                />
              ))}
          </div>
        </div>
      ))}
      <div className="settings-actions">
        <button
          className="btn-primary"
          onClick={save}
          disabled={!dirty || status === "saving"}
          data-testid="settings-save"
        >
          {status === "saving" ? "Saving…" : "Save changes"}
        </button>
        <button
          className="btn-ghost"
          onClick={() => {
            setEdited(values);
            setErrors({});
            setStatus("idle");
          }}
          disabled={!dirty}
        >
          Reset
        </button>
        {status === "saved" && (
          <span className="settings-msg-ok" data-testid="settings-saved">
            Saved
          </span>
        )}
        {status === "error" && errors._ && (
          <span className="settings-msg-err">{errors._}</span>
        )}
        {dirty && status !== "saved" && (
          <span className="settings-dirty">unsaved changes</span>
        )}
      </div>
    </section>
  );
}
