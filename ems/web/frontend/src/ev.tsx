// Shared EV config controls — the weekly-schedule editor and the brand/model car picker, plus the
// small pure helpers they need. Extracted from Settings.tsx (feat/ux-batch-3) so BOTH the Car view
// (their new home — see Car.tsx) and Settings can reuse the exact same components rather than
// forking. Everything here is presentational: the caller owns the value + persistence (both save
// through the same /api/settings POST path).

import { useEffect, useState } from "react";

// The `ev.car_id` setting (a stable slug or "" for custom) is rendered as brand/model pickers
// backed by GET /api/cars.
export type CarModel = {
  id: string;
  brand: string;
  model: string;
  battery_net_kwh: number;
  max_ac_kw: number;
  years: string;
};
export type CarsResp = { brands: string[]; cars: CarModel[] };

// The `ev.schedule` setting is a JSON string under the hood, rendered as a 7-day editor.
export type DayKey = "mon" | "tue" | "wed" | "thu" | "fri" | "sat" | "sun";
export type ScheduleDay = { enabled: boolean; min_pct: number; ready_by: string };
export type Schedule = Record<DayKey, ScheduleDay>;

export const DAY_ORDER: DayKey[] = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"];
export const DAY_LABEL: Record<DayKey, string> = {
  mon: "Monday", tue: "Tuesday", wed: "Wednesday", thu: "Thursday",
  fri: "Friday", sat: "Saturday", sun: "Sunday",
};
const DEFAULT_SCHEDULE_DAY: ScheduleDay = { enabled: false, min_pct: 80, ready_by: "07:30" };
const TIME_RE = /^([01]\d|2[0-3]):([0-5]\d)$/;

export function defaultSchedule(): Schedule {
  const out = {} as Schedule;
  for (const day of DAY_ORDER) out[day] = { ...DEFAULT_SCHEDULE_DAY };
  return out;
}

// Mirrors ems/ev_schedule.py's tolerant `parse_schedule` closely enough for the editor: any
// garbage collapses to the default shape rather than ever throwing mid-render.
export function parseScheduleClient(raw: string): Schedule {
  let data: unknown;
  try {
    data = JSON.parse(raw);
  } catch {
    return defaultSchedule();
  }
  if (typeof data !== "object" || data === null) return defaultSchedule();
  const out = defaultSchedule();
  for (const day of DAY_ORDER) {
    const rawDay = (data as Record<string, unknown>)[day];
    if (!rawDay || typeof rawDay !== "object") continue;
    const d = rawDay as Record<string, unknown>;
    const minPctNum = Number(d.min_pct);
    const min_pct = Number.isFinite(minPctNum)
      ? Math.max(0, Math.min(100, Math.round(minPctNum)))
      : 80;
    const ready_by = typeof d.ready_by === "string" && TIME_RE.test(d.ready_by)
      ? d.ready_by
      : "07:30";
    out[day] = { enabled: Boolean(d.enabled), min_pct, ready_by };
  }
  return out;
}

export function EvScheduleEditor({
  value,
  disabled,
  onChange,
}: {
  value: string;
  disabled: boolean;
  onChange: (v: string) => void;
}) {
  const schedule = parseScheduleClient(value);
  function updateDay(day: DayKey, patch: Partial<ScheduleDay>) {
    onChange(JSON.stringify({ ...schedule, [day]: { ...schedule[day], ...patch } }));
  }
  return (
    <div className="ev-schedule" data-testid="ev-schedule-editor">
      <div className="ev-schedule-grid">
        <div className="ev-schedule-row ev-schedule-head" aria-hidden="true">
          <span />
          <span>Day</span>
          <span>Min %</span>
          <span>Ready by</span>
        </div>
        {DAY_ORDER.map((day) => {
          const d = schedule[day];
          return (
            <div className="ev-schedule-row" key={day} data-testid={`ev-schedule-row-${day}`}>
              <input
                type="checkbox"
                checked={d.enabled}
                disabled={disabled}
                aria-label={`Enable ${DAY_LABEL[day]}`}
                data-testid={`ev-schedule-${day}-enabled`}
                onChange={(e) => updateDay(day, { enabled: e.target.checked })}
              />
              <span className="ev-schedule-day">{DAY_LABEL[day]}</span>
              <input
                type="number"
                min={0}
                max={100}
                step={5}
                value={d.min_pct}
                disabled={disabled || !d.enabled}
                aria-label={`${DAY_LABEL[day]} minimum percent`}
                data-testid={`ev-schedule-${day}-min-pct`}
                onChange={(e) => {
                  const n = Number(e.target.value);
                  const clamped = Number.isFinite(n) ? Math.max(0, Math.min(100, Math.round(n))) : 0;
                  updateDay(day, { min_pct: clamped });
                }}
              />
              <input
                type="time"
                value={d.ready_by}
                disabled={disabled || !d.enabled}
                aria-label={`${DAY_LABEL[day]} ready by`}
                data-testid={`ev-schedule-${day}-ready-by`}
                onChange={(e) => updateDay(day, { ready_by: e.target.value || "07:30" })}
              />
            </div>
          );
        })}
      </div>
    </div>
  );
}

// Brand → Model picker for `ev.car_id`, backed by GET /api/cars. "Custom" (empty brand) clears
// `ev.car_id` — the user then enters battery_kwh/charger_kw themselves. Picking a model hands the
// full CarModel back to the caller, which autofills battery_kwh but deliberately leaves charger_kw
// alone (the wallbox is a separate physical thing — see the inline AC-limit hint next to it).
export function CarPicker({
  carId,
  cars,
  disabled,
  onPick,
}: {
  carId: string;
  cars: CarModel[];
  disabled: boolean;
  onPick: (car: CarModel | null) => void;
}) {
  const selected = cars.find((c) => c.id === carId) ?? null;
  const brands = [...new Set(cars.map((c) => c.brand))].sort();
  // Local brand selection: normally mirrors the selected car's brand, but must survive being set
  // ahead of a model choice (brand picked, no model yet) — so it isn't derived every render.
  const [brand, setBrand] = useState<string>(selected?.brand ?? "");
  useEffect(() => {
    setBrand(selected?.brand ?? "");
  }, [carId, selected?.brand]);
  const models = brand ? cars.filter((c) => c.brand === brand) : [];

  return (
    <div className="car-picker" data-testid="car-picker">
      <div className="car-picker-row">
        <select
          aria-label="Car brand"
          data-testid="car-brand-select"
          disabled={disabled}
          value={brand}
          onChange={(e) => {
            const b = e.target.value;
            setBrand(b);
            if (!b) onPick(null); // "Custom" — clear the car, keep any manually-set capacity
          }}
        >
          <option value="">Custom</option>
          {brands.map((b) => (
            <option key={b} value={b}>{b}</option>
          ))}
        </select>
        <select
          aria-label="Car model"
          data-testid="car-model-select"
          disabled={disabled || !brand}
          value={selected?.id ?? ""}
          onChange={(e) => {
            const car = cars.find((c) => c.id === e.target.value) ?? null;
            onPick(car);
          }}
        >
          <option value="">{brand ? "Choose a model…" : "—"}</option>
          {models.map((c) => (
            <option key={c.id} value={c.id}>{c.model}</option>
          ))}
        </select>
      </div>
      {selected && (
        <p className="car-picker-specs" data-testid="car-picker-specs">
          {selected.battery_net_kwh} kWh usable · {selected.max_ac_kw} kW AC max
        </p>
      )}
    </div>
  );
}
