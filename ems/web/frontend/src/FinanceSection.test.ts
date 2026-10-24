import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, test } from "vitest";

import { ProvisionalMark } from "./FinanceSection";

describe("F-48 voorlopig badge", () => {
  test("shows the Dutch badge when a day is provisional", () => {
    const html = renderToStaticMarkup(createElement(ProvisionalMark, {
      days: [
        { day: "2026-06-28", provisional: true },
        { day: "2026-06-27", provisional: false },
      ],
    }));
    expect(html).toContain('data-testid="fin-provisional-badge"');
    expect(html).toContain("voorlopig");
    expect(html).toContain("badge-provisional");
    expect(html).toContain("2026-06-28");
    expect(html).not.toContain("2026-06-27");
    expect(html).toContain("Nog geen rollup");
  });

  test("stays hidden when no day is provisional", () => {
    const html = renderToStaticMarkup(createElement(ProvisionalMark, {
      days: [{ day: "2026-06-28", provisional: false }],
    }));
    expect(html).toBe("");
  });
});
