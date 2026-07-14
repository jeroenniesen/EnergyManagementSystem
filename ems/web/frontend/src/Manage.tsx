// Manage view (feat/ux-batch-3): folds the three operator surfaces — Settings, System, Audit —
// behind one top-level nav item with a slim segmented sub-nav. The user's words: they're "often
// used together and eat menu space." The EXISTING components (Settings / SystemView / AuditView)
// are mounted UNCHANGED — this is purely a container + sub-router; it never reaches into their
// internals. The active sub-tab + hash routing (#manage, #manage/system, #manage/audit) live in
// App.tsx; this component just renders the tab it's told to.
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
}: {
  tab: ManageTab;
  onTab: (t: ManageTab) => void;
  onSettingsSaved?: (values: Record<string, number | boolean | string>) => void;
}) {
  return (
    <section data-testid="manage">
      <nav className="manage-subnav" role="tablist" aria-label="Manage sections">
        {TABS.map((t) => (
          <button
            key={t.key}
            type="button"
            role="tab"
            className={`manage-tab${tab === t.key ? " active" : ""}`}
            aria-selected={tab === t.key}
            data-testid={t.testid}
            onClick={() => onTab(t.key)}
          >
            {t.label}
          </button>
        ))}
      </nav>
      {/* One surface at a time; each is the exact same component the old top-level views mounted. */}
      {tab === "settings" && <Settings onSaved={onSettingsSaved} />}
      {tab === "system" && <SystemView />}
      {tab === "audit" && <AuditView />}
    </section>
  );
}
