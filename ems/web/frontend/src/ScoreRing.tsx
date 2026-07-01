// An Oura-style circular score gauge (0-100, 100 = best). Presentational + reused by the home hero
// and (later) the Insights tab. Colour comes from the score band via CSS so light/dark + the sky
// backdrop all stay in tune. A button so it's tappable through to the detail view.

export type Band = "good" | "ok" | "low" | "na";

export function scoreBand(v: number | null | undefined): Band {
  if (v == null) return "na";
  if (v >= 80) return "good";
  if (v >= 50) return "ok";
  return "low";
}

export function ScoreRing({
  value,
  label,
  caption,
  hint,
  ariaText,
  onClick,
  size = 132,
  testId,
}: {
  value: number | null;
  label: string;
  caption?: string; // short line rendered under the ring (optional)
  hint?: string; // hover tooltip (usually the full self-explanation)
  ariaText?: string;
  onClick?: () => void;
  size?: number;
  testId?: string;
}) {
  const stroke = 11;
  const r = (size - stroke) / 2;
  const c = 2 * Math.PI * r;
  const pct = value == null ? 0 : Math.max(0, Math.min(100, value));
  const offset = c * (1 - pct / 100);
  const band = scoreBand(value);
  const half = size / 2;
  // Fold the visible caption into the spoken name — the button's aria-label would otherwise hide it
  // from screen readers, so they'd lose the motivating line sighted users get.
  const spoken =
    ariaText ??
    `${label} score: ${value == null ? "not available" : `${Math.round(value)} out of 100`}` +
      (caption ? `. ${caption}` : "");

  return (
    <button
      type="button"
      className={`ring ring-${band}`}
      onClick={onClick}
      aria-label={onClick ? `${spoken} — open Insights` : spoken}
      title={hint ?? caption}
      data-testid={testId ?? `ring-${label.toLowerCase().replace(/[^a-z]+/g, "-")}`}
    >
      <span className="ring-graphic" style={{ width: size, height: size }}>
        <svg viewBox={`0 0 ${size} ${size}`} className="ring-svg" aria-hidden="true">
          <circle
            className="ring-track"
            cx={half} cy={half} r={r} fill="none" strokeWidth={stroke}
          />
          <circle
            className="ring-arc"
            cx={half} cy={half} r={r} fill="none" strokeWidth={stroke}
            strokeLinecap="round"
            strokeDasharray={c}
            strokeDashoffset={offset}
            transform={`rotate(-90 ${half} ${half})`}
          />
        </svg>
        <span className="ring-center" aria-hidden="true">
          <span className="ring-value">{value == null ? "—" : Math.round(value)}</span>
          <span className="ring-name">{label}</span>
        </span>
      </span>
      {caption && <span className="ring-caption">{caption}</span>}
    </button>
  );
}
