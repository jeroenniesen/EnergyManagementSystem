import { describe, expect, test } from "vitest";

import { AUDIT_VIEW, SETUP_CHECKLIST, WEEK_DIGEST } from "./labels";

describe("load/error copy (#369)", () => {
  test("week digest exposes Dutch error + retry", () => {
    expect(WEEK_DIGEST.sectionTitle).toMatch(/week/i);
    expect(WEEK_DIGEST.loadError).toMatch(/kon niet/i);
    expect(WEEK_DIGEST.retry).toBe("Opnieuw");
  });

  test("setup checklist exposes loading + retry labels", () => {
    expect(SETUP_CHECKLIST.loading).toBe("Laden…");
    expect(SETUP_CHECKLIST.retry).toBe("Opnieuw proberen");
  });

  test("audit view exposes Dutch error + retry", () => {
    expect(AUDIT_VIEW.loadError).toMatch(/kon niet/i);
    expect(AUDIT_VIEW.retry).toBe("Opnieuw");
  });
});
