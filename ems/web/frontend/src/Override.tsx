import { useEffect, useState } from "react";

import { authHeaders } from "./auth";
import { humanize } from "./labels";

type OverrideState = {
  intent: string | null;
  expires_at: string | null;
  active: boolean;
  seconds_remaining: number;
  options: string[];
};

const INTENT_LABEL: Record<string, string> = {
  allow_self_consumption: "Let the battery manage itself",
  grid_charge_to_target: "Charge the battery now",
  hold_reserve: "Hold (don't charge or use)",
  discharge_for_load: "Power the house from the battery",
};
const intentLabel = (intent: string): string => INTENT_LABEL[intent] ?? humanize(intent);
const DURATIONS = [
  { label: "30 min", minutes: 30 },
  { label: "1 hour", minutes: 60 },
  { label: "2 hours", minutes: 120 },
  { label: "4 hours", minutes: 240 },
  { label: "8 hours", minutes: 480 },
];

export function OverrideCard() {
  const [state, setState] = useState<OverrideState | null>(null);
  const [intent, setIntent] = useState("");
  const [minutes, setMinutes] = useState(60);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    async function load() {
      try {
        const r = await fetch("/api/override");
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const b = await r.json();
        if (alive) setState(b);
      } catch {
        // A failed poll shouldn't blank the card; keep the last known state.
      }
    }
    load();
    // Re-poll so the countdown stays current and an expired override flips back to "following plan".
    const id = setInterval(load, 10000);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, []);

  async function apply(payload: { intent: string | null; minutes?: number }) {
    setBusy(true);
    setErr(null);
    try {
      const r = await fetch("/api/override", {
        method: "POST",
        headers: { "content-type": "application/json", ...authHeaders() },
        body: JSON.stringify(payload),
      });
      const b = await r.json();
      if (r.status === 401) {
        setErr("Unauthorized — set an access token in Settings.");
      } else if (r.status === 422) {
        setErr(Object.values(b.errors ?? {}).join("; ") || "invalid override");
      } else if (!r.ok) {
        throw new Error(b.detail ?? `HTTP ${r.status}`);
      } else {
        setState(b);
        setIntent("");
      }
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(false);
    }
  }

  if (!state) return null;
  return (
    <section className="override" data-testid="override">
      <div className="override-head">
        <span className="metric-label">Manual control</span>
        {state.active ? (
          <span className="badge badge-amber" data-testid="override-active">
            forcing {intentLabel(state.intent ?? "")} ·{" "}
            {Math.ceil(state.seconds_remaining / 60)}m left
          </span>
        ) : (
          <span className="badge badge-muted" data-testid="override-inactive">
            following plan
          </span>
        )}
      </div>
      <p className="override-hint">
        Take over for a fixed time and tell the battery exactly what to do — this overrules the
        automatic plan and ends on its own when the time is up. &ldquo;Let the battery manage
        itself&rdquo; hands control back to the battery&apos;s own self-use mode.
      </p>
      <div className="override-controls">
        <select
          aria-label="Override action"
          value={intent}
          onChange={(e) => setIntent(e.target.value)}
          data-testid="override-intent"
        >
          <option value="">Choose a mode…</option>
          {state.options.map((o) => (
            <option key={o} value={o}>
              {intentLabel(o)}
            </option>
          ))}
        </select>
        <select
          aria-label="Override duration"
          value={minutes}
          onChange={(e) => setMinutes(Number(e.target.value))}
          data-testid="override-minutes"
        >
          {DURATIONS.map((d) => (
            <option key={d.minutes} value={d.minutes}>
              {d.label}
            </option>
          ))}
        </select>
        <button
          className="btn-primary"
          disabled={!intent || busy}
          onClick={() => apply({ intent, minutes })}
          data-testid="override-apply"
        >
          Apply
        </button>
        {state.active && (
          <button
            className="btn-ghost"
            disabled={busy}
            onClick={() => apply({ intent: null })}
            data-testid="override-clear"
          >
            Clear
          </button>
        )}
      </div>
      {err && (
        <p className="field-err" data-testid="override-error">
          {err}
        </p>
      )}
    </section>
  );
}
