export type StorySlot = {
  start: string;
  soc_pct: number | null;
  grid_w: number;
  solar_w: number;
  battery_w: number;
  load_w: number;
  eur_per_kwh: number | null;
  action: string;
};

export type StoryTotals = {
  import_kwh: number;
  export_kwh: number;
  solar_kwh: number;
  charge_kwh: number;
  discharge_kwh: number;
  load_kwh: number;
  grid_cost_eur: number | null;
  self_sufficiency_pct: number | null;
  soc_start_pct: number | null;
  soc_end_pct: number | null;
  soc_min_pct: number | null;
  soc_max_pct: number | null;
};

export type EnergyStoryData = {
  window: "past" | "next";
  now: string;
  current_soc_pct: number | null;
  reserve_soc_pct: number;
  target_soc_pct: number | null;
  target_kwh: number | null;
  target_deadline: string | null;
  current_price_eur_per_kwh: number | null;
  slots: StorySlot[];
  totals: StoryTotals;
  headline: string;
  trust_markers?: string[];
  recent?: StorySlot[];
  recent_hours?: number;
  on_track?: {
    status: "ahead" | "on_track" | "behind" | "unknown";
    actual_soc_pct: number;
    target_soc_pct: number;
    deficit_kwh: number;
    message: string;
  };
  recent_review?: {
    message: string;
    solar_actual_kwh: number;
    solar_forecast_kwh: number | null;
    solar_pct_of_forecast: number | null;
  };
};

export type PlanConfidence = {
  level: "high" | "medium" | "low";
  reasons: string[];
};

export type PlanProvenance = {
  forecast_source: string;
  solar_confidence_pct: number;
  planner: "rule_based" | "adaptive" | "summer";
  intelligence: {
    state: string;
    last_evaluated_at: string | null;
    last_result: string | null;
    reason: string;
  };
};

export type SavedToday =
  | { status: "measured"; eur: number }
  | { status: "measuring" };

export type BatteryPlanData = {
  status: "on_track" | "needs_topup" | "behind_target" | "paused_safely" | "data_stale";
  summary: string;
  current_action:
    | "grid_charge"
    | "solar_charge"
    | "hold"
    | "discharge"
    | "self_consume"
    | "paused";
  current_reason: string;
  window_start: string;
  window_end: string;
  current_soc_pct: number | null;
  reserve_soc_pct: number;
  target_soc_pct: number | null;
  target_deadline: string | null;
  planned_grid_topup_kwh?: number;
  deviation: {
    status: "ok" | "behind_forecast" | "missing";
    message: string;
  };
  warnings: string[];
  graph: {
    forecast_soc: Array<{ ts: string; soc_pct: number | null }>;
    actual_soc: Array<{ ts: string; soc_pct: number | null }>;
    reserve_line: Array<{ ts: string; soc_pct: number | null }>;
    target_line: Array<{ ts: string; soc_pct: number | null }>;
    planned_actions: Array<{ start: string; end: string; action: string }>;
    price_windows: Array<{
      start: string;
      end: string;
      min_eur_per_kwh: number;
      max_eur_per_kwh: number;
    }>;
    solar: Array<{ ts: string; forecast_w: number; actual_w: number | null }>;
  };
  confidence?: PlanConfidence;
  provenance?: PlanProvenance;
};
