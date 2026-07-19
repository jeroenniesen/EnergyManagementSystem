import { useEffect, useState } from "react";

import { apiFetch } from "./auth";
import { humanize } from "./labels";

type OverrideState = {
  intent: string | null;
  expires_at: string | null;
  active: boolean;
  seconds_remaining: number;
  options: string[];
};

const INTENT_LABEL: Record<string, string> = {
  allow_self_consumption: "Return to the battery's own plan",
  grid_charge_to_target: "Charge the battery now",
  hold_reserve: "Hold the battery (don't charge or use it)",
  discharge_for_load: "Power the house from the battery",
};
// What each action will actually do — so the choice is understood, not hidden behind friendly words.
const CONSEQUENCE: Record<string, string> = {
  allow_self_consumption: "Hands control straight back to the battery's own self-use mode.",
  grid_charge_to_target: "This may buy power from the grid now, even if it isn't the cheapest time.",
  hold_reserve: "The battery will neither charge nor discharge — it just holds what it has.",
  discharge_for_load: "This runs the house from the battery now, which may empty it sooner.",
};
// Charge/discharge meaningfully change cost or comfort → confirm first. Hold / return-to-plan don't.
const RISKY = new Set(["grid_charge_to_target", "discharge_for_load"]);
const intentLabel = (intent: string): string => INTENT_LABEL[intent] ?? humanize(intent);
const DURATIONS = [
  { label: "30 min", minutes: 30 },
  { label: "1 hour", minutes: 60 },
  { label: "2 hours", minutes: 120 },
  { label: "4 hours", minutes: 240 },
  { label: "8 hours", minutes: 480 },
];

function endsAt(minutes: number): string {
  const t = new Date(Date.now() + minutes * 60_000);
  return t.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
}

export function OverrideCard({ dataQuality }: { dataQuality?: string }) {
  const [state, setState] = useState<OverrideState | null>(null);
  const [intent, setIntent] = useState("");
  const [minutes, setMinutes] = useState(60);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  // When set, we're awaiting confirmation for a risky action (charge/discharge).
  const [confirming, setConfirming] = useState<{ intent: string; minutes: number } | null>(null);

  useEffect(() => {
    let alive = true;
    async function load() {
      try {
        const r = await apiFetch("/api/override");
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
    setConfirming(null);
    try {
      const r = await apiFetch("/api/override", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(payload),
      });
      const b = await r.json();
      if (r.status === 401) {
        // apiFetch already cleared the (now-invalid) token and triggered the central 401 handler,
        // which bounces to <Login/> — nothing to show here (dead paste-token-box copy removed).
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

  // Apply directly for safe actions; for a risky one, ask for confirmation first.
  function onApply() {
    if (!intent) return;
    if (RISKY.has(intent)) setConfirming({ intent, minutes });
    else apply({ intent, minutes });
  }

  if (!state) return null;
  const unsafe = dataQuality === "unsafe";
  const riskyBlocked = unsafe && intent !== "" && RISKY.has(intent);

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
        Temporarily take over from the automatic plan. Your choice runs for a set time, then ends on
        its own and EMS goes back to following the plan.
      </p>

      {confirming ? (
        <div className="override-confirm" data-testid="override-confirm-panel">
          <p className="override-confirm-title">
            {intentLabel(confirming.intent)} for {DURATIONS.find((d) => d.minutes ===
              confirming.minutes)?.label ?? `${confirming.minutes} min`}?
          </p>
          <p className="override-consequence" data-testid="override-consequence">
            {CONSEQUENCE[confirming.intent]}
          </p>
          <p className="override-reassure">
            Ends automatically around {endsAt(confirming.minutes)}, then EMS follows the plan again.
          </p>
          <div className="override-controls">
            <button
              className="btn-primary"
              disabled={busy}
              onClick={() => apply({ intent: confirming.intent, minutes: confirming.minutes })}
              data-testid="override-confirm"
            >
              Confirm
            </button>
            <button
              className="btn-ghost"
              disabled={busy}
              onClick={() => setConfirming(null)}
              data-testid="override-cancel"
            >
              Cancel
            </button>
          </div>
        </div>
      ) : (
        <>
          <div className="override-controls">
            <select
              aria-label="Override action"
              value={intent}
              onChange={(e) => setIntent(e.target.value)}
              data-testid="override-intent"
            >
              <option value="">Choose a mode…</option>
              {state.options.map((o) => (
                <option key={o} value={o} disabled={unsafe && RISKY.has(o)}>
                  {intentLabel(o)}
                  {unsafe && RISKY.has(o) ? " — unavailable (data unsafe)" : ""}
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
              disabled={!intent || busy || riskyBlocked}
              onClick={onApply}
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
          {intent && CONSEQUENCE[intent] && (
            <p className="override-consequence" data-testid="override-consequence">
              {CONSEQUENCE[intent]}
            </p>
          )}
          {riskyBlocked && (
            <p className="field-err" data-testid="override-blocked">
              Charging and discharging are paused while battery/meter data is unsafe — the battery is
              safe and managing itself. You can still return control to the plan.
            </p>
          )}
        </>
      )}
      {err && (
        <p className="field-err" data-testid="override-error">
          {err}
        </p>
      )}
    </section>
  );
}
