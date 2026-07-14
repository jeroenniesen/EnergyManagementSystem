// The Car view (feat/ux-batch-3): a first-class home for everything about the car, because the
// weekly schedule changes often and car config/insight used to be scattered across the dashboard
// card and the Settings "Car" section. In order, it assembles:
//   (a) the FULL car-charging card (the same CarCard the dashboard shows compact — SoC, deadline,
//       advice, plug-in windows, the 48h timeline);
//   (b) the weekly-minimum schedule editor + brand/model car picker, MOVED here from Settings and
//       reusing the shared components in ev.tsx. Edits save through the SAME /api/settings POST
//       path, behind this view's own sticky save bar (the user said they change the schedule
//       weekly — it must be two interactions from anywhere: nav → edit → Save);
//   (c) a compact charging-sessions history table (GET /api/car/sessions), detected on demand from
//       recorded meter history, with an honest empty state.
import { useEffect, useState } from "react";

import { authHeaders } from "./auth";
import { CarCard } from "./CarCard";
import {
  CarPicker,
  EvScheduleEditor,
  type CarModel,
  type CarsResp,
  defaultSchedule,
} from "./ev";

type Values = Record<string, number | boolean | string>;
// The three settings the Car view owns (moved out of the Settings "Car" section): the picker sets
// car_id + autofills battery_kwh; the editor sets the weekly schedule JSON.
const EV_KEYS = ["ev.car_id", "ev.battery_kwh", "ev.schedule"] as const;

type Session = { start: string; end: string; kwh: number; avg_kw: number; peak_kw: number };
type SessionsResp = { sessions: Session[]; days: number };

function dayTime(iso: string): string {
  const d = new Date(iso);
  const day = d.toLocaleDateString([], { weekday: "short" });
  const time = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
  return `${day} ${time}`;
}

function hm(iso: string): string {
  return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
}

