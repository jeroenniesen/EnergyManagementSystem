// B-102: nested Strategy / Manual / Car disclosures inside "Meer van je huis".
// Keeps the More body scannable — one section at a time, not a control dump.
import { type ReactNode } from "react";

export type HomeMoreNestKind = "strategy" | "manual" | "car";

export function HomeMoreNest({
  kind,
  label,
  children,
}: {
  kind: HomeMoreNestKind;
  label: string;
  children: ReactNode;
}) {
  return (
    <details
      className="home-more-nest"
      data-testid={`home-more-${kind}`}
      data-home-more-nest={kind}
    >
      <summary
        className="home-more-nest-toggle"
        data-testid={`home-more-${kind}-toggle`}
        aria-label={label}
      >
        {label}
      </summary>
      <div
        className="home-more-nest-body"
        data-testid={`home-more-${kind}-body`}
        role="group"
        aria-label={label}
      >
        {children}
      </div>
    </details>
  );
}
