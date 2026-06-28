import { useEffect, useState } from "react";

import { SYSTEM_OVERALL } from "./labels";

type Check = { key: string; label: string; status: "ok" | "warn" | "fail"; detail: string };
type Diag = { overall: "ok" | "warn" | "fail"; checks: Check[] };

const STATUS_LABEL: Record<string, string> = { ok: "OK", warn: "Check", fail: "Problem" };
// Reuse the data-quality badge palette: ok→green, warn→amber, fail→red.
const OVERALL_DQ: Record<string, string> = { ok: "complete", warn: "degraded", fail: "unsafe" };

export function SystemView() {
  const [diag, setDiag] = useState<Diag | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    async function load() {
      try {
        const r = await fetch("/api/diagnostics");
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const b = await r.json();
        if (alive) {
          setDiag(b);
          setErr(null);
        }
      } catch (e) {
        if (alive) setErr(String(e));
      }
    }
    load();
    const id = setInterval(load, 10000);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, []);

  if (err) {
    return (
      <div className="error" data-testid="system-error">
        Cannot load diagnostics: {err}
      </div>
    );
  }
  if (!diag) return <div className="loading">Loading diagnostics…</div>;

  return (
    <section data-testid="system">
      <div className="override-head">
        <span className="metric-label">System status</span>
        <span
          className={`badge badge-dq dq-${OVERALL_DQ[diag.overall] ?? "degraded"}`}
          data-testid="system-overall"
          title={SYSTEM_OVERALL[diag.overall]?.title}
        >
          {SYSTEM_OVERALL[diag.overall]?.label ?? diag.overall}
        </span>
      </div>
      <ul className="checks" data-testid="checks">
        {diag.checks.map((c) => (
          <li key={c.key} className={`check check-${c.status}`} data-testid={`check-${c.key}`}>
            <span className={`check-dot dot-${c.status}`} aria-hidden="true" />
            <span className="check-label">{c.label}</span>
            <span className="check-detail">{c.detail}</span>
            <span className="check-status" data-status={c.status}>
              {STATUS_LABEL[c.status] ?? c.status}
            </span>
          </li>
        ))}
      </ul>

      <div className="export" data-testid="export">
        <span className="metric-label">Export history</span>
        <p className="settings-group-hint">Download recent measurements as a spreadsheet.</p>
        <div className="export-links">
          <a className="btn-ghost" href="/api/export?kind=raw&format=csv" data-testid="export-raw">
            Raw meters (CSV)
          </a>
          <a
            className="btn-ghost"
            href="/api/export?kind=derived&format=csv"
            data-testid="export-derived"
          >
            Reconstructed load (CSV)
          </a>
        </div>
      </div>
    </section>
  );
}
