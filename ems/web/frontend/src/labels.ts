// Plain-language labels for the backend's internal enums and signal keys, so a homeowner never
// sees a raw token like "unsafe", "dry_run" or "solcast: stale". Each map has a humanize() fallback
// so an unmapped value still reads as a phrase ("grid_charge_to_target" → "Grid charge to target")
// instead of leaking snake_case to the screen. Shared by the dashboard, override and system views.

/** Turn a snake_case / kebab token into a readable, sentence-cased phrase. */
export function humanize(token: string): string {
  const words = token.replace(/[_-]+/g, " ").replace(/\s+/g, " ").trim();
  return words ? words.charAt(0).toUpperCase() + words.slice(1) : token;
}

/**
 * B-09 / #73 — emotionally complete "EMS unreachable" banner copy.
 * Three lines only: what's wrong, failsafe (never a live mode claim), and "Laatst bekend hh:mm".
 * Does not say "the battery is safe" / "nothing changes" — the client cannot confirm AUTO.
 * B-98: Dutch-first on Home (same fold as "Laatst bekend").
 */
export const EMS_UNREACHABLE = {
  message: "EMS is niet bereikbaar vanaf dit apparaat.",
  /** Failsafe intent only — not a live battery reading. No "network loss" claim: the client
   * cannot tell EMS-down apart from a viewer-side network problem. */
  ems_doing:
    "In kijkmodus verandert EMS je batterij nooit. Na een schone stop gaat de batterij terug " +
    "naar zelfgebruik; als EMS zelf uitvalt, blijft de laatste modus staan tot EMS terug is.",
} as const;

/** Format the B-09 "last contact" line. `atMs` null → never reached (cold fail). */
export function formatLaatstBekend(atMs: number | null): string {
  if (atMs == null || !Number.isFinite(atMs)) {
    return "Laatst bekend —";
  }
  const d = new Date(atMs);
  const hh = String(d.getHours()).padStart(2, "0");
  const mm = String(d.getMinutes()).padStart(2, "0");
  return `Laatst bekend ${hh}:${mm}`;
}

type Labelled = { label: string; title: string };

/** Run mode: is the system actually commanding the battery, or only watching? (B-98 Dutch-first) */
export const RUN_MODE: Record<"dry" | "live", Labelled> = {
  dry: {
    label: "Alleen kijken",
    title: "Het systeem toont wat het zou doen, maar verandert de batterij nooit.",
  },
  live: {
    label: "Stuurt batterij",
    title: "Het systeem schakelt de batterij actief tussen modi.",
  },
};

/** Short labels for /api/status dry_run_cause (#178) — full text stays in dry_run_reason. */
export const DRY_RUN_CAUSE_LABEL: Record<string, string> = {
  mock: "demo/mock",
  config_dry_run: "config.yaml alleen kijken",
  settings_dry_run: "Instellingen alleen kijken",
  unarmed: "niet bewapend",
  no_live_prices: "geen live prijzen",
  no_live_devices: "geen live apparaten",
  observing: "observatieperiode",
  dry_run: "alleen kijken",
};

/** Empty-cause fallback next to Alleen kijken when dry_run_reason is set without a cause key. */
export const DRY_RUN_CAUSE_FALLBACK = "oorzaak";

/** Data source: real sensors vs. the built-in simulator. (B-98 Dutch-first next to Alles actueel) */
export const DATA_SOURCE: Record<"live" | "sim", Labelled> = {
  live: {
    label: "Live sensoren",
    title: "Meetwaarden komen van je echte meters en batterij.",
  },
  // Issue #79: short "Demo" so web + phone share the same glanceable word.
  sim: {
    label: "Demo",
    title: "Meetwaarden komen van de ingebouwde simulator, niet van je huis.",
  },
};

