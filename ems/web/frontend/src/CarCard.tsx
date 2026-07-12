// The car-charging card (design 2026-07-12): tells the user WHEN to plug in the car so a weekly
// minimum-charge schedule is met as cheaply as possible. Advisory/visual only — the EMS has no
// charger API and never controls the car (docs/superpowers/specs/2026-07-12-ev-charging-design.md).
// Consumes GET /api/car/plan, which returns a progressive set of shapes so the card can prompt for
// whatever is missing:
//   enabled:false                        -> render nothing (feature is off)
//   enabled:true, needs_anchor:true      -> ask for the car's current charge level
//   enabled:true, needs_schedule:true    -> point at Settings -> Car
//   enabled:true, soc + plan             -> the full plan (SoC, next deadline, advice, windows,
//                                           a 48h plug-in timeline)
// The one write here (POST /api/car/soc, the manual SoC anchor) reuses the exact auth-header +
// 401/422 handling pattern as OverrideCard's `apply()` (see Override.tsx).
import { useEffect, useState } from "react";

import { authHeaders } from "./auth";
import { Icon } from "./icons";

type Soc = {
  soc_pct: number;
  anchor_pct: number;
  anchor_ts: string;
  added_kwh: number;
  sessions_since_anchor: number;
  age_hours: number;
  stale: boolean;
};

type Deadline = {
  ready_by: string;
  min_pct: number;
  required_kwh: number;
  planned_kwh: number;
  pending_kwh: number;
  shortfall_kwh: number;
  already_met: boolean;
  feasible: boolean;
};

type Slot = {
  start: string;
  kw: number;
  ac_kwh: number;
  battery_kwh: number;
  eur_per_kwh_effective: number;
  est_cost_eur: number;
  solar_surplus: boolean;
  for_deadline: string;
};

type PlanWindow = {
  start: string;
  end: string;
  ac_kwh: number;
  battery_kwh: number;
  est_cost_eur: number;
  solar_share_pct: number;
  reason: string;
};

type Plan = {
  soc: number;
  deadlines: Deadline[];
  slots: Slot[];
  windows: PlanWindow[];
  advice: string;
  negative_price_hint: string | null;
  total_est_cost_eur: number;
  total_planned_kwh: number;
};

type CarPlanResp = {
  enabled: boolean;
  soc: Soc | null;
  plan: Plan | null;
  needs_anchor?: boolean;
  needs_schedule?: boolean;
};

// A best-effort background refresh — the plan barely moves within a couple of minutes.
const REFRESH_MS = 2 * 60 * 1000;

const HORIZON_H = 48;
const SLOT_MS = 15 * 60 * 1000;
const HORIZON_MS = HORIZON_H * 3600 * 1000;
const CELL_COUNT = HORIZON_MS / SLOT_MS; // 192

function dayTime(iso: string): string {
  const d = new Date(iso);
  const day = d.toLocaleDateString([], { weekday: "short" });
  const time = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
  return `${day} ${time}`;
}

function hm(iso: string): string {
  return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
}

function fmtTime(ms: number): string {
  return new Date(ms).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
}

type TimelineCell = { startMs: number; slot: Slot | null };

// A 48h grid of 15-min cells STARTING NOW (floored to the current quarter-hour, which lines up
// with the global 15-min slot grid since epoch-ms % 15min is timezone-independent). Built purely
// on the client clock — independent of which slots the backend happened to price/allocate — so
// the strip always shows the full horizon, with allocated slots (from `plan.slots`) overlaid.
function buildCells(nowMs: number, slots: Slot[]): { cells: TimelineCell[]; startMs: number } {
  const startMs = nowMs - (((nowMs % SLOT_MS) + SLOT_MS) % SLOT_MS);
  const bySlot = new Map(slots.map((s) => [Date.parse(s.start), s]));
  const cells: TimelineCell[] = [];
  for (let i = 0; i < CELL_COUNT; i++) {
    const t = startMs + i * SLOT_MS;
    cells.push({ startMs: t, slot: bySlot.get(t) ?? null });
  }
  return { cells, startMs };
}

// Local midnights strictly inside (startMs, startMs + horizon) — day boundaries for the tick +
// weekday label under the strip.
function midnightsIn(startMs: number): { ms: number; label: string }[] {
  const endMs = startMs + HORIZON_MS;
  const out: { ms: number; label: string }[] = [];
  const d = new Date(startMs);
  d.setHours(24, 0, 0, 0); // next local midnight after startMs
  while (d.getTime() < endMs) {
    out.push({ ms: d.getTime(), label: d.toLocaleDateString([], { weekday: "short" }) });
    d.setDate(d.getDate() + 1);
  }
  return out;
}

