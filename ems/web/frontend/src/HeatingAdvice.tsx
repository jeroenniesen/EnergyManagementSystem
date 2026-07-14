// Advice-only heating recommendations (BACKLOG B-11 / roadmap phase F7 — the biggest absolute
// CO2/€ prize left once solar + battery are handled, and the EMS has zero control over the boiler).
// House style: evidence -> suggestion -> "you decide" (see the solar-confidence advisor). Framed
// entirely by the household's own metered gas — never a generic tip list, and never nagging: it
// only renders when /api/report's `gas` is non-null (a gas meter is actually configured).
// Renders directly under GasPanel in Insights.tsx.
//
// Mark-as-done (production feedback: "can't check these items as done" — these are one-off jobs,
// not a recurring habit): state lives in ONE settings field, `heating.done` (ems/settings.py), a
// JSON object of {itemKey: "YYYY-MM-DD"}. Saved through the SAME /api/settings endpoint Settings
// uses, but posted immediately on click — a tiny optimistic patch + rollback on failure — never via
// the Settings dirty-bar/save button; this is app state the user just acted on, not a draft edit to
// review before saving (see App.tsx's patchStrategy for the same immediate-POST idiom).
import { useEffect, useState } from "react";

export type GasSummary = { m3: number; kwh_eq: number; eur: number; co2_kg: number };
export type Period = "day" | "week" | "month" | "year";

const WINDOW_NOUN: Record<Period, string> = {
  day: "today",
  week: "this week",
  month: "this month",
  year: "this year",
};

// Fallback window length when window_start/window_end aren't available — matches resolve_window's
// nominal period lengths (ems/reporting.py) closely enough for an honest ballpark.
const NOMINAL_DAYS: Record<Period, number> = { day: 1, week: 7, month: 30, year: 365 };

// Below this average m3/day, a home is essentially DHW-only (summer, no space heating) — Dutch
// heating-season use typically runs several times higher. Only softens the pitch; never hides it.
const LOW_USE_M3_PER_DAY = 1.5;

// The three one-off advice items, keyed exactly as stored in `heating.done`. This is the "not done
// yet" default order — done items sort below it (see sortedItems in HeatingAdvice).
type AdviceKey = "balancing" | "flow_temp" | "dhw_eco";
const ADVICE_KEYS: AdviceKey[] = ["balancing", "flow_temp", "dhw_eco"];
// Short past-tense summary for the collapsed line ("✓ Balanced radiators — done 15 Jul · Undo").
const DONE_LABEL: Record<AdviceKey, string> = {
  balancing: "Balanced radiators",
  flow_temp: "Lowered the flow temperature",
  dhw_eco: "Hot water on eco",
};

function windowNoun(period: Period, partial: boolean): string {
  const base = WINDOW_NOUN[period];
  return partial ? `${base} so far` : base;
}

function windowDays(period: Period, windowStart?: string, windowEnd?: string): number {
  if (windowStart && windowEnd) {
    const ms = Date.parse(windowEnd) - Date.parse(windowStart);
    if (Number.isFinite(ms) && ms > 0) return ms / 86_400_000;
  }
  return NOMINAL_DAYS[period];
}

// Honest annualisation of THIS window's gas spend at a flat rate (10-15% -> midpoint 12.5% for
// balancing), rounded to the nearest €10 so it never reads as more precise than it is.
function annualizedEur(eurThisWindow: number, days: number, rate: number): number {
  const perYear = eurThisWindow * (365 / days) * rate;
  return Math.round(perYear / 10) * 10;
}