/** Overall data quality / system health → friendly phrase + explanation. (B-98 Dutch-first) */
export const DATA_QUALITY: Record<string, Labelled> = {
  complete: {
    label: "Alles actueel",
    title: "Alle sensoren en voorspellingen zijn bijgewerkt.",
  },
  degraded: {
    // Issue #79: never "Gedegradeerd" — consumer phrase naming partial staleness.
    label: "Deels verouderd",
    title: "Sommige sensor- of voorspellingsdata is verouderd, dus het plan kan minder precies zijn.",
  },
  price_fallback: {
    label: "Geen actuele prijzen",
    title: "Actuele Tibber-prijzen ontbreken, dus er wordt een fallback-/demo-prijscurve gebruikt.",
  },
  unsafe: {
    label: "Gepauzeerd — zelfgebruik",
    title:
      "Data ontbreekt of is verouderd, dus EMS zet de batterij terug naar zelfgebruik.",
  },
};

/** Dashboard consumer sources for the compact freshness strip (issue #79). */
export const CONSUMER_SOURCES = ["battery", "grid", "prices", "forecast"] as const;
export type ConsumerSource = (typeof CONSUMER_SOURCES)[number];

export const CONSUMER_SOURCE_LABEL: Record<ConsumerSource, string> = {
  battery: "Batterij",
  grid: "P1-meter",
  prices: "Prijzen",
  forecast: "Zonvoorspelling",
};

export type DeviceHealthSummary = {
  badge: string;
  label: string;
  detail: string;
  severity: string;
};

export type DeviceHealthSource = {
  key: string;
  label: string;
  state: string;
  updated_at?: string | null;
  updated_hhmm?: string | null;
  age_seconds?: number | null;
  note?: string | null;
};

export type DeviceHealth = {
  sources: DeviceHealthSource[];
  battery_reachable?: boolean | null;
  forecast_age_seconds?: number | null;
  prices_kind?: string;
  summary: DeviceHealthSummary;
};

/**
 * Alert keys the device-health strip matches onto each source row (#73 three answers).
 * Prices prefer #126 / PR #148 live-price keys (`no_live_prices`, `tibber_prices_unavailable`)
 * ahead of freshness / horizon warnings.
 */
export const DEVICE_HEALTH_SOURCE_ALERT_KEYS: Record<string, readonly string[]> = {
  grid: ["grid_stale", "grid_missing"],
  battery: ["battery_stale", "battery_missing", "soc_stale", "soc_missing"],
  prices: [
    "no_live_prices",
    "tibber_prices_unavailable",
    "prices_stale",
    "prices_missing",
    "price_horizon_incomplete",
  ],
  forecast: ["forecast_stale", "forecast_missing"],
};

/** First matching alert for a strip source, preferring the order in DEVICE_HEALTH_SOURCE_ALERT_KEYS. */
export function pickDeviceHealthAlert<T extends { key: string }>(
  sourceKey: string,
  alerts: readonly T[] | null | undefined,
): T | null {
  const keys = DEVICE_HEALTH_SOURCE_ALERT_KEYS[sourceKey] ?? [];
  if (!alerts?.length || !keys.length) return null;
  for (const key of keys) {
    const hit = alerts.find((a) => a.key === key);
    if (hit) return hit;
  }
  return null;
}

/** Plain-language detail like "prijzen van 14:00". */
export function sourceDetail(key: string, hhmm: string | null | undefined): string {
  const name =
    key === "battery"
      ? "batterij"
      : key === "grid"
        ? "P1-meter"
        : key === "prices"
          ? "prijzen"
          : key === "forecast"
            ? "zonvoorspelling"
            : key;
  return hhmm ? `${name} van ${hhmm}` : name;
}

/**
 * Prefer the backend `device_health.summary` as source of truth.
 * Fall back to a local "Deels verouderd" / Demo only when the API section is absent
 * (older servers / fan-out path). Severity matches backend: critical only for missing grid.
 */
