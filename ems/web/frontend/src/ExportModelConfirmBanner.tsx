import { useState } from "react";

import { apiFetch } from "./auth";
import { EXPORT_MODEL_BANNER, exportModelMismatchWarning } from "./labels";

export type ExportModelBannerValues = {
  exportPriceModel: string;
  exportModelConfirmed: boolean;
};

type Props = {
  values: ExportModelBannerValues | null;
  onSaved: (patch: Partial<ExportModelBannerValues>) => void;
};

export function ExportModelConfirmBanner({ values, onSaved }: Props) {
  const [pending, setPending] = useState<"net_metering" | "spot_minus_tax" | null>(null);
  const [error, setError] = useState<string | null>(null);

  if (!values || values.exportModelConfirmed) {
    return null;
  }

  const mismatch = exportModelMismatchWarning(values.exportPriceModel);
  const busy = pending !== null;

  async function choose(model: "net_metering" | "spot_minus_tax") {
    setPending(model);
    setError(null);
    try {
      const r = await apiFetch("/api/settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          "prices.export_price_model": model,
          "prices.export_model_confirmed": true,
        }),
      });
      const b = await r.json().catch(() => ({}));
      if (!r.ok) {
        setError(typeof b.detail === "string" ? b.detail : "Opslaan mislukt");
        return;
      }
      onSaved({
        exportPriceModel: String(b.values?.["prices.export_price_model"] ?? model),
        exportModelConfirmed: b.values?.["prices.export_model_confirmed"] === true,
      });
    } catch {
      setError("Opslaan mislukt");
    } finally {
      setPending(null);
    }
  }

  return (
    <section
      className="settings-access-bar export-model-banner"
      data-testid="export-model-banner"
      aria-labelledby="export-model-banner-title"
      aria-busy={busy}
    >
      <h2 className="settings-group-title" id="export-model-banner-title">
        {EXPORT_MODEL_BANNER.title}
      </h2>
      <p className="settings-group-hint">{EXPORT_MODEL_BANNER.body}</p>
      {mismatch && (
        <p className="export-model-mismatch" data-testid="export-model-mismatch">
          {mismatch}
        </p>
      )}
      <div className="export-model-actions">
        <button
          type="button"
          className="btn-primary"
          data-testid="export-model-saldering"
          disabled={busy}
          aria-busy={pending === "net_metering"}
          onClick={() => choose("net_metering")}
        >
          {pending === "net_metering" ? EXPORT_MODEL_BANNER.saving : EXPORT_MODEL_BANNER.saldering}
        </button>
        <button
          type="button"
          className="btn-secondary"
          data-testid="export-model-from2027"
          disabled={busy}
          aria-busy={pending === "spot_minus_tax"}
          onClick={() => choose("spot_minus_tax")}
        >
          {pending === "spot_minus_tax" ? EXPORT_MODEL_BANNER.saving : EXPORT_MODEL_BANNER.from2027}
        </button>
      </div>
      {error && (
        <p className="field-error" data-testid="export-model-error" role="alert">
          {error}
        </p>
      )}
    </section>
  );
}

export function exportModelFromSettingsValues(
  values: Record<string, unknown> | null | undefined,
): ExportModelBannerValues | null {
  if (!values || !Object.prototype.hasOwnProperty.call(values, "prices.export_model_confirmed")) {
    return null;
  }
  return {
    exportPriceModel: String(values["prices.export_price_model"] ?? "spot_minus_tax"),
    exportModelConfirmed: values["prices.export_model_confirmed"] === true,
  };
}
