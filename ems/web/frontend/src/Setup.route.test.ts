import { describe, expect, test } from "vitest";

import { routeFromHash } from "./App";

describe("setup route (#290)", () => {
  test("#setup maps to the Inrichten view", () => {
    expect(routeFromHash("#setup")).toEqual({ view: "setup", tab: "settings" });
    expect(routeFromHash("setup")).toEqual({ view: "setup", tab: "settings" });
  });
});
