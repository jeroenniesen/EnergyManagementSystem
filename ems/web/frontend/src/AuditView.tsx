// Audit log: a transparent, read-only history of every plan/battery-mode decision, configuration
// change and manual override the app makes — newest first, filterable by type.
import { useEffect, useState } from "react";

import { apiFetch } from "./auth";

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

  useEffect(() => {
    const url = "/api/audit?limit=200" + (filter ? `&category=${filter}` : "");
    apiFetch(url)
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error("http"))))
      .then((b) => setEntries(b.entries ?? []))
      .catch(() => setErr("Couldn't load the audit log."));
  }, [filter]);

  if (err) return <div className="error" data-testid="audit-error">{err}</div>;
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
        </select>
      </div>
      <p className="settings-group-hint">
        Every plan / battery-mode decision, setting change and manual override the app makes —
        newest first. Read-only.
      </p>
      {entries === null ? (
        <div className="loading">Reading the change log…</div>
      ) : entries.length === 0 ? (
        <p className="plan-reason" data-testid="audit-empty">
          No changes recorded yet — decisions appear here as the system runs.
        </p>
      ) : (
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
      )}
    </section>
  );
}
