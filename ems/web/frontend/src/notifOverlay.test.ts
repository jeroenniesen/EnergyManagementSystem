import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, test } from "vitest";

// Cheap CSS contract for the notifications overlay stacking bug: the topbar must form a
// stacking context above later dashboard siblings (the frosted .hero), otherwise the panel's
// own z-index only competes inside the isolated topbar and paints under the hero.
const css = readFileSync(
  join(dirname(fileURLToPath(import.meta.url)), "styles.css"),
  "utf8",
);

function block(selector: string): string {
  const re = new RegExp(
    `${selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\s*\\{([^}]*)\\}`,
    "m",
  );
  const m = css.match(re);
  if (!m) throw new Error(`CSS block not found for ${selector}`);
  return m[1];
}

describe("notifications overlay stacking CSS", () => {
  test("topbar isolation + z-index lifts the panel above the hero", () => {
    const topbar = block(".topbar");
    expect(topbar).toMatch(/isolation:\s*isolate/);
    expect(topbar).toMatch(/z-index:\s*20/);
  });

  test("notif panel stays opaque and scrolls within the viewport", () => {
    const panel = block(".notif-panel");
    expect(panel).toMatch(/z-index:\s*60/);
    expect(panel).toMatch(/background:\s*var\(--panel\)/);
    expect(panel).toMatch(/max-height:\s*min\(/);
    expect(panel).toMatch(/overflow:\s*hidden/);

    const list = block(".notif-list");
    expect(list).toMatch(/overflow-y:\s*auto/);
    expect(list).toMatch(/min-height:\s*0/);
  });
});