export function summarizeDeviceHealth(
  freshness: Record<string, string> | null | undefined,
  deviceHealth: DeviceHealth | null | undefined,
  isDemo: boolean,
): DeviceHealthSummary {
  if (deviceHealth?.summary) return deviceHealth.summary;

  const fromApi = deviceHealth?.sources ?? [];
  const byKey = new Map(fromApi.map((s) => [s.key, s]));
  const staleKeys = CONSUMER_SOURCES.filter((key) => {
    const state = byKey.get(key)?.state ?? freshness?.[key];
    return state === "stale" || state === "missing";
  });
  if (staleKeys.length > 0) {
    const key = staleKeys[0];
    const hhmm = byKey.get(key)?.updated_hhmm ?? null;
    const state = byKey.get(key)?.state ?? freshness?.[key];
    return {
      badge: "partially_stale",
      label: "Deels verouderd",
      detail: sourceDetail(key, hhmm),
      severity: key === "grid" && state === "missing" ? "critical" : "warning",
    };
  }
  if (isDemo) {
    return {
      badge: "demo",
      label: "Demo",
      detail: "Cijfers komen niet van jouw huis — dit is demodata.",
      severity: "warning",
    };
  }
  return {
    badge: "current",
    label: "Alles actueel",
    detail: "Batterij, P1-meter, prijzen en zonvoorspelling zijn bijgewerkt.",
    severity: "ok",
  };
}

/** Plain-language confidence behind the current plan, keyed by data-quality level. (B-98) */
export const CONFIDENCE: Record<string, string> = {
  complete: "Hoog vertrouwen — alle data is actueel.",
  degraded: "Goed — sommige niet-kritische data is vertraagd of geschat.",
  price_fallback: "Fallback-prijscurve in gebruik — prijsgebaseerde stappen zijn gepauzeerd.",
  unsafe:
    "Plan gepauzeerd tot batterij-/meterdata terug is; EMS mikt op zelfgebruik van de batterij.",
};

/** System-view overall health badge (same palette as data quality). Sentence-case "Check" so this
 * chip and the per-check-row STATUS_LABEL ("Check") read as the same vocabulary — a shouty
 * all-caps "NEEDS A LOOK" (this label is upper-cased by CSS, like every badge) came across as an
 * alarm in production screenshots for something that's calm-safe, not broken. */
export const SYSTEM_OVERALL: Record<string, Labelled> = {
  ok: { label: "All good", title: "Every readiness check passed." },
  warn: { label: "Check", title: "One or more checks want attention, but nothing is broken." },
  fail: { label: "Problem", title: "A readiness check failed — see the list below." },
};

/** Friendly names for the per-signal freshness keys. (B-98 Dutch-first) */
export const SIGNAL_NAME: Record<string, string> = {
  grid: "P1-meter",
  solar: "Zonmeter",
  soc: "Batterijniveau",
  battery: "Batterij",
  ev: "Autolader",
  tibber_price: "Prijzen",
  prices: "Prijzen",
  solar_forecast: "Zonvoorspelling",
  solcast: "Zonvoorspelling",
  forecast: "Zonvoorspelling",
};

/** Freshness states in plain words. (B-98 Dutch-first — DeviceHealth + Advanced) */
export const FRESHNESS_STATE: Record<string, string> = {
  fresh: "actueel",
  stale: "vertraagd",
  missing: "niet beschikbaar",
};

/** The dashboard's car badge (App.tsx), keyed by the controller's `desired_mode` (from
 * /api/decision) — feat/car-charge-modes made the badge mode-aware, since the guard now has three
 * behaviours instead of always holding. `desired_mode: "discharge"` here reliably means the narrow
 * car-session mapping is active (`intent_to_mode(..., car_session=True)`, SPEC §7.1) — the operator
 * picked `static_discharge`/`match_home_load` and the battery is actually covering the house — so
 * it's the one token worth a distinct phrase; every other mode (idle/auto/charge) is the safe hold,
 * today's default, byte-for-byte. Fall back to CAR_BADGE_SUFFIX_DEFAULT for anything unmapped. */
export const CAR_BADGE_SUFFIX: Record<string, string> = {
  discharge: "battery covering the house",
};
export const CAR_BADGE_SUFFIX_DEFAULT = "battery held";

