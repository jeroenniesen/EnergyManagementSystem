// The Car view (feat/ux-batch-3): a first-class home for everything about the car, because the
// weekly schedule changes often and car config/insight used to be scattered across the dashboard
// card and the Settings "Car" section. In order, it assembles:
//   (a) the FULL car-charging card (the same CarCard the dashboard shows compact — SoC, deadline,
//       advice, plug-in windows, the 48h timeline);
//   (b) "While the car charges" (feat/car-charge-modes) — the home-BATTERY's behaviour during a
//       charging session (a completely different concern from (a)'s advisory charge-timing plan):
//       the master switch + three keyboard-accessible radio-card modes (hold / a fixed discharge
//       wattage / match the predicted house load), moved out of Settings' "Control & safety" group
//       the same way the fields in (c) were moved out of its "Car" group. Saves immediately (the
//       heating mark-as-done idiom — optimistic patch, POST the changed keys, roll back on
//       failure), NOT through this view's sticky save bar below, since there's nothing to draft;
//   (c) the weekly-minimum schedule editor + brand/model car picker, MOVED here from Settings and
//       reusing the shared components in ev.tsx. Edits save through the SAME /api/settings POST
//       path, behind this view's own sticky save bar (the user said they change the schedule
//       weekly — it must be two interactions from anywhere: nav → edit → Save);
//   (d) a compact charging-sessions history table (GET /api/car/sessions), detected on demand from
//       recorded meter history, with an honest empty state.
import { useEffect, useRef, useState } from "react";

import { apiFetch } from "./auth";
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

// --- "While the car charges" battery-mode section (feat/car-charge-modes) ----------------------
// Mirrors ems/control/car_mode.py's three modes + ems/settings.py's defaults, so a partial/missing
// GET /api/settings response (or a fresh install) degrades to exactly the backend's own default
// shape: hold selected, master on, 800 W.
type CarChargingMode = "hold" | "static_discharge" | "match_home_load";
const CAR_MODE_ORDER: CarChargingMode[] = ["hold", "static_discharge", "match_home_load"];
const CAR_MODE_DEFAULTS = { holdEnabled: true, mode: "hold" as CarChargingMode, dischargeW: 800 };
const CAR_MODE_MIN_W = 100;
const CAR_MODE_MAX_W = 5000;
const CAR_MODE_STEP_W = 50;

type CarModeState = { holdEnabled: boolean; mode: CarChargingMode; dischargeW: number };

const CAR_MODE_TITLE: Record<CarChargingMode, string> = {
  hold: "Hold the battery",
  static_discharge: "Help with a fixed power",
  match_home_load: "Cover the house automatically",
};

// Base (mode-independent) description; static_discharge's physics line is appended separately
// (CarModeSection) once the entered wattage is known, and match_home_load's live "~N W" is filled
// in here from the freshest reading available (see `houseLoadW`).
function carModeDesc(key: CarChargingMode, houseLoadW: number | null): string {
  switch (key) {
    case "hold":
      return "The battery pauses; solar + grid cover the house and car. Safest, and the default.";
    case "static_discharge":
      return "Discharges at the fixed wattage below while the car charges.";
    case "match_home_load":
      return houseLoadW != null
        ? `The battery quietly covers the home's predicted use (~${Math.round(houseLoadW)} W ` +
          "right now) so the car charges purely on grid + solar."
        : "The battery quietly covers the home's predicted use so the car charges purely on grid " +
          "+ solar.";
  }
}

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
    <div className="car-sessions" data-testid="car-sessions" data-density-kind="card">
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

