import { useEffect, useState } from "react";

import { HEALTH_ROW_LABEL, HEALTH_STATUS, INCIDENT_TYPE_LABEL, SYSTEM_OVERALL } from "./labels";

type Check = { key: string; label: string; status: "ok" | "warn" | "fail"; detail: string };
type Readiness = {
  control_ready: boolean;
  sensing_ready: boolean;
  summary: string;
};
// The two ops signals (B-76) borrowed from /api/diagnostics's existing `storage`/`recorder` reads
// — no new measurement, just surfaced alongside Model health so "is the data layer healthy" has
// one home. Kept loose (only the fields this page reads) rather than mirroring the full contract.
type BackupState = {
  last_backup_ts: string | null;
  last_backup_ok: boolean | null;
};
type Diag = {
  overall: "ok" | "warn" | "fail";
  checks: Check[];
  readiness?: Readiness;
  storage?: { backup?: BackupState | null } | null;
  recorder?: { clamped_samples: number } | null;
};
type IncidentRollup = {
  total: number;
  by_type: Record<string, number>;
  by_day: Record<string, number>;
  most_recent: string | null;
  last_7_days: number;
};

// /api/accuracy's synthesized B-76 health block + the three headline numbers it was derived from.
type HealthStatusValue = "ok" | "warn" | "unknown";
type ModelHealth = {
  solar: HealthStatusValue;
  load: HealthStatusValue;
  plan_execution: HealthStatusValue;
  notes: string[];
};
type Accuracy = {
  solar: { bias_w: number | null } | null;
  load: { mape_pct: number | null } | null;
  plan_execution: { hit_rate_pct: number | null } | null;
  health: ModelHealth;
};

const HEALTH_ROW_ORDER: (keyof Omit<ModelHealth, "notes">)[] = ["solar", "load", "plan_execution"];

// notes[] holds one entry per WARN row, in solar/load/plan_execution order, skipping ok/unknown —
// this walks the same order to attribute each note back to the row it belongs to.
function noteForRow(health: ModelHealth, row: keyof Omit<ModelHealth, "notes">): string | null {
  let i = 0;
  for (const key of HEALTH_ROW_ORDER) {
    if (health[key] !== "warn") continue;
    if (key === row) return health.notes[i] ?? null;
    i++;
  }
  return null;
}

function headlineFor(row: keyof Omit<ModelHealth, "notes">, acc: Accuracy): string | null {
  if (row === "solar") {
    const v = acc.solar?.bias_w;
    return v == null ? null : `${v} W bias`;
  }
  if (row === "load") {
    const v = acc.load?.mape_pct;
    return v == null ? null : `${v}% MAPE`;
  }
  const v = acc.plan_execution?.hit_rate_pct;
  return v == null ? null : `${v}% hit rate`;
}