/** Controller decision outcomes in plain words (every token the controller/API can emit). (B-98) */
export const OUTCOME_LABEL: Record<string, string> = {
  dry_run: "Alleen kijken",
  not_controlling: "Alleen kijken",
  applied: "Toegepast",
  idempotent: "Geen wijziging nodig",
  dwell: "Wachten voor omschakelen",
  cap_reached: "Dagelijkse schakellimiet bereikt",
  no_plan: "Nog geen plan",
  unconfigured: "Niet ingesteld",
  failed_recovered: "Hersteld — veilige modus",
  failed_unrecovered: "Fout — veilige modus",
};

/** The battery's physical running mode, in plain words. */
export const PHYSICAL_MODE: Record<string, string> = {
  auto: "Self-use (auto)", // vendor self-consumption / P1-zeroing
  charge: "Charging",
  discharge: "Powering the house",
  idle: "Holding",
};

/** Control-incident types (from /api/incidents), in plain words — see export_package.incident_rollup. */
export const INCIDENT_TYPE_LABEL: Record<string, string> = {
  cluster_mismatch: "Cluster mismatch",
  command_failed: "Command failed",
  fallback: "Fell back to a safe default",
  revert: "Reverted to safe mode",
};

/** Production feedback ("don't know what action I need to take here"): each incident TYPE gets a
 * short, honest "what to do" line under its count row on the System page (B-37 parity) — covers
 * every key in INCIDENT_TYPE_LABEL above. command_failed/cluster_mismatch point at something
 * concrete to check; fallback/revert are EMS protecting itself, so the copy stays reassuring
 * rather than inventing a fix — only recurring incidents warrant a look. */
export const INCIDENT_TYPE_ACTION: Record<string, string> = {
  command_failed:
    "The battery didn't confirm some commands; EMS retried or fell back safely. If this keeps " +
    "growing, check the Indevolt gateway's power/network.",
  cluster_mismatch:
    "A tower is running its own mode — power-cycle it if it hasn't rejoined after a few cycles.",
  fallback:
    "EMS chose a safe default rather than risk an uncertain command — no action needed unless " +
    "this keeps recurring.",
  revert:
    "EMS reverted to the battery's own safe mode to protect your home — no action needed unless " +
    "this keeps recurring.",
};

/** Model-health track verdict (B-76, from /api/accuracy's `health` block) → dot colour + text
 * label (never colour-only) + a title for the "still collecting evidence" honest empty state.
 * "Check" (not "Needs a look") matches the System page's check-row STATUS_LABEL convention. */
export const HEALTH_STATUS: Record<"ok" | "warn" | "unknown", Labelled> = {
  ok: { label: "Working well", title: "The recent evidence looks dependable." },
  warn: { label: "Check", title: "There is something worth reviewing below." },
  unknown: { label: "Still collecting evidence", title: "Not enough history yet to judge this." },
};

/** Row titles for the Model-health panel (B-76), keyed the same as /api/accuracy's health block. */
export const HEALTH_ROW_LABEL: Record<"solar" | "load" | "plan_execution", string> = {
  solar: "Solar outlook",
  load: "Home energy pattern",
  plan_execution: "Plan follow-through",
};

/** Plan-provenance line (feat/ux-batch-3): which planner FUNCTION produced the live plan, in plain
 * words, keyed the same as /api/battery-plan's `provenance.planner`. "rule_based" only ever occurs
 * for the winter arbitrage planner; "adaptive"/"summer" only ever occur for the summer solar-first
 * strategy (ems.web.api's `_resolved_planner_name` mirrors ems.planner.strategy.build_plan's own
 * dispatch), so the season is safely folded into the copy without a separate field. */
export const PLANNER_PROVENANCE_LABEL: Record<string, string> = {
  rule_based: "rule-based winter planner",
  adaptive: "adaptive summer planner",
  summer: "solar-first summer planner",
};