// The three-radio-card picker + master toggle. Each card is a <button role="radio"> (roving
// tabindex + arrow-key navigation, the same pattern as StrategyCard's segmented control) so the
// wattage input can sit as a plain SIBLING of the button when static_discharge is selected —
// nesting a real <input> inside a <button> would be invalid HTML. Space/Enter select the focused
// card for free (native button semantics); arrows move + select, roving-tabindex style.
function CarModeSection({
  carMode,
  onToggleHold,
  onSelectMode,
  onCommitWatts,
  houseLoadW,
  status,
  error,
  disabled = false,
}: {
  carMode: CarModeState;
  onToggleHold: (next: boolean) => void;
  onSelectMode: (next: CarChargingMode) => void;
  onCommitWatts: (next: number) => void;
  houseLoadW: number | null;
  status: "idle" | "saving" | "saved" | "error";
  error: string | null;
  // Reader read-only mode (auth slice 2 web): these three settings save IMMEDIATELY on
  // interaction (no sticky save bar to gate), so the controls themselves must be disabled.
  // Defaults false so every other caller is unaffected.
  disabled?: boolean;
}) {
  const optionRefs = useRef<(HTMLButtonElement | null)[]>([]);
  const [wattsRaw, setWattsRaw] = useState(String(carMode.dischargeW));
  useEffect(() => {
    setWattsRaw(String(carMode.dischargeW));
  }, [carMode.dischargeW]);

  function commitWatts() {
    const n = Number(wattsRaw);
    const clamped = Number.isFinite(n)
      ? Math.max(CAR_MODE_MIN_W, Math.min(CAR_MODE_MAX_W, Math.round(n / CAR_MODE_STEP_W) * CAR_MODE_STEP_W))
      : carMode.dischargeW;
    setWattsRaw(String(clamped));
    if (clamped !== carMode.dischargeW) onCommitWatts(clamped);
  }

  function onOptionKeyDown(e: React.KeyboardEvent<HTMLButtonElement>, idx: number) {
    const fwd = e.key === "ArrowRight" || e.key === "ArrowDown";
    const back = e.key === "ArrowLeft" || e.key === "ArrowUp";
    if (!fwd && !back) return;
    e.preventDefault();
    const next = (idx + (fwd ? 1 : -1) + CAR_MODE_ORDER.length) % CAR_MODE_ORDER.length;
    onSelectMode(CAR_MODE_ORDER[next]);
    optionRefs.current[next]?.focus();
  }

  const enteredW = Number(wattsRaw);
  const showWattsWarning =
    carMode.mode === "static_discharge" && Number.isFinite(enteredW) &&
    houseLoadW != null && enteredW > houseLoadW;

  return (
    <div className="car-mode" data-testid="car-battery-mode" data-density-kind="card">
      <div className="car-mode-head">
        <span className="metric-label">While the car charges</span>
        <label className="switch-row" data-testid="car-mode-hold-row">
          <button
            type="button"
            role="switch"
            aria-checked={carMode.holdEnabled}
            aria-label="Special battery behaviour while the car charges"
            disabled={disabled}
            className={`switch${carMode.holdEnabled ? " switch-on" : ""}`}
            data-testid="car-mode-hold-toggle"
            onClick={() => onToggleHold(!carMode.holdEnabled)}
          >
            <span className="switch-knob" />
          </button>
          <span className="switch-label">{carMode.holdEnabled ? "On" : "Off"}</span>
        </label>
      </div>
      <p className="settings-group-hint">
        {carMode.holdEnabled
          ? "The battery follows the mode you pick below whenever the car is charging."
          : "Off — the planner runs exactly as it would with no car; the battery is untouched. " +
            "Turn this on to apply the mode you pick below."}
      </p>

      <div className="car-mode-options" role="radiogroup" aria-label="While the car charges">
        {CAR_MODE_ORDER.map((key, i) => {
          const selected = carMode.mode === key;
          return (
            <div
              key={key}
              className={`car-mode-option${selected ? " car-mode-option-selected" : ""}`}
              data-testid={`car-mode-option-${key}`}
            >
              <button
                type="button"
                role="radio"
                aria-checked={selected}
                tabIndex={selected ? 0 : -1}
                disabled={disabled}
                ref={(el) => {
                  optionRefs.current[i] = el;
                }}
                className="car-mode-option-head"
                data-testid={`car-mode-${key}`}
                onClick={() => onSelectMode(key)}
                onKeyDown={(e) => onOptionKeyDown(e, i)}
              >
                <span className="car-mode-radio-dot" aria-hidden="true" />
                <span className="car-mode-option-body">
                  <span className="car-mode-option-title">{CAR_MODE_TITLE[key]}</span>
                  <span className="car-mode-option-desc">{carModeDesc(key, houseLoadW)}</span>
                </span>
              </button>
              {selected && key === "static_discharge" && (
                <div className="car-mode-watts" data-testid="car-mode-watts">
                  <label className="field-label" htmlFor="car-mode-watts-input">
                    Discharge power <span className="field-unit">(W)</span>
                  </label>
                  <div className="car-mode-watts-row">
                    <input
                      id="car-mode-watts-input"
                      type="number"
                      min={CAR_MODE_MIN_W}
                      max={CAR_MODE_MAX_W}
                      step={CAR_MODE_STEP_W}
                      value={wattsRaw}
                      disabled={disabled}
                      data-testid="car-mode-watts-input"
                      onChange={(e) => setWattsRaw(e.target.value)}
                      onBlur={commitWatts}
                      onKeyDown={(e) => {
                        if (e.key === "Enter") (e.target as HTMLInputElement).blur();
                      }}
                    />
                  </div>
                  {showWattsWarning && (
                    <p className="car-mode-warning" data-testid="car-mode-watts-warning">
                      Above your home&apos;s usual draw — the extra feeds the car from the battery,
                      which is your choice.
                    </p>
                  )}
                </div>
              )}
            </div>
          );
        })}
      </div>

      {status === "saved" && (
        <p className="settings-msg-ok car-mode-status" data-testid="car-mode-saved">
          Saved
        </p>
      )}
      {status === "error" && (
        <p className="settings-msg-err car-mode-status" data-testid="car-mode-error">
          {error ?? "Couldn't save — try again."}
        </p>
      )}
    </div>
  );
}

