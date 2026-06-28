import { useEffect, useState } from "react";

import { SYSTEM_OVERALL } from "./labels";

type Check = { key: string; label: string; status: "ok" | "warn" | "fail"; detail: string };
type Readiness = {
  control_ready: boolean;
  sensing_ready: boolean;
  summary: string;
};
type Diag = { overall: "ok" | "warn" | "fail"; checks: Check[]; readiness?: Readiness };

const STATUS_LABEL: Record<string, string> = { ok: "OK", warn: "Check", fail: "Problem" };
const OVERALL_DQ: Record<string, string> = { ok: "complete", warn: "degraded", fail: "unsafe" };

// Group checks by what the homeowner actually cares about (emotional review: System status).
const GROUPS: { title: string; match: (key: string) => boolean }[] = [
  { title: "Your home's data", match: (k) => k.startsWith("sensor.") || k === "data_quality" },
  { title: "Forecast & prices", match: (k) => k === "prices" || k === "forecast" },
  { title: "Battery & control", match: (k) => ["battery", "mode", "planner", "auth"].includes(k) },
  { title: "App storage", match: (k) => k === "history_store" || k === "settings_store" },
];
const groupOf = (key: string) => GROUPS.find((g) => g.match(key))?.title ?? "Other";

// For a warn/fail check: what's wrong + what the user can do (or that EMS is already safe).
const RECOVERY: Record<string, string> = {
  history_store: "Restart the app; if it persists, check the /data volume has space.",
  settings_store: "Settings can't be read or written — restart the app.",
  prices: "Live prices are unavailable, so EMS uses a fallback curve and avoids price-based charging.",
  forecast: "Solar forecast unavailable — EMS falls back to its built-in model curve.",
  battery: "Battery unreachable — check the Indevolt IP and power. EMS stays in safe mode (no control).",
  data_quality: "Some data is stale — see the sensor rows. Control stays safe until it returns.",
  planner: "No plan yet — usually prices/forecast are still loading. EMS holds self-consumption.",
  "sensor.grid": "Critical — check the P1 meter. EMS won't control until grid data returns.",
  "sensor.soc": "Critical — check the battery connection. EMS won't control until SoC returns.",
  "sensor.solar": "Non-critical — solar accounting degrades until the meter returns.",
  "sensor.ev": "Non-critical — the car guard is paused until the meter returns.",
  "sensor.battery": "The battery power reading is delayed; SoC-based decisions may lag.",
};

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
  if (!diag) return <div className="loading">Checking the system…</div>;

  const r = diag.readiness;
  // Tone the control-readiness sentence: safe-and-watching is reassuring, sensing-down needs attention.
  const tone = r ? (!r.sensing_ready ? "attention" : r.control_ready ? "good" : "watching") : "watching";
  const groups = GROUPS.map((g) => ({
    title: g.title,
    checks: diag.checks.filter((c) => groupOf(c.key) === g.title),
  })).filter((g) => g.checks.length > 0);

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

      {r && (
        <p className={`system-readiness home-${tone}`} data-testid="system-readiness">
          {r.summary}
        </p>
      )}

      <div data-testid="checks">
      {groups.map((g) => (
        <div key={g.title} className="check-group" data-testid={`check-group-${g.title}`}>
          <h3 className="check-group-title">{g.title}</h3>
          <ul className="checks">
            {g.checks.map((c) => (
              <li key={c.key} className={`check check-${c.status}`} data-testid={`check-${c.key}`}>
                <span className={`check-dot dot-${c.status}`} aria-hidden="true" />
                <span className="check-label">{c.label}</span>
                <span className="check-detail">{c.detail}</span>
                <span className="check-status" data-status={c.status}>
                  {STATUS_LABEL[c.status] ?? c.status}
                </span>
                {c.status !== "ok" && RECOVERY[c.key] && (
                  <span className="check-recovery" data-testid={`recovery-${c.key}`}>
                    {RECOVERY[c.key]}
                  </span>
                )}
              </li>
            ))}
          </ul>
        </div>
      ))}
      </div>

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