/** Scenario/ML planning-intelligence layer status (CLAUDE.md honesty ask, feat/ux-batch-3; B-79
 * made this runtime-derived): ems/intelligence/planning.py builds pessimistic/expected/optimistic
 * planning scenarios (E-08); whether it's actually evaluating/steering is now tracked at runtime,
 * not assumed. `/api/battery-plan`'s `provenance.intelligence` and `GET /api/intelligence` both
 * return the SAME object — `{state, last_evaluated_at, last_result, reason}` — computed by the
 * backend's `_intelligence_status()` from its `intelligence_box` evaluation record. There is no
 * "current mode" constant here anymore: BatteryPlan.tsx reads `provenance.intelligence.state` off
 * the live plan; System.tsx fetches `/api/intelligence` itself and reads its `.state`. This map
 * only supplies the plain-language copy for each of the four states the backend can report. */
export const INTELLIGENCE_COPY: Record<string, { label: string; detail: string; short: string }> = {
  not_active: {
    label: "Planning intelligence",
    detail: "not active; the dependable baseline plans today",
    short: "not active yet",
  },
  shadow_evaluation: {
    label: "Planning intelligence",
    detail: "evaluating in shadow — comparing against the baseline, not steering",
    short: "shadow (not steering)",
  },
  advisory: {
    label: "Planning intelligence",
    detail: "advisory — surfacing suggestions; the baseline still steers",
    short: "advisory",
  },
  active: {
    label: "Planning intelligence",
    detail: "active — steering the plan",
    short: "active",
  },
};

/**
 * B-98 — Dutch-first Home chrome (first viewport + PlanStory + adjacent topbar status).
 * Prefer this table over ad-hoc English strings on the Home fold. Keep Waarom? / Nu: as already
 * productized NL. Operator/planner tokens under Waarom? or provenance may stay English.
 */
export const HOME_ACT = {
  calm: "Niets nodig van jou.",
  override:
    "Je bent handmatig aan het sturen — het stopt vanzelf, of wis het hieronder.",
  unsafe:
    "EMS zet de batterij terug naar zelfgebruik tot de meterdata weer betrouwbaar is — " +
    "niets te doen; het hervat vanzelf.",
} as const;

export const HOME_CONFIDENCE_CHIP: Record<"high" | "medium" | "low", string> = {
  high: "Hoog vertrouwen",
  medium: "Middelmatig vertrouwen",
  low: "Laag vertrouwen",
};

export const HOME_HERO = {
  /** Calm synthesis fallback when on-track / reason / score are absent or jargon. */
  fallback: "Batterij volgt het huidige plan.",
  /** Dutch on-track phrasing when the API sends English on_track.message. */
  onTrack: "Op schema voor het nachtdoel vanavond.",
  behind: "Nog niet op het doel.",
  runningTonight: "De batterij voedt vanavond de woning.",
  demoLead: "Dit is een demo-huis.",
  demoLink: "Gebruik mijn echte huis →",
  demoDismissAria: "Nu sluiten",
} as const;

/**
 * Small readiness.home_state headline set (ems/readiness.home_state) → Dutch for the Home fold.
 * Unknown English controlling phrases fall through the action-phrase map; last resort keeps tone
 * calm by preferring a short Dutch watching/attention line over raw EN.
 */
export const HOME_HEADLINE = {
  needsAttention: "Aandacht nodig — batterij- of meterdata ontbreekt",
  manualControl: "Je hebt handmatige controle",
  allGood: "Alles goed",
  watching: "Aan het kijken",
  toppingUp: "Batterij bijladen",
  runningHouse: "De batterij voedt de woning",
  holding: "Batterij vasthouden voor later",
  batterySafe: "De batterij is veilig",
  batteryRunningHouse: "de batterij voedt de woning",
} as const;

const _HOME_ACTION_PHRASE_NL: Record<string, string> = {
  "topping up the battery": HOME_HEADLINE.toppingUp,
  "running the house on your battery": HOME_HEADLINE.runningHouse,
  "holding the battery for later": HOME_HEADLINE.holding,
  "the battery is running the house": HOME_HEADLINE.batteryRunningHouse,
  "the battery is safe": HOME_HEADLINE.batterySafe,
  "your home": "je huis",
};

function _titleCaseNl(phrase: string): string {
  if (!phrase) return phrase;
  return phrase.charAt(0).toUpperCase() + phrase.slice(1);
}

