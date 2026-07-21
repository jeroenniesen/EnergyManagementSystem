// Manage view (feat/ux-batch-3): folds the three operator surfaces — Settings, System, Audit —
// behind one top-level nav item with a slim segmented sub-nav. The user's words: they're "often
// used together and eat menu space." Settings/AuditView are mounted UNCHANGED. SystemView gets one
// optional prop (`onNavigate`, production feedback: its action lines needed a real destination) —
// this is purely a container + sub-router otherwise; it never reaches into their internals. The
// active sub-tab + hash routing (#manage, #manage/system, #manage/audit) live in App.tsx; this
// component just renders the tab it's told to.
import { useRef } from "react";

import { AuditView } from "./AuditView";
import { Settings } from "./Settings";
import { SystemView } from "./System";

export type ManageTab = "settings" | "system" | "audit";

const TABS: { key: ManageTab; label: string; testid: string }[] = [
  { key: "settings", label: "Settings", testid: "manage-tab-settings" },
  { key: "system", label: "System", testid: "manage-tab-system" },
  { key: "audit", label: "Audit", testid: "manage-tab-audit" },
];

export function Manage({
  tab,
  onTab,
  onSettingsSaved,
  settingsSection,
  canOperate = true,
  isAdmin = false,
  identityAuth = false,
}: {
  tab: ManageTab;
  // `section` is an optional second argument so this same callback can also carry a Settings
  // deep-link (System's health actions) without a second prop — plain tab clicks below just omit
  // it, which resets any pending section (see App.tsx's navigate()).
  onTab: (t: ManageTab, section?: string) => void;
  onSettingsSaved?: (values: Record<string, number | boolean | string>) => void;
  // Which Settings section to open on mount — threaded straight to Settings' `initialSection`.
  settingsSection?: string;
  // Reader read-only mode + admin panel gating (auth slice 2 web) — threaded straight to Settings,
  // the only sub-tab with mutating controls or an admin-only surface (System/Audit are read-only).
  canOperate?: boolean;
  isAdmin?: boolean;
  // Identity auth active → hide the deprecated legacy shared-token knobs in Settings (design §8).
  identityAuth?: boolean;
}) {
  const refs = useRef<(HTMLButtonElement | null)[]>([]);
  const idx = Math.max(0, TABS.findIndex((t) => t.key === tab));

  // Arrow keys move through the sub-nav like a native tablist (mirrors StrategyCard's segmented
  // control onKeyDown — roving tabindex, wraps around).
  function onKeyDown(e: React.KeyboardEvent) {
    const fwd = e.key === "ArrowRight" || e.key === "ArrowDown";
    const back = e.key === "ArrowLeft" || e.key === "ArrowUp";
    if (!fwd && !back) return;
    e.preventDefault();
    const next = (idx + (fwd ? 1 : -1) + TABS.length) % TABS.length;
    onTab(TABS[next].key);
    refs.current[next]?.focus();
  }

  return (
    <section data-testid="manage">
      <nav
        className="manage-subnav"
        role="tablist"
        aria-label="Manage sections"
        onKeyDown={onKeyDown}
      >
        {TABS.map((t, i) => (
          <button
            key={t.key}
            ref={(el) => {
              refs.current[i] = el;
            }}
            type="button"
            role="tab"
            id={`manage-tab-${t.key}`}
            aria-controls={`manage-panel-${t.key}`}
            className={`manage-tab${tab === t.key ? " active" : ""}`}
            aria-selected={tab === t.key}
            tabIndex={tab === t.key ? 0 : -1}
            data-testid={t.testid}
            onClick={() => onTab(t.key)}
          >
            {t.label}
          </button>
        ))}
      </nav>
      {/* One surface at a time; each is the exact same component the old top-level views mounted.
          SystemView's model-health action lines (B-37/production feedback) need to jump to another
          Manage sub-tab — `onTab` is already threaded in from App for the sub-nav above, so it's
          reused as-is (no App.tsx change needed) rather than adding a second navigation prop. */}
      <div
        className="manage-panel"
        role="tabpanel"
        id={`manage-panel-${tab}`}
        aria-labelledby={`manage-tab-${tab}`}
        data-testid="manage-panel"
      >
        {tab === "settings" && (
          <Settings
            onSaved={onSettingsSaved}
            initialSection={settingsSection}
            canOperate={canOperate}
            isAdmin={isAdmin}
            identityAuth={identityAuth}
          />
        )}
        {tab === "system" && <SystemView onNavigate={onTab} />}
        {tab === "audit" && <AuditView />}
      </div>
    </section>
  );
}