function pctOf(ms: number, startMs: number): number | null {
  const rel = ms - startMs;
  if (rel < 0 || rel > HORIZON_MS) return null;
  return (rel / HORIZON_MS) * 100;
}

function deadlineStatus(d: Deadline): { cls: string; badge: string; label: string } {
  if (d.already_met) return { cls: "car-tick-good", badge: "badge-live", label: "met" };
  if (!d.feasible) {
    return {
      cls: "car-tick-bad",
      badge: "badge-danger",
      label: `not feasible — short ${d.shortfall_kwh.toFixed(1)} kWh`,
    };
  }
  if (d.pending_kwh > 0) return { cls: "car-tick-warn", badge: "badge-amber", label: "pending prices" };
  return { cls: "car-tick-good", badge: "badge-live", label: "planned" };
}

function SocSetForm({
  pct,
  onChange,
  onSubmit,
  onCancel,
  busy,
}: {
  pct: number;
  onChange: (n: number) => void;
  onSubmit: () => void;
  onCancel?: () => void;
  busy: boolean;
}) {
  return (
    <div className="override-controls car-anchor-form" data-testid="car-anchor-form">
      <input
        type="number"
        min={0}
        max={100}
        step={1}
        value={pct}
        onChange={(e) => onChange(Number(e.target.value))}
        aria-label="Car charge level (%)"
        className="car-pct-input"
        data-testid="car-soc-input"
      />
      <button
        type="button"
        className="btn-primary"
        disabled={busy}
        onClick={onSubmit}
        data-testid="car-soc-set"
      >
        Set
      </button>
      {onCancel && (
        <button
          type="button"
          className="btn-ghost"
          disabled={busy}
          onClick={onCancel}
          data-testid="car-anchor-cancel"
        >
          Cancel
        </button>
      )}
    </div>
  );
}

function CardHead() {
  return (
    <div className="override-head">
      <span className="metric-label">
        <span className="car-dot" aria-hidden="true" />
        Car
      </span>
    </div>
  );
}

