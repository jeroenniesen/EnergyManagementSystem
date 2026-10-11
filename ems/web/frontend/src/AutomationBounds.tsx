import { useEffect, useState } from "react";

import { apiFetch } from "./auth";

type BoundRow = {
  id: string;
  label: string;
  explanation: string;
  status: "known" | "unknown" | "effective" | string;
  display: string;
  value?: unknown;
  unit?: string;
  setting_key?: string | null;
};

type BoundsResp = {
  title: string;
  summary: string;
  writes_allowed: boolean;
  block_reason: string | null;
  bounds: BoundRow[];
};

/** Read-only "What EMS may do" card for Settings → Control (#246 / B-35 slice 1). */
export function AutomationBoundsCard({ refreshKey = 0 }: { refreshKey?: number }) {
  const [data, setData] = useState<BoundsResp | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const r = await apiFetch("/api/automation-bounds");
        if (!r.ok) {
          if (!cancelled) setError("Could not load automation limits.");
          return;
        }
        const body = (await r.json()) as BoundsResp;
        if (!cancelled) {
          setData(body);
          setError(null);
        }
      } catch {
        if (!cancelled) setError("Could not load automation limits.");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [refreshKey]);

  if (error) {
    return (
      <section className="automation-bounds" data-testid="automation-bounds" aria-live="polite">
        <h3 className="automation-bounds-title">What EMS may do</h3>
        <p className="advisor-hint" data-testid="automation-bounds-error">{error}</p>
      </section>
    );
  }
  if (!data) {
    return (
      <section className="automation-bounds" data-testid="automation-bounds" aria-busy="true">
        <h3 className="automation-bounds-title">What EMS may do</h3>
        <p className="settings-group-hint">Loading limits…</p>
      </section>
    );
  }

  return (
    <section className="automation-bounds" data-testid="automation-bounds">
      <h3 className="automation-bounds-title">{data.title}</h3>
      <p
        className={`automation-bounds-summary${data.writes_allowed ? "" : " blocked"}`}
        data-testid="automation-bounds-summary"
      >
        {data.summary}
      </p>
      <ul className="automation-bounds-list">
        {data.bounds.map((b) => (
          <li
            key={b.id}
            className={`automation-bounds-row status-${b.status}`}
            data-testid={`automation-bound-${b.id}`}
            data-status={b.status}
          >
            <div className="automation-bounds-row-head">
              <span className="automation-bounds-label">{b.label}</span>
              <span className="automation-bounds-value">{b.display}</span>
            </div>
            <p className="automation-bounds-help">{b.explanation}</p>
          </li>
        ))}
      </ul>
      <p className="automation-bounds-footnote">
        Read-only overview. Change reserve and power under Battery, fuse and watch-only
        below, and export arming on Trading.
      </p>
    </section>
  );
}