function when(ts: string): string {
  const d = new Date(ts);
  return Number.isNaN(d.getTime()) ? ts : d.toLocaleString([], { dateStyle: "medium" });
}

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
  const [incidents, setIncidents] = useState<IncidentRollup | null>(null);
  const [accuracy, setAccuracy] = useState<Accuracy | null>(null);

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

  // Control-incident rollup (command failures, cluster mismatches, fallbacks, reverts) — a
  // separate, best-effort fetch so a hiccup here never blocks the readiness checks above.
  useEffect(() => {
    let alive = true;
    async function load() {
      try {
        const r = await fetch("/api/incidents");
        if (!r.ok) return;
        const b = await r.json();
        if (alive) setIncidents(b.incidents ?? null);
      } catch {
        // best-effort — the panel simply stays hidden
      }
    }
    load();
    const id = setInterval(load, 30000);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, []);

  // Model health (B-76): the synthesized solar/load/plan-execution verdict — one extra, best-effort
  // fetch (same pattern as incidents above); a hiccup here never blocks the readiness checks.
  useEffect(() => {
    let alive = true;
    async function load() {
      try {
        const r = await fetch("/api/accuracy");
        if (!r.ok) return;
        const b = await r.json();
        if (alive) setAccuracy(b);
      } catch {
        // best-effort — the panel simply stays hidden
      }
    }
    load();
    const id = setInterval(load, 30000);
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

      {incidents && (
        <div
          className={`incidents ${incidents.total === 0 ? "incidents-calm" : "incidents-warn"}`}
          data-testid="incidents"
        >
          <span className="metric-label">Control health</span>
          {incidents.total === 0 ? (
            <p className="incidents-summary incidents-calm-text">
              No control incidents recorded
            </p>
          ) : (
            <>
              <p className="incidents-summary incidents-warn-text">
                {incidents.last_7_days} incident{incidents.last_7_days === 1 ? "" : "s"} in the
                last 7 days — most recent{" "}
                {incidents.most_recent ? when(incidents.most_recent) : "unknown"}
              </p>
              <ul className="incident-types" data-testid="incident-types">
                {Object.entries(incidents.by_type).map(([type, count]) => (
                  <li key={type} className="incident-type-row">
                    <span className="incident-type-label">
                      {INCIDENT_TYPE_LABEL[type] ?? type}
                    </span>
                    <span className="incident-type-count">{count}</span>
                  </li>
                ))}
              </ul>
            </>
          )}
        </div>
      )}

      {accuracy && (
        <div className="model-health" data-testid="model-health">
          <span className="metric-label">Model health</span>
          <ul className="health-rows" data-testid="health-rows">
            {HEALTH_ROW_ORDER.map((row) => {
              const status = accuracy.health[row];
              const note = status === "warn" ? noteForRow(accuracy.health, row) : null;
              const headline = status === "unknown" ? null : headlineFor(row, accuracy);
              return (
                <li
                  key={row}
                  className={`health-row health-${status}`}
                  data-testid={`health-${row}`}
                >
                  <span className={`check-dot dot-${status}`} aria-hidden="true" />
                  <span className="health-label">{HEALTH_ROW_LABEL[row]}</span>
                  <span className="health-value">{headline ?? "—"}</span>
                  <span className="health-status" data-status={status}>
                    {HEALTH_STATUS[status]?.label ?? status}
                  </span>
                  {note && (
                    <span className="health-note" data-testid={`health-note-${row}`}>
                      {note}
                    </span>
                  )}
                </li>
              );
            })}
          </ul>

          <ul className="health-ops" data-testid="health-ops">
            <li className="health-ops-row" data-testid="health-backups">
              <span className="health-ops-label">Backups</span>
              <span className="health-ops-value">
                {diag.storage?.backup?.last_backup_ts
                  ? `${when(diag.storage.backup.last_backup_ts)} — ` +
                    (diag.storage.backup.last_backup_ok ? "ok" : "failed")
                  : "No backup has run yet"}
              </span>
            </li>
            <li className="health-ops-row" data-testid="health-clamped-samples">
              <span className="health-ops-label">Clamped samples</span>
              <span className="health-ops-value">
                {diag.recorder ? diag.recorder.clamped_samples : "—"}
              </span>
            </li>
          </ul>

          <p className="health-footer">
            Detailed numbers: the export package's validation summary.
          </p>
        </div>
      )}

      <div className="export" data-testid="export">
        <span className="metric-label">Export &amp; replay</span>
        <p className="settings-group-hint">
          The full <strong>export package</strong> is one ZIP of your history as spreadsheets
          (energy, prices, solar forecast vs. actual, plan history, daily savings, gas &amp; CO₂,
          decision log) plus a manifest and a plain-language validation summary — for your own
          analytics, or to share for a health check. Or grab a single CSV / the plan replay below.
        </p>
        <div className="export-links">
          <a className="btn-primary" href="/api/export/package" data-testid="export-package">
            Download export package (ZIP)
          </a>
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
          <a className="btn-ghost" href="/api/replay" download="plan-replay.json"
             data-testid="export-replay">
            Plan replay (JSON)
          </a>
        </div>
      </div>
    </section>
  );
}