function SessionsTable({ sessions }: { sessions: Session[] | null }) {
  return (
    <div className="car-sessions" data-testid="car-sessions">
      <span className="metric-label">Recent charging</span>
      <p className="settings-group-hint">
        Charging sessions detected from your meter history over the last 14 days.
      </p>
      {sessions === null ? (
        <div className="loading">Reading charging history…</div>
      ) : sessions.length === 0 ? (
        <p className="plan-reason" data-testid="car-sessions-empty">
          No charging sessions detected in the last 14 days.
        </p>
      ) : (
        <ul className="car-sessions-list" data-testid="car-sessions-list">
          {sessions.map((s) => (
            <li key={s.start} className="car-session-row" data-testid="car-session-row">
              <span className="car-session-when">
                {dayTime(s.start)}–{hm(s.end)}
              </span>
              <span className="car-session-stats">
                {s.kwh.toFixed(1)} kWh · avg {s.avg_kw.toFixed(1)} kW
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export function CarView({ onOpenSettings }: { onOpenSettings?: () => void }) {
  const [values, setValues] = useState<Values>({});
  const [edited, setEdited] = useState<Values>({});
  const [cars, setCars] = useState<CarModel[] | null>(null);
  const [status, setStatus] = useState<"idle" | "saving" | "saved" | "error">("idle");
  const [saveErr, setSaveErr] = useState<string | null>(null);
  const [sessions, setSessions] = useState<Session[] | null>(null);

  // Current ev.* values from the shared settings store — the same GET Settings uses, so a value
  // saved in either surface shows up in the other. Only the values are needed here (the editor +
  // picker are hardcoded components, not schema-driven).
  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const r = await fetch("/api/settings");
        if (!r.ok) return;
        const b: { values?: Values } = await r.json();
        if (alive && b.values) {
          const seed: Values = {};
          for (const k of EV_KEYS) if (k in b.values) seed[k] = b.values[k];
          setValues(seed);
          setEdited(seed);
        }
      } catch {
        /* best-effort — the editor still renders on defaults */
      }
    })();
    return () => {
      alive = false;
    };
  }, []);

  // Static car database for the picker (ems/cars.py) — degrades to "Custom" only on failure.
  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const r = await fetch("/api/cars");
        if (!r.ok) return;
        const b: CarsResp = await r.json();
        if (alive) setCars(b.cars);
      } catch {
        /* best-effort */
      }
    })();
    return () => {
      alive = false;
    };
  }, []);

  // Detected charging sessions for the history table (newest-first from the endpoint).
  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const r = await fetch("/api/car/sessions?days=14");
        if (!r.ok) {
          if (alive) setSessions([]);
          return;
        }
        const b: SessionsResp = await r.json();
        if (alive) setSessions(b.sessions ?? []);
      } catch {
        if (alive) setSessions([]);
      }
    })();
    return () => {
      alive = false;
    };
  }, []);

  function set(key: string, v: number | boolean | string) {
    setEdited((prev) => ({ ...prev, [key]: v }));
    setStatus("idle");
  }

  const changed: Values = {};
  for (const k of EV_KEYS) if (k in edited && edited[k] !== values[k]) changed[k] = edited[k];
  const changeCount = Object.keys(changed).length;
  const dirty = changeCount > 0;

  async function save() {
    setStatus("saving");
    setSaveErr(null);
    try {
      const r = await fetch("/api/settings", {
        method: "POST",
        headers: { "content-type": "application/json", ...authHeaders() },
        body: JSON.stringify(changed),
      });
      if (r.status === 401) {
        setSaveErr("Unauthorized — set an access token in Manage → Settings.");
        setStatus("error");
        return;
      }
      const b = await r.json().catch(() => ({}));
      if (r.status === 422) {
        setSaveErr(Object.values(b.errors ?? {}).join("; ") || "Some values were rejected.");
        setStatus("error");
        return;
      }
      if (!r.ok) throw new Error(b.detail ?? `HTTP ${r.status}`);
      // Reconcile: prefer the server's echoed values, else fold in what we just changed. Works
      // against both the real backend (returns the full values map) and a partial test echo.
      const merged: Values = { ...values, ...changed, ...(b.values ?? {}) };
      const seed: Values = {};
      for (const k of EV_KEYS) if (k in merged) seed[k] = merged[k];
      setValues(seed);
      setEdited(seed);
      setStatus("saved");
    } catch (e) {
      setSaveErr(String(e));
      setStatus("error");
    }
  }

  function reset() {
    setEdited(values);
    setStatus("idle");
    setSaveErr(null);
  }

  const selectedCar = (cars ?? []).find((c) => c.id === String(edited["ev.car_id"] ?? "")) ?? null;
  const scheduleValue = String(edited["ev.schedule"] ?? JSON.stringify(defaultSchedule()));
  const showSaveBar = dirty || status === "saved";

  return (
    <section data-testid="car-view">
      {/* (a) The full car-charging card (same component as the dashboard, non-compact). */}
      <CarCard />

      {/* (b) Config: car picker + weekly schedule, moved here from Settings. */}
      <div className="car-config" data-testid="car-config">
        <span className="metric-label">Your car &amp; weekly schedule</span>
        <p className="settings-group-hint">
          Pick your car (capacity &amp; AC limit) and set the minimum charge to reach each morning.
          Charger power, efficiency and the advice toggle live in{" "}
          {onOpenSettings ? (
            <button type="button" className="link-inline" data-testid="car-config-settings-link"
              onClick={onOpenSettings}>
              Manage → Settings → Car
            </button>
          ) : (
            <span>Manage → Settings → Car</span>
          )}
          .
        </p>

        <div className="car-config-field" data-testid="car-config-picker">
          <label className="field-label">Car</label>
          <CarPicker
            carId={String(edited["ev.car_id"] ?? "")}
            cars={cars ?? []}
            disabled={status === "saving"}
            onPick={(car) => {
              if (car) {
                set("ev.car_id", car.id);
                set("ev.battery_kwh", car.battery_net_kwh);
              } else {
                set("ev.car_id", "");
              }
            }}
          />
          {selectedCar && (
            <p className="advisor-hint" data-testid="car-config-ac-hint">
              {selectedCar.model}&apos;s onboard AC charger tops out at{" "}
              <strong>{selectedCar.max_ac_kw} kW</strong> — the car, not the wallbox, caps the
              charge speed above that.
            </p>
          )}
        </div>

        <div className="car-config-field" data-testid="car-config-battery">
          <label className="field-label" htmlFor="set-ev.battery_kwh">
            Battery capacity <span className="field-unit">(kWh)</span>
          </label>
          <input
            id="set-ev.battery_kwh"
            type="number"
            min={10}
            max={150}
            step={0.5}
            value={String(edited["ev.battery_kwh"] ?? 57.5)}
            disabled={status === "saving"}
            onChange={(e) => {
              const n = Number(e.target.value);
              set("ev.battery_kwh", Number.isFinite(n) ? n : 57.5);
            }}
          />
          <p className="field-help">
            Usable battery capacity — autofilled from the car picker; override if you know better.
          </p>
        </div>

        <div className="car-config-field" data-testid="car-config-schedule">
          <label className="field-label">Weekly minimum charge</label>
          <EvScheduleEditor
            value={scheduleValue}
            disabled={status === "saving"}
            onChange={(v) => set("ev.schedule", v)}
          />
        </div>
      </div>

      {/* (c) Charging-sessions history. */}
      <SessionsTable sessions={sessions} />

      {/* Own sticky save bar (reuses the Settings sticky-save pattern), scoped to the car config. */}
      {showSaveBar && (
        <div className="settings-savebar" data-testid="car-savebar">
          <div className="settings-savebar-inner">
            <div className="settings-savebar-msg">
              {dirty && (
                <span className="settings-dirty" data-testid="car-dirty">
                  {changeCount} unsaved change{changeCount === 1 ? "" : "s"}
                </span>
              )}
              {status === "saved" && (
                <span className="settings-msg-ok" data-testid="car-saved">
                  Saved
                </span>
              )}
              {status === "error" && saveErr && (
                <span className="settings-msg-err" data-testid="car-save-error">{saveErr}</span>
              )}
            </div>
            <div className="settings-savebar-actions">
              <button className="btn-ghost" data-testid="car-discard" onClick={reset}
                disabled={!dirty}>
                Discard
              </button>
              <button className="btn-primary" data-testid="car-save" onClick={save}
                disabled={!dirty || status === "saving"}>
                {status === "saving" ? "Saving…" : "Save"}
              </button>
            </div>
          </div>
        </div>
      )}
    </section>
  );
}
