// Audit log: a transparent, read-only history of every plan/battery-mode decision, configuration
// change and manual override the app makes — newest first, filterable by type.
import { useCallback, useEffect, useState } from "react";

import { apiFetch } from "./auth";
import { AUDIT_VIEW } from "./labels";

type Entry = {
  id: number;
  ts: string;
  category: string;
  summary: string;
  detail: Record<string, unknown>;
};

const CAT_LABEL: Record<string, string> = {
  battery_decision: "Decision",
  config_change: "Setting",
  manual_override: "Override",
  ai_validation: "AI check",
  auth: "Access",
};

function when(ts: string): string {
  const d = new Date(ts);
  return Number.isNaN(d.getTime())
    ? ts
    : d.toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
}

export function AuditView() {
  const [entries, setEntries] = useState<Entry[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [filter, setFilter] = useState("");
  const [fetchGen, setFetchGen] = useState(0);

  const retry = useCallback(() => setFetchGen((g) => g + 1), []);

  useEffect(() => {
    let alive = true;
    setErr(null);
    const url = "/api/audit?limit=200" + (filter ? `&category=${filter}` : "");
    apiFetch(url)
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error("http"))))
      .then((b) => {
        if (alive) setEntries(b.entries ?? []);
      })
      .catch(() => {
        if (alive) {
          setEntries(null);
          setErr(AUDIT_VIEW.loadError);
        }
      });
    return () => {
      alive = false;
    };
  }, [filter, fetchGen]);

  return (
    <section data-testid="audit">
      <div className="override-head">
        <span className="metric-label">Audit log</span>
        <select
          className="audit-filter"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          aria-label="Filter the audit log by type"
          data-testid="audit-filter"
        >
          <option value="">All changes</option>
          <option value="battery_decision">Battery decisions</option>
          <option value="config_change">Setting changes</option>
          <option value="manual_override">Manual overrides</option>
          <option value="ai_validation">AI checks</option>
          <option value="auth">Access &amp; accounts</option>
        </select>
      </div>
      {err && (
        <div className="audit-error-block" role="alert" data-testid="audit-error">
          <p>{err}</p>
          <button type="button" className="btn secondary" data-testid="audit-retry" onClick={retry}>
            {AUDIT_VIEW.retry}
          </button>
        </div>
      )}
      <p className="settings-group-hint">
        Every plan / battery-mode decision, setting change and manual override the app makes —
        newest first. Read-only.
      </p>
      {!err && entries === null ? (
        <div className="loading">Reading the change log…</div>
      ) : !err && entries && entries.length === 0 ? (
        <p className="plan-reason" data-testid="audit-empty">
          No changes recorded yet — decisions appear here as the system runs.
        </p>
      ) : !err && entries ? (
        <ul className="audit-list" data-testid="audit-list">
          {entries.map((e) => (
            <li key={e.id} className="audit-row">
              <span className={`audit-cat audit-cat-${e.category}`}>
                {CAT_LABEL[e.category] ?? e.category}
              </span>
              <span className="audit-summary">{e.summary}</span>
              <span className="audit-time">{when(e.ts)}</span>
            </li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}
