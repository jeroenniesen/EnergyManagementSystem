// Advice-only heating recommendations (BACKLOG B-11 / roadmap phase F7 — the biggest absolute
// CO2/€ prize left once solar + battery are handled, and the EMS has zero control over the boiler).
// House style: evidence -> suggestion -> "you decide" (see the solar-confidence advisor). Framed
// entirely by the household's own metered gas — never a generic tip list, and never nagging: it
// only renders when /api/report's `gas` is non-null (a gas meter is actually configured).
// Renders directly under GasPanel in Insights.tsx.
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

function AdviceCard({
  testId,
  title,
  evidence,
  body,
  saving,
  details,
  safety,
}: {
  testId: string;
  title: string;
  evidence: string;
  body: string;
  saving: string;
  details: string;
  safety?: string;
}) {
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

  return (
    <div className="heating-advice" data-testid="heating-advice">
      <h3 className="card-title flow-title heating-advice-title">
        <span className="gas-dot" aria-hidden="true" />
        Heating — the biggest lever left
      </h3>
      <div className="advice-cards">
        <AdviceCard
          testId="advice-balancing"
          title="Balance your radiators (waterzijdig inregelen)"
          evidence={evidence}
          body={`Radiators heating unevenly? Balancing typically saves 10–15% of gas (~€${balancingEur}/yr for your usage — rough estimate from your meter).`}
          saving="Typical saving: 10–15% of gas, as a one-off (redo only if radiators change)."
          details="A heating engineer, or a DIY balancing kit with a clip-on thermometer, adjusts each radiator's lockshield valve so hot water reaches every radiator evenly — instead of the nearest ones taking most of the flow and starving the far ones."
        />
        <AdviceCard
          testId="advice-flow-temp"
          title={'Lower the boiler flow temperature ("zet \'m op 60")'}
          evidence={evidence}
          body="Condensing boilers only condense — and save gas — when the water returning to them is cool enough. Lowering the central-heating (CV) flow temperature to 60°C leaves comfort unchanged in most homes."
          saving="Typical saving: 5–10% of gas."
          details="Boiler menu → CV/flow temperature. Turn it down in steps of about 5°C over a few days and check the coldest rooms still reach temperature on the coldest days; only turn it back up if one struggles."
        />
        <AdviceCard
          testId="advice-dhw-eco"
          title="Hot water: eco mode, safely"
          evidence={evidence}
          body="Switch the boiler's hot-water (DHW) mode to eco or a schedule so it stops reheating a tank nobody's drawing from."
          saving="Typical saving: 2–5% of gas from fewer reheat cycles — smaller than the other two, but free."
          safety="Keep stored tap water at 60°C or higher, always — that's the margin that prevents Legionella. Eco/schedule mode changes WHEN the boiler reheats, never how hot the stored water gets; never set it below 60°C."
          details="Boiler menu → hot-water / DHW schedule. Match reheating to when you actually use hot water (e.g. off overnight) — leave the storage target itself at 60°C or above."
        />
      </div>
      {lowUse && (
        <p className="heating-advice-note" data-testid="heating-advice-seasonal">
          You&apos;re barely heating now — these pay off from autumn; a good time to do them.
        </p>
      )}
    </div>
  );
}
