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
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!values || values.exportModelConfirmed) {
    return null;
  }

  const mismatch = exportModelMismatchWarning(values.exportPriceModel);

  async function choose(model: "net_metering" | "spot_minus_tax") {
    setBusy(true);
    setError(null);
    try {
      const r = await apiFetch("/api/settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ "prices.export_price_model": model }),
      });
      const b = await r.json().catch(() => ({}));
      if (!r.ok) {
        setError(typeof b.detail === "string" ? b.detail : "Opslaan mislukt");
        return;
      }
      onSaved({
        exportPriceModel: String(b.values?.["prices.export_price_model"] ?? model),
        exportModelConfirmed: Boolean(b.values?.["prices.export_model_confirmed"] ?? true),
      });
    } catch {
      setError("Opslaan mislukt");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section
      className="settings-access-bar export-model-banner"
      data-testid="export-model-banner"
      aria-labelledby="export-model-banner-title"
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
          onClick={() => choose("net_metering")}
        >
          {busy ? EXPORT_MODEL_BANNER.saving : EXPORT_MODEL_BANNER.saldering}
        </button>
        <button
          type="button"
          className="btn-secondary"
          data-testid="export-model-from2027"
          disabled={busy}
          onClick={() => choose("spot_minus_tax")}
        >
          {EXPORT_MODEL_BANNER.from2027}
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
  if (!values) return null;
  return {
    exportPriceModel: String(values["prices.export_price_model"] ?? "spot_minus_tax"),
    exportModelConfirmed: Boolean(values["prices.export_model_confirmed"]),
  };
}