function _mapHomeActionPhrase(phrase: string): string | null {
  const key = phrase.trim().toLowerCase();
  return _HOME_ACTION_PHRASE_NL[key] ?? null;
}

/** Map readiness.home_state.headline (EN) to Dutch for the hero verdict. */
export function homeHeadlineNl(headline: string | null | undefined): string {
  const h = (headline ?? "").trim();
  if (!h) return HOME_HEADLINE.watching;

  if (h.startsWith("Needs attention")) return HOME_HEADLINE.needsAttention;
  if (h === "You're in manual control") return HOME_HEADLINE.manualControl;

  if (h.startsWith("All good")) {
    const dash = h.indexOf("—");
    if (dash >= 0) {
      const rest = _mapHomeActionPhrase(h.slice(dash + 1).trim());
      if (rest) return `${HOME_HEADLINE.allGood} — ${rest}`;
    }
    return HOME_HEADLINE.allGood;
  }

  if (h.startsWith("Watching")) {
    const dash = h.indexOf("—");
    if (dash >= 0) {
      const rest = _mapHomeActionPhrase(h.slice(dash + 1).trim());
      if (rest) return `${HOME_HEADLINE.watching} — ${rest}`;
    }
    // e.g. "Watching your home"
    const after = h.slice("Watching".length).trim();
    const rest = after ? _mapHomeActionPhrase(after) : null;
    return rest ? `${HOME_HEADLINE.watching} — ${rest}` : HOME_HEADLINE.watching;
  }

  const controlling = _mapHomeActionPhrase(h);
  if (controlling) return _titleCaseNl(controlling);

  // Do not leave raw English in the hero fold.
  return HOME_HEADLINE.watching;
}

/**
 * Translate B-68 confidence reason strings (ems/confidence.py) for the hero sub-line.
 * Returns null when the reason is unmapped English — hide rather than flip-flop locale.
 */
export function homeConfidenceReasonNl(reason: string | null | undefined): string | null {
  const r = (reason ?? "").trim();
  if (!r) return null;

  if (r.startsWith("Safety fallback active")) {
    return "Veiligheidsfallback actief — EMS houdt vast, plant niet.";
  }
  if (r.startsWith("The battery isn't answering")) {
    return "De batterij antwoordt nu niet, dus het plan is niet betrouwbaar.";
  }
  if (r.startsWith("Some live data is stale")) {
    return "Sommige live data is verouderd, dus het plan is nu niet te vertrouwen.";
  }
  if (r.startsWith("Still learning your roof")) {
    return "Nog je dak aan het leren — nog weinig zonvoorspellingsbewijs.";
  }
  if (r.startsWith("No forecast evidence yet")) {
    return "Nog geen voorspellingsbewijs — de eerste zonnige dagen worden vastgelegd.";
  }
  if (r.startsWith("Live prices are unavailable")) {
    return "Live prijzen ontbreken — het plan draait op een fallback-prijssignaal.";
  }
  if (r.startsWith("Some sensor or forecast data is stale")) {
    return "Sommige sensor- of voorspellingsdata is verouderd — kwaliteit van het plan is lager.";
  }
  if (r.startsWith("There's no live price signal") || r.startsWith("There is no live price signal")) {
    return "Geen live prijssignaal — het plan draait alleen op de voorspelling.";
  }
  if (r.startsWith("Solar forecasts have been running")) {
    return "Zonvoorspellingen lopen de laatste tijd te hoog/laag — dimensionering is minder precies.";
  }
  if (r.startsWith("Fresh data, calibrated forecast")) {
    return "Actuele data, gekalibreerde voorspelling, batterij reageert — niets houdt dit plan tegen.";
  }
  if (r.startsWith("Data quality is currently")) {
    return "Datakwaliteit beperkt het planvertrouwen nu.";
  }
  // Already Dutch (or unknown locale) — show as-is only if it does not look like EN API copy.
  if (
    /\b(the|your|for|with|from|battery|learning|safety|fresh|solar|tonight|target|house|running|watching|needs|attention|still|fallback|stale|plan)\b/i.test(
      r,
    )
  ) {
    return null;
  }
  return r;
}

