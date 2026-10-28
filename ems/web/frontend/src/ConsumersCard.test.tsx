import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, test } from "vitest";

import { ConsumersCardView, type ConsumersCardModel } from "./ConsumersCard";

const model = (accepted: boolean, overrides: Record<string, boolean> = {}): ConsumersCardModel => ({
  data: {
    title: "Appliances",
    summary: "1 selected appliance.",
    read_only: true,
    devices: [
      {
        id: "mock-washer-1",
        name: "Mock washer",
        kind: "washer",
        integration: "mock",
        opted_in: true,
        status: {
          state: "idle",
          state_label: "Idle — ready when you are",
          power_w: 0,
          energy_kwh: null,
          can_start: true,
          observed_at: null,
          detail: null,
        },
      },
    ],
  },
  advice: {
    title: "Start window advice",
    enabled: true,
    dry_run: true,
    live_armed: false,
    summary: "1 start window from day-ahead prices.",
    footnote: "Advice only — nothing changes automatically.",
    advice: [
      {
        device_id: "mock-washer-1",
        device_name: "Mock washer",
        kind: "washer",
        eligible: true,
        accepted,
        window: {
          start: "2026-07-11T10:00:00+00:00",
          end: "2026-07-11T11:00:00+00:00",
          slots: 4,
          avg_eur_per_kwh: 0.06,
          assumed_kwh: 1,
          reason: "Best 1h window, assuming 1 kWh.",
        },
        skip_reason: null,
      },
    ],
  },
  overrides,
  actionError: null,
  busyId: null,
  onAccept: () => {},
  onCancel: () => {},
});

describe("ConsumersCard start window", () => {
  test("offers Ik start hier before the window is accepted", () => {
    const html = renderToStaticMarkup(<ConsumersCardView {...model(false)} />);
    expect(html).toContain("Ik start hier");
    expect(html).not.toContain("Annuleer");
    expect(html).toContain("consumer-mock-washer-1-accept-window");
  });

  test("offers Annuleer once the server says the window is accepted", () => {
    const html = renderToStaticMarkup(<ConsumersCardView {...model(true)} />);
    expect(html).toContain("Annuleer");
    expect(html).not.toContain("Ik start hier");
    expect(html).toContain("consumer-mock-washer-1-cancel-window");
  });

  test("a local cancel override shows Ik start hier again", () => {
    const html = renderToStaticMarkup(
      <ConsumersCardView {...model(true, { "mock-washer-1": false })} />,
    );
    expect(html).toContain("Ik start hier");
    expect(html).not.toContain("Annuleer");
  });
});
