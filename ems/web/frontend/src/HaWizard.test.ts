import { describe, expect, it } from "vitest";

import {
  probeErrorMessage,
  stepAfterSuccessfulTest,
  tokenCreateUrl,
  trimHaUrl,
  validateHaUrlClient,
} from "./haWizardHelpers";

describe("trimHaUrl", () => {
  it("strips whitespace and trailing slashes", () => {
    expect(trimHaUrl("  http://ha.local:8123/  ")).toBe("http://ha.local:8123");
  });
});

describe("validateHaUrlClient", () => {
  it("accepts LAN and Nabu Casa shapes", () => {
    const lan = validateHaUrlClient("http://homeassistant.local:8123/");
    expect(lan.ok).toBe(true);
    expect(lan.normalized).toBe("http://homeassistant.local:8123");
    expect(lan.httpLanWarning).toBe(false);

    const nabu = validateHaUrlClient("https://abcd.ui.nabu.casa");
    expect(nabu.ok).toBe(true);
    expect(nabu.httpLanWarning).toBe(false);
  });

  it("warns on http to a public host", () => {
    const check = validateHaUrlClient("http://example.com:8123");
    expect(check.ok).toBe(true);
    expect(check.httpLanWarning).toBe(true);
  });

  it("rejects path, credentials, and bad schemes", () => {
    expect(validateHaUrlClient("http://ha.local/api").ok).toBe(false);
    expect(validateHaUrlClient("http://user:pass@ha.local").ok).toBe(false);
    expect(validateHaUrlClient("ftp://ha.local").ok).toBe(false);
    expect(validateHaUrlClient("").ok).toBe(false);
  });
});

describe("tokenCreateUrl", () => {
  it("points at profile security", () => {
    expect(tokenCreateUrl("http://ha.local:8123")).toBe(
      "http://ha.local:8123/profile/security",
    );
  });
});

describe("probeErrorMessage", () => {
  it("prefers server message", () => {
    expect(
      probeErrorMessage({ ok: false, message: "Home Assistant rejected the token." }),
    ).toContain("rejected");
  });
});

describe("stepAfterSuccessfulTest", () => {
  it("moves to pick either way (manual add still available)", () => {
    expect(stepAfterSuccessfulTest(0)).toBe("pick");
    expect(stepAfterSuccessfulTest(2)).toBe("pick");
  });
});
