// An Oura-style circular score gauge (0-100, 100 = best) — the shared "score" motif across the home
// hero and the Insights tab, so a score looks the same wherever it appears. Colour comes from the
// band via CSS so light/dark + the sky backdrop stay in tune. Renders as a button when it links
// somewhere (tap → detail), or a decorative element when it's just showing a value in place.
import { useEffect, useState } from "react";

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
  // Sweep the arc up from empty on mount (the satisfying "fill" — CSS transitions the offset).
  // Start at the target when the user prefers reduced motion, so it never animates.
  const [shown, setShown] = useState(() =>
    typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches
      ? pct
      : 0,
  );
  useEffect(() => {
    const id = requestAnimationFrame(() => setShown(pct));
    return () => cancelAnimationFrame(id);
  }, [pct]);
  const offset = c * (1 - shown / 100);
  const band = scoreBand(value);
  const half = size / 2;
  // Fold the visible caption into the spoken name — the button's aria-label would otherwise hide it
  // from screen readers, so they'd lose the motivating line sighted users get.
  const spoken =
    ariaText ??
    `${label} score: ${value == null ? "not available" : `${Math.round(value)} out of 100`}` +
      (caption ? `. ${caption}` : "");
  const tid = testId ?? `ring-${label.toLowerCase().replace(/[^a-z]+/g, "-")}`;

  const graphic = (
    <>
      <span className="ring-graphic" style={{ width: size, height: size }}>
        <svg viewBox={`0 0 ${size} ${size}`} className="ring-svg" aria-hidden="true">
          <circle className="ring-track" cx={half} cy={half} r={r} fill="none" strokeWidth={stroke} />
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
    </>
  );

  // Non-interactive placements (e.g. inside an Insights card that already carries the label) render
  // a plain element, decorative to assistive tech so the value isn't announced twice.
  if (!onClick) {
    return (
      <span className={`ring ring-static ring-${band}`} data-testid={tid} title={hint ?? caption} aria-hidden="true">
        {graphic}
      </span>
    );
  }

  return (
    <button
      type="button"
      className={`ring ring-${band}`}
      onClick={onClick}
      aria-label={`${spoken} — open Insights`}
      title={hint ?? caption}
      data-testid={tid}
    >
      {graphic}
    </button>
  );
}