export function CarView({
  onOpenSettings,
  canOperate = true,
}: {
  onOpenSettings?: () => void;
  // Reader read-only mode (auth slice 2 web): every mutating control here — the car-mode
  // toggle/radios/watts, the car picker, the capacity field, the schedule editor, and the sticky
  // save bar — is disabled/hidden for a reader. Defaults true so every other caller is unaffected.
  canOperate?: boolean;
}) {
  const [values, setValues] = useState<Values>({});
  const [edited, setEdited] = useState<Values>({});
  const [cars, setCars] = useState<CarModel[] | null>(null);
  const [status, setStatus] = useState<"idle" | "saving" | "saved" | "error">("idle");
  const [saveErr, setSaveErr] = useState<string | null>(null);
  const [sessions, setSessions] = useState<Session[] | null>(null);

  // "While the car charges" battery-mode state (feat/car-charge-modes) — separate from the
  // ev.*/values-edited-dirty-bar state above: these three keys save IMMEDIATELY on selection (the
  // heating mark-as-done idiom), never through this view's sticky save bar.
  const [carMode, setCarMode] = useState<CarModeState>(CAR_MODE_DEFAULTS);
  const [carModeStatus, setCarModeStatus] = useState<"idle" | "saving" | "saved" | "error">("idle");
  const [carModeErr, setCarModeErr] = useState<string | null>(null);
  // The smallest honest source for "what's the house using right now": the same coalesced reading
  // /api/status already exposes as `non_ev_load_w` (App.tsx's "Home use" tile) — no new backend
  // read needed. Used both for match_home_load's live "~N W" copy and as the static_discharge
  // physics-warning threshold. null (fetch failed/unavailable) hides both rather than guessing.
  const [houseLoadW, setHouseLoadW] = useState<number | null>(null);

  // Current ev.* values from the shared settings store — the same GET Settings uses, so a value
  // saved in either surface shows up in the other. Only the values are needed here (the editor +
  // picker are hardcoded components, not schema-driven). The three car-mode keys ride the SAME
  // fetch (one GET, not two) into their own immediate-save state above.
  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const r = await apiFetch("/api/settings");
        if (!r.ok) return;
        const b: { values?: Values } = await r.json();
        if (!alive || !b.values) return;
        const seed: Values = {};
        for (const k of EV_KEYS) if (k in b.values) seed[k] = b.values[k];
        setValues(seed);
        setEdited(seed);

        const v = b.values;
        const holdRaw = v["control.hold_battery_when_car_charging"];
        const modeRaw = v["control.car_charging_battery_mode"];
        const wattsRaw = v["control.car_discharge_w"];
        setCarMode({
          holdEnabled: typeof holdRaw === "boolean" ? holdRaw : CAR_MODE_DEFAULTS.holdEnabled,
          mode: (CAR_MODE_ORDER as string[]).includes(String(modeRaw))
            ? (modeRaw as CarChargingMode)
            : CAR_MODE_DEFAULTS.mode,
          dischargeW: typeof wattsRaw === "number" ? wattsRaw : CAR_MODE_DEFAULTS.dischargeW,
        });
      } catch {
        /* best-effort — the editor still renders on defaults */
      }
    })();
    return () => {
      alive = false;
    };
  }, []);

  // Live non-EV house load, for the match_home_load card's "~N W" and the static_discharge
  // warning threshold — a light poll (the read is already coalesced server-side, no flood risk).
  useEffect(() => {
    let alive = true;
    function load() {
      apiFetch("/api/status")
        .then((r) => (r.ok ? r.json() : null))
        .then((b: { non_ev_load_w?: number } | null) => {
          if (alive && b && typeof b.non_ev_load_w === "number") setHouseLoadW(b.non_ev_load_w);
        })
        .catch(() => {
          /* best-effort — the cards just render without the live figure */
        });
    }
    load();
    const id = setInterval(load, 30000);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, []);

  // Immediate-save helper for the car-mode section: optimistic patch (already applied by the
  // caller before calling this), POST the changed key(s), roll back on failure — same idiom as
  // HeatingAdvice's mark-as-done. Never touches the ev.*/schedule dirty-bar state above.
  function postCarMode(body: Record<string, number | boolean | string>, rollback: () => void) {
    setCarModeStatus("saving");
    setCarModeErr(null);
    apiFetch("/api/settings", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    })
      .then(async (r) => {
        if (r.status === 401) {
          // apiFetch already cleared the (now-invalid) token and triggered the central 401
          // handler, which bounces to <Login/> — nothing to show here (dead paste-token-box
          // copy removed).
          return;
        }
        if (!r.ok) {
          const b = await r.json().catch(() => ({}));
          throw new Error(
            r.status === 422
              ? Object.values(b.errors ?? {}).join("; ") || "Some values were rejected."
              : (b.detail ?? `HTTP ${r.status}`),
          );
        }
        setCarModeStatus("saved");
      })
      .catch((e) => {
        rollback();
        setCarModeErr(String(e instanceof Error ? e.message : e));
        setCarModeStatus("error");
      });
  }

  function toggleCarModeHold(next: boolean) {
    const prev = carMode.holdEnabled;
    setCarMode((c) => ({ ...c, holdEnabled: next }));
    postCarMode(
      { "control.hold_battery_when_car_charging": next },
      () => setCarMode((c) => ({ ...c, holdEnabled: prev })),
    );
  }

  function selectCarMode(next: CarChargingMode) {
    if (next === carMode.mode) return; // already selected — nothing to save
    const prev = carMode.mode;
    const body: Record<string, number | boolean | string> = {
      "control.car_charging_battery_mode": next,
    };
    // Selecting the fixed-power mode posts its wattage alongside the mode in the SAME request, so
    // the two keys that define this behaviour are always saved together.
    if (next === "static_discharge") body["control.car_discharge_w"] = carMode.dischargeW;
    setCarMode((c) => ({ ...c, mode: next }));
    postCarMode(body, () => setCarMode((c) => ({ ...c, mode: prev })));
  }

  function commitCarModeWatts(next: number) {
    const prev = carMode.dischargeW;
    setCarMode((c) => ({ ...c, dischargeW: next }));
    postCarMode(
      { "control.car_discharge_w": next },
      () => setCarMode((c) => ({ ...c, dischargeW: prev })),
    );
  }

  // Static car database for the picker (ems/cars.py) — degrades to "Custom" only on failure.
  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const r = await apiFetch("/api/cars");
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
        const r = await apiFetch("/api/car/sessions?days=14");
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
      const r = await apiFetch("/api/settings", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(changed),
      });
      if (r.status === 401) {
        // apiFetch already cleared the (now-invalid) token and triggered the central 401
        // handler, which bounces to <Login/> — nothing to show here (dead paste-token-box
        // copy removed).
        setStatus("idle");
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
  const showSaveBar = canOperate && (dirty || status === "saved");
  const fieldsDisabled = status === "saving" || !canOperate;

  return (
    <section data-testid="car-view" data-density-surface="car">
      {/* (a) The full car-charging card (same component as the dashboard, non-compact). Threads
          the same onOpenSettings this view already has (used below by the config-section link) so
          the feature-off empty state can send you to Manage → Settings → Car too. */}
      <CarCard onOpenSettings={onOpenSettings} canOperate={canOperate} />

      {/* (b) The home-battery's behaviour while the car charges (feat/car-charge-modes) — saves
          immediately, independent of this view's own sticky save bar below. */}
      <CarModeSection
        carMode={carMode}
        onToggleHold={toggleCarModeHold}
        onSelectMode={selectCarMode}
        onCommitWatts={commitCarModeWatts}
        houseLoadW={houseLoadW}
        status={carModeStatus}
        error={carModeErr}
        // NOT gated on carModeStatus === "saving" — these controls never disabled during their
        // own immediate-save round-trip before this slice, and the arrow-key/space keyboard
        // interaction test depends on that (a disabled button can't hold focus). Reader read-only
        // mode is the only new reason to disable them.
        disabled={!canOperate}
      />

      {/* (c) Config: car picker + weekly schedule, moved here from Settings. */}
      <div className="car-config" data-testid="car-config" data-density-kind="card">
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
            disabled={fieldsDisabled}
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
            disabled={fieldsDisabled}
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
            disabled={fieldsDisabled}
            onChange={(v) => set("ev.schedule", v)}
          />
        </div>
      </div>

      {/* (d) Charging-sessions history. */}
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
