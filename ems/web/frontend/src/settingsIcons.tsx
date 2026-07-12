// Small hand-drawn 16px glyphs for the Settings menu — one per section group. Self-hosted inline
// SVG (no icon lib, per the bundle budget), stroke = currentColor so each inherits the menu item's
// colour (muted → accent when active). Purely decorative (the text label carries the meaning), so
// aria-hidden. Kept here rather than in the shared icons.tsx so the settings shell owns its own set.
import type { ReactNode } from "react";

const SECTION_PATHS: Record<string, ReactNode> = {
  // Connection — two chain links (device/service wiring).
  connection: (
    <>
      <path d="M9 12h6" />
      <path d="M8.5 8H7a4 4 0 0 0 0 8h1.5" />
      <path d="M15.5 8H17a4 4 0 0 1 0 8h-1.5" />
    </>
  ),
  // Meters — a gauge dial with a needle.
  meters: (
    <>
      <path d="M4 18a8 8 0 1 1 16 0" />
      <path d="M12 14a1.6 1.6 0 1 0 0-3.2 1.6 1.6 0 0 0 0 3.2z" />
      <path d="m12.9 11.4 2.6-2.9" />
    </>
  ),
  // Battery — outline + terminal (matches the shared battery glyph).
  battery: (
    <>
      <rect x="3" y="8" width="15" height="9" rx="2" />
      <line x1="21" y1="11" x2="21" y2="14" />
    </>
  ),
  // Prices — euro sign with the two bars.
  prices: (
    <>
      <path d="M16.5 7a5 5 0 1 0 0 10" />
      <path d="M5 10.5h8" />
      <path d="M5 14h6" />
    </>
  ),
  // Site — map pin (location + array).
  site: (
    <>
      <path d="M12 21s7-5.5 7-11a7 7 0 1 0-14 0c0 5.5 7 11 7 11z" />
      <circle cx="12" cy="10" r="2.5" />
    </>
  ),
  // Strategy — compass needle.
  strategy: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="m15.5 8.5-2 5.2-5.2 2 2-5.2z" />
    </>
  ),
  // Planner — calendar.
  planner: (
    <>
      <rect x="4" y="5" width="16" height="16" rx="2" />
      <path d="M4 9.5h16" />
      <path d="M8.5 3v4" />
      <path d="M15.5 3v4" />
    </>
  ),
  // Control — shield (safety limits).
  control: <path d="M12 3.2 5 6v5c0 4.5 3 7.4 7 8.8 4-1.4 7-4.3 7-8.8V6z" />,
  // Car (ev) — simple car body with wheels.
  ev: (
    <>
      <path d="M5 13.5 6.4 9.4A2 2 0 0 1 8.3 8h7.4a2 2 0 0 1 1.9 1.4l1.4 4.1V17h-2" />
      <path d="M7 17H5v-3.5" />
      <path d="M5 13.5h14" />
      <circle cx="8" cy="17" r="1.6" />
      <circle cx="16" cy="17" r="1.6" />
    </>
  ),
  // AI — a spark/sparkle.
  ai: (
    <>
      <path d="M12 3.5 13.7 8 18 9.5 13.7 11 12 15.5 10.3 11 6 9.5 10.3 8z" />
      <path d="M18 15.5 18.8 17.6 21 18.4 18.8 19.2 18 21.5 17.2 19.2 15 18.4 17.2 17.6z" />
    </>
  ),
  // Reporting — bar chart.
  reporting: (
    <>
      <path d="M4 20h16" />
      <path d="M7 20v-6" />
      <path d="M12 20V8" />
      <path d="M17 20v-9" />
    </>
  ),
  // Access — padlock.
  access: (
    <>
      <rect x="5" y="11" width="14" height="9" rx="2" />
      <path d="M8 11V8a4 4 0 0 1 8 0v3" />
    </>
  ),
  // Appearance (ui) — an eye (how it looks).
  ui: (
    <>
      <path d="M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12z" />
      <circle cx="12" cy="12" r="3" />
    </>
  ),
  // Fallback for any future/unknown group — sliders.
  _fallback: (
    <>
      <line x1="4" y1="8" x2="20" y2="8" />
      <line x1="4" y1="16" x2="20" y2="16" />
      <circle cx="9" cy="8" r="2" />
      <circle cx="15" cy="16" r="2" />
    </>
  ),
};

export function SectionIcon({ group, className }: { group: string; className?: string }) {
  return (
    <svg
      className={className}
      width="16"
      height="16"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      {SECTION_PATHS[group] ?? SECTION_PATHS._fallback}
    </svg>
  );
}