export const HOME_MORE_TOGGLE = "Meer van je huis";

export const HOME_TILE = {
  sectionAria: "Tot nu toe vandaag",
  staleUpdated: "Verouderd · Bijgewerkt",
  solarScore: {
    label: "Zonnescore",
    title: "Zonnescore tot nu toe vandaag",
    unavailable: "Zonnescore niet beschikbaar",
  },
  soc: {
    label: "Batterijniveau",
    title: "Actueel batterijniveau",
    unavailable: "Actueel batterijniveau niet beschikbaar",
  },
  savings: {
    label: "Bespaard",
    title: "Besparing tot nu toe vandaag",
    measuring: "Besparing tot nu toe vandaag: nog meten",
    unavailable: "Besparing tot nu toe vandaag niet beschikbaar",
  },
  gridImport: {
    label: "Netinvoer",
    title: "Netinvoer tot nu toe vandaag",
    unavailable: "Netinvoer tot nu toe vandaag niet beschikbaar",
  },
} as const;

export const HOME_EVENING_PEAK = {
  trustMarker: "Batterij dekt de avondpiek",
  chancePrefix: "Kans dat de batterij de avondpiek dekt:",
  uncalibratedSuffix: " (standaardbanden tot er meer voorspellingsgeschiedenis is)",
} as const;

/** PlanStory legend / header / axis / tip chrome (B-98). Chart geometry unchanged. */
export const HOME_PLAN_STORY = {
  recentReview: "Laatste 3 uur",
  recordedBattery: "Gemeten batterij",
  forecastBattery: "Voorspelde batterij",
  unavailable: "Batterijplan niet beschikbaar.",
  plannedWith: "Gepland met",
  now: "nu",
  tipRecorded: "gemeten",
  tipForecast: "voorspelling",
  tipBattery: "Batterijniveau",
  tipPrice: "Prijs",
  tipSolar: "Zon",
  tipAction: "Actie",
  tipGridFlow: "Netstroom",
  tipImport: "invoer",
  tipExport: "uitvoer",
  loading: "Batterijplan wordt geladen.",
  labelPrefix: "Batterijplan:",
  noRecordedData: "Geen gemeten data",
  noForecastData: "Geen voorspellingsdata",
  batteryAtNow: (pct: number) => `Batterij nu op ${pct}%.`,
  batteryUnavailable: "Batterijniveau niet beschikbaar.",
  nightTarget: (pct: number, by: string) =>
    `Nachtdoel ${pct}%${by ? ` om ${by}` : ""}.`,
  minReserve: (pct: number) => `Minimumreserve ${pct}%.`,
  priceRange: (min: string, max: string) => `Prijs €${min}–€${max}`,
  solarRange: (maxW: string) => `Zon 0–${maxW} W`,
  reserveLabel: (pct: number) => `reserve ${pct}%`,
  targetLabel: (pct: number) => `doel ${pct}%`,
} as const;

/**
 * PlanStory action ribbon / legend labels (B-98). Aligned with decisionWhy "Nu: …" vocabulary
 * where the same action appears (laden / zelfconsumptie / vasthouden).
 */
export const PLAN_ACTION_META: Record<
  "solar_charge" | "grid_charge" | "discharge" | "self_consume" | "hold" | "idle",
  { label: string; phrase: string }
> = {
  solar_charge: {
    label: "Laden van zonnepanelen",
    phrase: "Laadt van zonnepanelen",
  },
  grid_charge: {
    label: "Laden van het net",
    phrase: "Laadt van het net",
  },
  discharge: {
    label: "Zelfconsumptie",
    phrase: "Voedt de woning",
  },
  self_consume: {
    label: "Zelfconsumptie",
    phrase: "Zelfconsumptie",
  },
  hold: {
    label: "Vasthouden",
    phrase: "Houdt vast",
  },
  idle: {
    label: "Inactief",
    phrase: "Staat stil",
  },
};
