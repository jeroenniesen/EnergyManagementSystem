import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, test } from "vitest";

import { MustChargePanel } from "./Override";
import { MUST_CHARGE_OVERRIDE } from "./labels";

const idle = {
  active: false,
  target_soc: null,
  deadline_hhmm: null,
  unreachable_reason: null,
  warning: null,
  watch_only: true,
  execution: MUST_CHARGE_OVERRIDE.watchOnly,
};

describe("MustChargePanel", () => {
  test("shows the form, Annuleer is absent until an override is active", () => {
    const html = renderToStaticMarkup(
      <MustChargePanel
        state={idle}
        canOperate
        target="80"
        hour="07:00"
        busy={false}
        error={null}
        onTarget={() => undefined}
        onHour={() => undefined}
        onSubmit={() => undefined}
        onCancel={() => undefined}
      />,
    );
    expect(html).toContain(MUST_CHARGE_OVERRIDE.title);
    expect(html).toContain(MUST_CHARGE_OVERRIDE.watchOnly);
    expect(html).toContain("must-charge-target");
    expect(html).toContain("must-charge-hour");
    expect(html).toContain(MUST_CHARGE_OVERRIDE.submit);
    expect(html).not.toContain("must-charge-cancel");
  });

  test("active override shows the target, the warning and Annuleer", () => {
    const html = renderToStaticMarkup(
      <MustChargePanel
        state={{
          ...idle,
          active: true,
          target_soc: 80,
          deadline_hhmm: "07:00",
          unreachable_reason: "Dagplafond 1 kWh bereikt",
        }}
        canOperate
        target="80"
        hour="07:00"
        busy={false}
        error={null}
        onTarget={() => undefined}
        onHour={() => undefined}
        onSubmit={() => undefined}
        onCancel={() => undefined}
      />,
    );
    expect(html).toContain("Vul tot 80% vóór 07:00");
    expect(html).toContain("Dagplafond 1 kWh bereikt");
    expect(html).toContain(MUST_CHARGE_OVERRIDE.cancel);
    expect(html).toContain("must-charge-watch-only");
    expect(html).toContain("alleen kijken — niet uitgevoerd");
  });
});
