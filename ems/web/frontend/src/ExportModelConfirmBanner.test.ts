import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, test } from "vitest";

import {
  ExportModelConfirmBanner,
  exportModelFromSettingsValues,
} from "./ExportModelConfirmBanner";
import { exportModelMismatchWarning } from "./labels";

describe("export model confirmation banner (#341)", () => {
  test("unloaded settings do not look unconfirmed", () => {
    expect(exportModelFromSettingsValues(null)).toBeNull();
    expect(exportModelFromSettingsValues({})).toBeNull();
    expect(exportModelFromSettingsValues({ "prices.export_price_model": "net_metering" })).toBeNull();
  });

  test("only an explicit true hides the banner", () => {
    expect(exportModelFromSettingsValues({
      "prices.export_price_model": "spot_minus_tax",
      "prices.export_model_confirmed": false,
    })).toEqual({
      exportPriceModel: "spot_minus_tax",
      exportModelConfirmed: false,
    });
    expect(exportModelFromSettingsValues({
      "prices.export_model_confirmed": true,
    })?.exportModelConfirmed).toBe(true);
  });

  test("mismatch copy names the stored model and invents no amounts", () => {
    for (const model of ["spot_minus_tax", "net_metering", "fixed"]) {
      const text = exportModelMismatchWarning(model) ?? "";
      expect(text).not.toMatch(/€|\d+[,.]\d+/);
    }
    expect(exportModelMismatchWarning("spot_minus_tax")).toContain("huidige model");
    expect(exportModelMismatchWarning("net_metering")).toContain("saldering");
    expect(exportModelMismatchWarning("fixed")).toBeNull();
  });

  test("banner exposes both choices and stays out of a confirmed render", () => {
    const open = renderToStaticMarkup(createElement(ExportModelConfirmBanner, {
      values: { exportPriceModel: "spot_minus_tax", exportModelConfirmed: false },
      onSaved: () => {},
    }));
    expect(open).toContain("export-model-banner");
    expect(open).toContain("export-model-saldering");
    expect(open).toContain("export-model-from2027");
    expect(open).toContain("Kies je exportmodel (saldering of vanaf 2027)");
    expect(open).not.toContain("€");
    const closed = renderToStaticMarkup(createElement(ExportModelConfirmBanner, {
      values: { exportPriceModel: "net_metering", exportModelConfirmed: true },
      onSaved: () => {},
    }));
    expect(closed).toBe("");
  });
});
