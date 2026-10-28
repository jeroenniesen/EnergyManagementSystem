import { describe, expect, test } from "vitest";

import {
  controlArmConsequenceText,
  controlSectionHint,
  draftNeedsControlConfirm,
  modeBlockRecovery,
  restartPendingStatusLabel,
  savedApplyStatusText,
} from "./controlArming";

const savedSafe = {
  "control.dry_run": true,
  "control.operational": false,
};

describe("draftNeedsControlConfirm (#364)", () => {
  test("turning Watch only off needs the consequence and confirmation", () => {
    expect(
      draftNeedsControlConfirm(savedSafe, { ...savedSafe, "control.dry_run": false }),
    ).toBe(true);
  });

  test("turning Let the system control on needs the consequence and confirmation", () => {
    expect(
      draftNeedsControlConfirm(savedSafe, { ...savedSafe, "control.operational": true }),
    ).toBe(true);
  });

  test("either dangerous change is enough when both move", () => {
    expect(
      draftNeedsControlConfirm(savedSafe, {
        "control.dry_run": false,
        "control.operational": true,
      }),
    ).toBe(true);
  });

  test("a dangerous change still confirms when another switch gets safer", () => {
    expect(
      draftNeedsControlConfirm(
        { "control.dry_run": true, "control.operational": true },
        { "control.dry_run": false, "control.operational": false },
      ),
    ).toBe(true);
  });

  test("the safer direction does not ask for confirmation", () => {
    expect(
      draftNeedsControlConfirm(
        { "control.dry_run": false, "control.operational": true },
        { "control.dry_run": true, "control.operational": false },
      ),
    ).toBe(false);
  });

  test("an unchanged draft does not ask for confirmation", () => {
    expect(draftNeedsControlConfirm(savedSafe, { ...savedSafe })).toBe(false);
  });

  test("missing or non-boolean values do not arm", () => {
    expect(draftNeedsControlConfirm({}, { "control.dry_run": false })).toBe(false);
    expect(draftNeedsControlConfirm({ "control.operational": false }, {})).toBe(false);
    expect(
      draftNeedsControlConfirm(
        { "control.dry_run": "true", "control.operational": 0 },
        { "control.dry_run": false, "control.operational": true },
      ),
    ).toBe(false);
  });
});

describe("restartPendingStatusLabel (#364)", () => {
  test("pending status reads Wacht op herstart and never the token", () => {
    const label = restartPendingStatusLabel(true);
    expect(label).toBe("Wacht op herstart");
    expect(label).not.toContain("restart_pending");
  });

  test("a field that is not pending keeps the needs-restart badge", () => {
    expect(restartPendingStatusLabel(false)).toBe("needs restart");
  });

  test("the save confirmation uses the same pending label", () => {
    const pending = savedApplyStatusText(true);
    expect(pending).toContain("Wacht op herstart");
    expect(pending).not.toContain("restart_pending");
    expect(savedApplyStatusText(false)).toBe("Saved — active now");
  });
});

describe("control arm copy (#364)", () => {
  test("the consequence says EMS may control, only after restart, and a charge window can abort", () => {
    const text = controlArmConsequenceText();
    expect(text).toMatch(/batterij/);
    expect(text).toMatch(/aansturen/);
    expect(text).toMatch(/pas actief na een herstart/);
    expect(text).toMatch(/laadvenster/);
    expect(text).not.toMatch(/restart_pending/);
  });

  test("the visible control hint has no dry_run or mock jargon", () => {
    const hint = controlSectionHint().toLowerCase();
    expect(hint).not.toContain("dry_run");
    expect(hint).not.toContain("mock");
    expect(hint.length).toBeGreaterThan(0);
  });
});

describe("modeBlockRecovery (#364)", () => {
  test("the main line is one Dutch sentence; config tokens sit behind Meer", () => {
    const { sentence, more } = modeBlockRecovery();
    expect(sentence).toMatch(/EMS stuurt/);
    expect(sentence).toMatch(/herstart/);
    expect(sentence).toMatch(/Alleen kijken/);
    expect(sentence).toMatch(/Let the system control the battery/);
    const sentences = sentence.split(/[.!?]/).map((part) => part.trim()).filter(Boolean);
    expect(sentences).toHaveLength(1);
    for (const token of ["config.yaml", "dev.mode", "control.dry_run"]) {
      expect(sentence).not.toContain(token);
      expect(more).toContain(token);
    }
  });
});