export function CarCard({ onOpenSettings }: { onOpenSettings: () => void }) {
  const [data, setData] = useState<CarPlanResp | null>(null);
  const [pctInput, setPctInput] = useState(50);
  const [editingAnchor, setEditingAnchor] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    function load() {
      fetch("/api/car/plan")
        .then((r) => (r.ok ? r.json() : null))
        .then((b) => {
          if (alive && b) setData(b);
        })
        .catch(() => {
          /* best-effort — a failed poll just keeps the last known plan (or stays hidden) */
        });
    }
    load();
    const id = setInterval(load, REFRESH_MS);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, []);

  async function setSoc(pct: number) {
    setBusy(true);
    setErr(null);
    try {
      const r = await fetch("/api/car/soc", {
        method: "POST",
        headers: { "content-type": "application/json", ...authHeaders() },
        body: JSON.stringify({ pct }),
      });
      const b = await r.json().catch(() => ({}));
      if (r.status === 401) {
        setErr("Unauthorized — set an access token in Settings.");
      } else if (r.status === 422) {
        setErr(Object.values(b.errors ?? {}).join("; ") || "invalid charge level");
      } else if (!r.ok) {
        throw new Error(b.detail ?? `HTTP ${r.status}`);
      } else {
        setEditingAnchor(false);
        // The anchor changed both the SoC AND the plan — re-fetch the whole thing to reconcile.
        const r2 = await fetch("/api/car/plan");
        if (r2.ok) setData(await r2.json());
      }
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(false);
    }
  }

  if (!data || !data.enabled) return null;

  if (data.needs_anchor) {
    return (
      <section className="car-card" data-testid="car-card">
        <CardHead />
        <p className="override-hint">What&apos;s the car&apos;s charge now?</p>
        <SocSetForm pct={pctInput} onChange={setPctInput} onSubmit={() => setSoc(pctInput)} busy={busy} />
        {err && (
          <p className="field-err" data-testid="car-error">
            {err}
          </p>
        )}
      </section>
    );
  }

  if (data.needs_schedule) {
    return (
      <section className="car-card" data-testid="car-card">
        <CardHead />
        <p className="override-hint">No weekly minimum charge level set yet.</p>
        <button
          type="button"
          className="strategy-more"
          onClick={onOpenSettings}
          data-testid="car-schedule-link"
        >
          Set a weekly minimum in Settings → Car
        </button>
      </section>
    );
  }

  const { plan, soc } = data;
  if (!plan || !soc || plan.deadlines.length === 0) return null; // defensive: contract guarantees this

  const nextDeadline = plan.deadlines[0];
  const status = deadlineStatus(nextDeadline);
  const { cells, startMs } = buildCells(Date.now(), plan.slots);
  const midnightTicks = midnightsIn(startMs);
  const deadlineMarkers = plan.deadlines
    .map((d) => ({ d, pct: pctOf(Date.parse(d.ready_by), startMs) }))
    .filter((m): m is { d: Deadline; pct: number } => m.pct != null);
  const filledCount = cells.filter((c) => c.slot).length;
  const timelineLabel =
    `Car-charging timeline for the next 48 hours: ${filledCount} of ${cells.length} ` +
    `15-minute slots allocated to charging` +
    (deadlineMarkers.length > 0
      ? `, with ${deadlineMarkers.length} upcoming deadline${deadlineMarkers.length > 1 ? "s" : ""} marked.`
      : ".");

  return (
    <section className="car-card" data-testid="car-card">
      <CardHead />

      <div className="car-soc-row" data-testid="car-soc-row">
        <span className="car-soc-value" data-testid="car-soc-value">
          {soc.soc_pct.toFixed(1)}%
        </span>
        <span className="car-soc-meta">estimated · anchored {Math.round(soc.age_hours)}h ago</span>
        {soc.stale && (
          <span className="badge badge-amber" data-testid="car-soc-stale">
            stale
          </span>
        )}
        <button
          type="button"
          className="car-edit-btn"
          aria-label="Re-anchor the car's charge level"
          onClick={() => {
            setPctInput(Math.round(soc.soc_pct));
            setErr(null);
            setEditingAnchor((v) => !v);
          }}
          data-testid="car-reanchor-btn"
        >
          ✎
        </button>
      </div>
      {editingAnchor && (
        <SocSetForm
          pct={pctInput}
          onChange={setPctInput}
          onSubmit={() => setSoc(pctInput)}
          onCancel={() => setEditingAnchor(false)}
          busy={busy}
        />
      )}
      {err && (
        <p className="field-err" data-testid="car-error">
          {err}
        </p>
      )}

      <div className="car-deadline-chip" data-testid="car-next-deadline">
        <Icon
          name={status.cls === "car-tick-bad" ? "alert" : "check"}
          className={`car-tick ${status.cls}`}
        />
        <span className="car-deadline-text">
          {dayTime(nextDeadline.ready_by)} · ≥{nextDeadline.min_pct}%
        </span>
        <span className={`badge ${status.badge}`} data-testid="car-deadline-status">
          {status.label}
        </span>
      </div>

      <p className="car-advice" data-testid="car-advice">
        {plan.advice}
      </p>

      {plan.negative_price_hint && (
        <p className="advisor-hint" data-testid="car-negative-price-hint">
          ⚡ {plan.negative_price_hint}
        </p>
      )}

      {plan.windows.length > 0 && (
        <ul className="car-windows" data-testid="car-windows">
          {plan.windows.map((w) => (
            <li key={w.start} className="car-window-row" data-testid="car-window-row">
              {dayTime(w.start)}–{hm(w.end)} · {w.battery_kwh.toFixed(1)} kWh · ≈€
              {w.est_cost_eur.toFixed(2)} · {w.solar_share_pct}% sun
            </li>
          ))}
        </ul>
      )}

      <div className="car-timeline-wrap">
        <div
          className="car-timeline"
          data-testid="car-timeline"
          role="img"
          aria-label={timelineLabel}
        >
          <div className="car-timeline-track">
            {cells.map((c) => (
              <span
                key={c.startMs}
                data-testid="car-timeline-cell"
                className={`car-timeline-cell${
                  c.slot ? (c.slot.solar_surplus ? " car-cell-solar" : " car-cell-fill") : ""
                }`}
                title={
                  c.slot
                    ? `${fmtTime(c.startMs)} · ${
                        c.slot.solar_surplus ? "solar surplus" : "charging"
                      } · ${c.slot.battery_kwh.toFixed(1)} kWh`
                    : undefined
                }
              />
            ))}
            {midnightTicks.map((m) => (
              <div
                key={m.ms}
                className="car-timeline-midnight"
                style={{ left: `${pctOf(m.ms, startMs)}%` }}
              >
                <span className="car-timeline-day-label">{m.label}</span>
              </div>
            ))}
            {deadlineMarkers.map(({ d, pct }) => (
              <div
                key={d.ready_by}
                className="car-timeline-deadline"
                style={{ left: `${pct}%` }}
                title={`${dayTime(d.ready_by)} · ≥${d.min_pct}%`}
                data-testid="car-timeline-deadline"
              >
                <span className="car-timeline-flag" aria-hidden="true">
                  ⚑
                </span>
              </div>
            ))}
          </div>
        </div>
      </div>
    </section>
  );
}
