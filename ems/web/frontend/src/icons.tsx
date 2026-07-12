// One small inline-SVG icon set for the whole UI — replaces the earlier mix of emoji, Unicode
// glyphs and CSS dots with a single consistent visual language. Self-hosted (no CDN, per SPEC),
// 16px, stroke = currentColor so each icon inherits its surrounding text colour. Icons are
// decorative (the text label carries the meaning), so they are aria-hidden.

export type IconName =
  | "battery-level"
  | "home"
  | "grid"
  | "solar"
  | "bolt"
  | "bulb"
  | "sliders"
  | "euro"
  | "auto"
  | "winter"
  | "car"
  | "check"
  | "alert"
  | "bell";

const PATHS: Record<IconName, React.ReactNode> = {
  // Battery outline (how full it is) — body fills the canvas, terminal nub flush to its right.
  "battery-level": (
    <>
      <rect x="1" y="7" width="18" height="10" rx="2" />
      <line x1="23" y1="10" x2="23" y2="14" />
    </>
  ),
  // House (whole-home load).
  home: (
    <>
      <path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" />
      <path d="M9 22V12h6v10" />
    </>
  ),
  // Plug (the grid connection).
  grid: (
    <>
      <path d="M9 8V2" />
      <path d="M15 8V2" />
      <path d="M18 8v5a4 4 0 0 1-4 4h-4a4 4 0 0 1-4-4V8z" />
      <path d="M12 21v-4" />
    </>
  ),
  // Sun (solar).
  solar: (
    <>
      <circle cx="12" cy="12" r="4" />
      <path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M6.34 17.66l-1.41 1.41M19.07 4.93l-1.41 1.41" />
    </>
  ),
  // Lightning bolt (battery power flow).
  bolt: <path d="M13 2L3 14h9l-1 8 10-12h-9z" />,
  // Lightbulb (home use / consumption).
  bulb: (
    <>
      <path d="M15 14c.2-1 .7-1.7 1.5-2.5C17.7 10.6 18 9.3 18 8A6 6 0 0 0 6 8c0 1.3.3 2.6 1.5 3.5.8.8 1.3 1.5 1.5 2.5" />
      <path d="M9 18h6" />
      <path d="M10 22h4" />
    </>
  ),
  // Sliders (the battery's running mode).
  sliders: (
    <>
      <line x1="21" y1="6" x2="14" y2="6" />
      <line x1="10" y1="6" x2="3" y2="6" />
      <line x1="21" y1="12" x2="12" y2="12" />
      <line x1="8" y1="12" x2="3" y2="12" />
      <line x1="21" y1="18" x2="16" y2="18" />
      <line x1="12" y1="18" x2="3" y2="18" />
      <line x1="14" y1="4" x2="14" y2="8" />
      <line x1="8" y1="10" x2="8" y2="14" />
      <line x1="16" y1="16" x2="16" y2="20" />
    </>
  ),
  // Euro (money saved).
  euro: (
    <>
      <path d="M4 10h12" />
      <path d="M4 14h9" />
      <path d="M19 6a7.7 7.7 0 0 0-5.2-2A7.9 7.9 0 0 0 6 12a7.9 7.9 0 0 0 7.8 8 7.7 7.7 0 0 0 5.2-2" />
    </>
  ),
  // Circular arrows (Auto / follows the season).
  auto: (
    <>
      <path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8" />
      <path d="M21 3v5h-5" />
      <path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16" />
      <path d="M3 21v-5h5" />
    </>
  ),
  // Circle-check (a plan check that passed).
  check: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="m8.5 12 2.5 2.5 4.5-5" />
    </>
  ),
  // Alert triangle (a plan check that needs attention).
  alert: (
    <>
      <path d="M10.3 4.3 2.6 17.8a1.5 1.5 0 0 0 1.3 2.2h16.2a1.5 1.5 0 0 0 1.3-2.2L13.7 4.3a1.5 1.5 0 0 0-2.6 0Z" />
      <path d="M12 9v4" />
      <path d="M12 17h.01" />
    </>
  ),
  // Car (EV charging).
  car: (
    <>
      <path d="M3 13l2-5.2A2 2 0 0 1 6.9 6.5h10.2a2 2 0 0 1 1.9 1.3L21 13v3a1 1 0 0 1-1 1h-1" />
      <path d="M5 17H4a1 1 0 0 1-1-1v-3" />
      <path d="M3 13h18" />
      <circle cx="7.5" cy="17" r="1.8" />
      <circle cx="16.5" cy="17" r="1.8" />
      <path d="M9.3 17h5.4" />
    </>
  ),
  // Bell (notifications).
  bell: (
    <>
      <path d="M18 8a6 6 0 0 0-12 0c0 7-3 9-3 9h18s-3-2-3-9" />
      <path d="M13.73 21a2 2 0 0 1-3.46 0" />
    </>
  ),
  // Snowflake (winter strategy).
  winter: (
    <>
      <line x1="2" y1="12" x2="22" y2="12" />
      <line x1="12" y1="2" x2="12" y2="22" />
      <path d="m20 16-4-4 4-4" />
      <path d="m4 8 4 4-4 4" />
      <path d="m16 4-4 4-4-4" />
      <path d="m8 20 4-4 4 4" />
    </>
  ),
};

export function Icon({ name, className }: { name: IconName; className?: string }) {
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
      {PATHS[name]}
    </svg>
  );
}
