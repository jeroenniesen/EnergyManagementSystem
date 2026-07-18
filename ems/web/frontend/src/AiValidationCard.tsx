// The AI's advisory "second opinion" on the current plan — read-only. Runs on a schedule (and the
// "Check now" button). Purely advisory: the AI never changes anything. Hidden until AI is on.
import { useEffect, useState } from "react";

import { apiFetch } from "./auth";

type Latest = { text: string; ts: string; source: string } | null;

function when(ts: string): string {
  const d = new Date(ts);
  return Number.isNaN(d.getTime())
    ? ts
    : d.toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
}

export function AiValidationCard() {
  const [latest, setLatest] = useState<Latest>(null);
  const [active, setActive] = useState(false);
  const [busy, setBusy] = useState(false);

  function apply(b: { latest?: Latest; active?: boolean }) {
    setLatest(b.latest ?? null);
    setActive(Boolean(b.active));
  }

  useEffect(() => {
    apiFetch("/api/ai/validation")
      .then((r) => (r.ok ? r.json() : null))
      .then((b) => b && apply(b))
      .catch(() => {});
  }, []);

  async function checkNow() {
    setBusy(true);
    try {
      const r = await apiFetch("/api/ai/validate", { method: "POST" });
      if (r.ok) apply(await r.json());
    } catch {
      /* advisory only — a failed check never disrupts the dashboard */
    } finally {
      setBusy(false);
    }
  }

  if (!active && !latest) return null; // nothing to show until AI is enabled / has run
  return (
    <section className="ai-validation" data-testid="ai-validation">
      <div className="override-head">
        <span className="metric-label">AI second opinion</span>
        {active && (
          <button
            className="btn-ghost ai-check-btn"
            onClick={checkNow}
            disabled={busy}
            data-testid="ai-check"
          >
            {busy ? "Checking…" : "Check now"}
          </button>
        )}
      </div>
      {latest ? (
        <>
          <p className="ai-validation-text" data-testid="ai-validation-text">{latest.text}</p>
          <p className="ai-validation-meta">
            Advisory review · {when(latest.ts)} · the AI never changes anything
          </p>
        </>
      ) : (
        <p className="plan-reason">No review yet — runs on a schedule, or press “Check now”.</p>
      )}
    </section>
  );
}
