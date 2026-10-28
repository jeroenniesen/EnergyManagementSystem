// Hash helpers for #manage/settings/<section> (#368). Pure: no DOM, no React.
// Section ids mirror Settings.tsx NAV_GROUPS — do not rename them.

export const SETTINGS_SECTION_IDS = [
  "connection",
  "meters",
  "battery",
  "prices",
  "consumers",
  "site",
  "strategy",
  "planner",
  "control",
  "ev",
  "ai",
  "reporting",
  "notify",
  "access",
  "ui",
] as const;

export type SettingsSectionId = (typeof SETTINGS_SECTION_IDS)[number];

const VIEWS = ["dashboard", "insights", "car", "chat", "trading", "setup", "manage"] as const;
const MANAGE_TABS = ["settings", "system", "audit"] as const;

export type AppView = (typeof VIEWS)[number];
export type AppManageTab = (typeof MANAGE_TABS)[number];

export type ParsedAppHash = {
  view: AppView;
  tab: AppManageTab;
  /** Raw id from #manage/settings/<id>, including ids that are not in SETTINGS_SECTION_IDS. */
  settingsSection?: string;
  /** False when settingsSection is present but not a known section id. */
  settingsSectionKnown?: boolean;
};

const KNOWN_SECTIONS: ReadonlySet<string> = new Set(SETTINGS_SECTION_IDS);

function stripHash(hash: string): string {
  return hash.replace(/^#\/?/, "");
}

function decodeSegment(segment: string): string {
  try {
    return decodeURIComponent(segment);
  } catch {
    return segment;
  }
}

/** Hash → view, manage tab, and optional settings section. Unknown views fall back to dashboard. */
export function parseAppHash(hash: string): ParsedAppHash {
  const raw = stripHash(hash);
  // Legacy top-level Settings/System/Audit → the Manage sub-tab they became.
  if (raw === "settings") return { view: "manage", tab: "settings" };
  if (raw === "system") return { view: "manage", tab: "system" };
  if (raw === "audit") return { view: "manage", tab: "audit" };
  if (raw === "setup") return { view: "setup", tab: "settings" };
  if (raw === "manage" || raw === "manage/settings") return { view: "manage", tab: "settings" };

  const section = raw.match(/^manage\/settings\/([^/]+)$/);
  if (section) {
    const id = decodeSegment(section[1]);
    return {
      view: "manage",
      tab: "settings",
      settingsSection: id,
      settingsSectionKnown: KNOWN_SECTIONS.has(id),
    };
  }

  const manage = raw.match(/^manage\/(.+)$/);
  if (manage) {
    const tab = manage[1];
    return {
      view: "manage",
      tab: (MANAGE_TABS as readonly string[]).includes(tab) ? (tab as AppManageTab) : "settings",
    };
  }

  return {
    view: (VIEWS as readonly string[]).includes(raw) ? (raw as AppView) : "dashboard",
    tab: "settings",
  };
}

/**
 * Canonical hash without the leading '#'.
 * A settings section is `#manage/settings/<id>`. Bare Manage stays `#manage` so existing
 * bookmarks keep working. System and audit ignore a section argument.
 */
export function serialiseAppHash(
  view: AppView,
  tab: AppManageTab = "settings",
  section?: string,
): string {
  if (view === "manage") {
    if (tab === "system" || tab === "audit") return `manage/${tab}`;
    if (section) return `manage/settings/${encodeURIComponent(section)}`;
    return "manage";
  }
  return view;
}

/**
 * Known id passes through. An unknown id becomes the first known section (no error).
 * No id (bare #manage) stays undefined so the caller can show the section list.
 */
export function resolveSettingsSection(
  section: string | undefined,
  knownIds: readonly string[],
): string | undefined {
  if (!section) return undefined;
  if (knownIds.includes(section)) return section;
  return knownIds[0];
}
