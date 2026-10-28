import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, test } from "vitest";

import { RESERVE_EXPLANATION, ReserveInput, reserveFieldFromSettings } from "./Setup";

describe("setup reserve", () => {
  test("takes bounds, step, unit and default from settings schema", () => {
    const schema = [{
      key: "battery.min_reserve_soc", default: 13, min: 7, max: 44,
      step: 2, unit: "%",
    }];
    const { field, value } = reserveFieldFromSettings({ schema, values: {} });
    const html = renderToStaticMarkup(
      <ReserveInput field={field} value={value} onChange={() => {}} />,
    );
    expect(html).toContain('min="7"');
    expect(html).toContain('max="44"');
    expect(html).toContain('step="2"');
    expect(html).toContain('value="13"');
    expect(html).toContain("%</span>");
    expect(reserveFieldFromSettings({ schema, values: { "battery.min_reserve_soc": 17 } }).value)
      .toBe("17");
  });

  test("uses ordinary language without jargon", () => {
    expect(RESERVE_EXPLANATION).toContain("achter voor 's nachts of bij storing");
    expect(RESERVE_EXPLANATION).not.toMatch(/soc/i);
  });
});