// Local-calendar YYYY-MM-DD (same convention as Insights.tsx's todayStr) — the date a card was
// marked done. Stored and compared purely as a date string; no timezone math needed downstream.
function todayStr(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(
    d.getDate(),
  ).padStart(2, "0")}`;
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

// "2026-07-15" -> "15 Jul" for the collapsed line — short, locale-independent, unambiguous.
function formatDoneDate(iso: string): string {
  const d = new Date(`${iso}T00:00:00`);
  if (Number.isNaN(d.getTime())) return iso;
  return `${d.getDate()} ${MONTHS[d.getMonth()]}`;
}

type AdviceCardProps = {
  testId: string;
  title: string;
  evidence: string;
  body: string;
  saving: string;
  details: string;
  safety?: string;
  doneLabel: string;
  doneDate?: string;
  busy: boolean;
  saveError: boolean;
  onMarkDone: () => void;
  onUndo: () => void;
};

function AdviceCard({
  testId,
  title,
  evidence,
  body,
  saving,
  details,
  safety,
  doneLabel,
  doneDate,
  busy,
  saveError,
  onMarkDone,
  onUndo,
}: AdviceCardProps) {
  if (doneDate) {
    // Collapsed to a single reassuring line — the full advice text (already acted on) is gone,
    // not just hidden, so a done card never still reads as "more to do".
    return (
      <div className="advice-card advice-card-done" data-testid={testId} data-done="true">
        <p className="advice-done-line">
          <span className="advice-done-check" aria-hidden="true">
            ✓
          </span>{" "}
          {doneLabel} — done {formatDoneDate(doneDate)} ·{" "}
          <button
            type="button"
            className="advice-undo"
            data-testid={`${testId}-undo`}
            onClick={onUndo}
            disabled={busy}
          >
            Undo
          </button>
        </p>
        {saveError && (
          <p className="advice-action-error" data-testid={`${testId}-error`}>
            Couldn&apos;t save — try again.
          </p>
        )}
      </div>
    );
  }
  return (
    <div className="advice-card" data-testid={testId}>
      <h4 className="advice-card-title">{title}</h4>
      <p className="advice-evidence">{evidence}</p>
      <p className="advice-body">{body}</p>
      <p className="advice-saving">{saving}</p>
      {safety && (
        <p className="advice-safety" data-testid={`${testId}-safety`}>
          <strong>Safety:</strong> {safety}
        </p>
      )}
      <details className="advice-details">
        <summary>How to do it</summary>
        <p>{details}</p>
      </details>
      <p className="advice-disclaimer">Advice only — nothing changes automatically.</p>
      <div className="advice-card-actions">
        {saveError && (
          <span className="advice-action-error" data-testid={`${testId}-error`}>
            Couldn&apos;t save — try again.
          </span>
        )}
        <button
          type="button"
          className="advice-mark-done"
          data-testid={`${testId}-mark-done`}
          onClick={onMarkDone}
          disabled={busy}
        >
          {busy ? "Saving…" : "Mark as done"}
        </button>
      </div>
    </div>
  );
}

export function HeatingAdvice({
  gas,
  partial,
  period,
  windowStart,
  windowEnd,
}: {
  gas: GasSummary;
  partial: boolean;
  period: Period;
  windowStart?: string;
  windowEnd?: string;
}) {
  const days = windowDays(period, windowStart, windowEnd);
  const evidence = `Your gas use ${windowNoun(period, partial)}: ${gas.m3.toFixed(1)} m³ ≈ €${gas.eur.toFixed(2)}.`;
  const balancingEur = annualizedEur(gas.eur, days, 0.125);
  const lowUse = gas.m3 / days < LOW_USE_M3_PER_DAY;

  // {itemKey: "YYYY-MM-DD"} — loaded best-effort from the ONE settings field this feature owns
  // (heating.done), the same idiom WhatIf's counterfactual header uses: never blocks the cards
  // rendering, and a failed/slow fetch just means every card starts "not done" until it resolves.
  const [doneMap, setDoneMap] = useState<Record<string, string>>({});
  const [busyKey, setBusyKey] = useState<AdviceKey | null>(null);
  const [errorKey, setErrorKey] = useState<AdviceKey | null>(null);

  useEffect(() => {
    let alive = true;
    fetch("/api/settings")
      .then((r) => (r.ok ? r.json() : null))
      .then((b: { values?: Record<string, unknown> } | null) => {
        if (!alive || !b) return;
        const raw = b.values?.["heating.done"];
        if (typeof raw !== "string") return;
        try {
          const parsed: unknown = JSON.parse(raw);
          if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
            setDoneMap(parsed as Record<string, string>);
          }
        } catch {
          /* malformed stored value — leave every card "not done" rather than crash */
        }
      })
      .catch(() => {
        /* best-effort, like the rest of Insights' sub-panels */
      });
    return () => {
      alive = false;
    };
  }, []);

  // Save the ONE `heating.done` key immediately: optimistic patch, POST, roll back + flag the item
  // on failure. Never touches the Settings dirty-bar/save flow.
  function save(key: AdviceKey, next: Record<string, string>, prev: Record<string, string>) {
    setBusyKey(key);
    setErrorKey(null);
    setDoneMap(next);
    fetch("/api/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ "heating.done": JSON.stringify(next) }),
    })
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
      })
      .catch(() => {
        setDoneMap(prev);
        setErrorKey(key);
      })
      .finally(() => {
        setBusyKey(null);
      });
  }

  function markDone(key: AdviceKey) {
    save(key, { ...doneMap, [key]: todayStr() }, doneMap);
  }

  function undo(key: AdviceKey) {
    const next = { ...doneMap };
    delete next[key];
    save(key, next, doneMap);
  }

  const allDone = ADVICE_KEYS.every((k) => doneMap[k]);

  const items: { key: AdviceKey; props: Omit<
    AdviceCardProps,
    "doneDate" | "busy" | "saveError" | "onMarkDone" | "onUndo"
  > }[] = [
    {
      key: "balancing",
      props: {
        testId: "advice-balancing",
        title: "Balance your radiators (waterzijdig inregelen)",
        evidence,
        body: `Radiators heating unevenly? Balancing typically saves 10–15% of gas (~€${balancingEur}/yr for your usage — rough estimate from your meter).`,
        saving: "Typical saving: 10–15% of gas, as a one-off (redo only if radiators change).",
        details:
          "A heating engineer, or a DIY balancing kit with a clip-on thermometer, adjusts each radiator's lockshield valve so hot water reaches every radiator evenly — instead of the nearest ones taking most of the flow and starving the far ones.",
        doneLabel: DONE_LABEL.balancing,
      },
    },
    {
      key: "flow_temp",
      props: {
        testId: "advice-flow-temp",
        title: 'Lower the boiler flow temperature ("zet \'m op 60")',
        evidence,
        body: "Condensing boilers only condense — and save gas — when the water returning to them is cool enough. Lowering the central-heating (CV) flow temperature to 60°C leaves comfort unchanged in most homes.",
        saving: "Typical saving: 5–10% of gas.",
        details:
          "Boiler menu → CV/flow temperature. Turn it down in steps of about 5°C over a few days and check the coldest rooms still reach temperature on the coldest days; only turn it back up if one struggles.",
        doneLabel: DONE_LABEL.flow_temp,
      },
    },
    {
      key: "dhw_eco",
      props: {
        testId: "advice-dhw-eco",
        title: "Hot water: eco mode, safely",
        evidence,
        body: "Switch the boiler's hot-water (DHW) mode to eco or a schedule so it stops reheating a tank nobody's drawing from.",
        saving: "Typical saving: 2–5% of gas from fewer reheat cycles — smaller than the other two, but free.",
        safety:
          "Keep stored tap water at 60°C or higher, always — that's the margin that prevents Legionella. Eco/schedule mode changes WHEN the boiler reheats, never how hot the stored water gets; never set it below 60°C.",
        details:
          "Boiler menu → hot-water / DHW schedule. Match reheating to when you actually use hot water (e.g. off overnight) — leave the storage target itself at 60°C or above.",
        doneLabel: DONE_LABEL.dhw_eco,
      },
    },
  ];

  // Not-done first, done below — a stable sort keeps each group in its original order.
  const sortedItems = [...items].sort((a, b) => {
    const aDone = doneMap[a.key] ? 1 : 0;
    const bDone = doneMap[b.key] ? 1 : 0;
    return aDone - bDone;
  });

  return (
    <div className="heating-advice" data-testid="heating-advice">
      <h3 className="card-title flow-title heating-advice-title">
        <span className="gas-dot" aria-hidden="true" />
        {allDone ? "Heating — all three done ✓" : "Heating — the biggest lever left"}
      </h3>
      <div className="advice-cards">
        {sortedItems.map((item) => (
          <AdviceCard
            key={item.key}
            {...item.props}
            doneDate={doneMap[item.key]}
            busy={busyKey === item.key}
            saveError={errorKey === item.key}
            onMarkDone={() => markDone(item.key)}
            onUndo={() => undo(item.key)}
          />
        ))}
      </div>
      {lowUse && (
        <p className="heating-advice-note" data-testid="heating-advice-seasonal">
          You&apos;re barely heating now — these pay off from autumn; a good time to do them.
        </p>
      )}
      {allDone && (
        <p className="heating-advice-alldone" data-testid="heating-advice-alldone">
          These were one-offs; revisit if your setup changes.
        </p>
      )}
    </div>
  );
}
