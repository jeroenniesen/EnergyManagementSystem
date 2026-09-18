import { useEffect, useState, type FormEvent } from "react";
import { apiFetch } from "./auth";
import "./tariff-tools.css";

type Period = {
  start_date: string; end_date: string; raw_includes_import_components: boolean;
  import_tax_eur_per_kwh: number; import_surcharge_eur_per_kwh: number;
  export_tax_eur_per_kwh: number; export_surcharge_eur_per_kwh: number;
  export_fee_eur_per_kwh: number; fixed_export_eur_per_kwh: number | null;
};
type Tariffs = { periods: Period[]; timezone: string };
type Invoice = {
  complete: boolean; sample_coverage: number; price_coverage: number;
  observed_variable_cost_eur: number | null; estimated_total_eur: number | null;
  difference_eur: number | null; basis: string;
};
const COMPONENTS = [
  ["import_tax_eur_per_kwh", "Import tax"],
  ["import_surcharge_eur_per_kwh", "Import surcharge"],
  ["export_tax_eur_per_kwh", "Export tax"],
  ["export_surcharge_eur_per_kwh", "Export adjustment"],
  ["export_fee_eur_per_kwh", "Export fee"],
] as const;
const money = (value: number | null) => value == null ? "Unavailable" : `€${value.toFixed(2)}`;

async function request<T>(path: string, body?: object): Promise<T> {
  const response = await apiFetch(path, body ? {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  } : undefined);
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(typeof error.detail === "string" ? error.detail : `Request failed (${response.status})`);
  }
  return response.json();
}

export function TariffTools({ canOperate }: { canOperate: boolean }) {
  const [tariffs, setTariffs] = useState<Tariffs | null>(null);
  const [tariffError, setTariffError] = useState("");
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [invoice, setInvoice] = useState<Invoice | null>(null);
  const [invoiceError, setInvoiceError] = useState("");
  const [comparing, setComparing] = useState(false);
  useEffect(() => {
    let active = true;
    request<Tariffs>("/api/tariffs").then(value => { if (active) setTariffs(value); })
      .catch(error => { if (active) setTariffError(error.message); });
    return () => { active = false; };
  }, []);
  async function addPeriod(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!canOperate) return;
    const form = event.currentTarget;
    const data = new FormData(form);
    const components = Object.fromEntries(COMPONENTS.map(([key]) => [key, Number(data.get(key))]));
    setSaving(true); setTariffError(""); setSaved(false);
    try {
      setTariffs(await request<Tariffs>("/api/tariffs", {
        ...components, start_date: data.get("start_date"), end_date: data.get("end_date"),
        raw_includes_import_components: data.get("includes") === "yes",
        fixed_export_eur_per_kwh: data.get("fixed_export") === "" ? null : Number(data.get("fixed_export")),
      }));
      setSaved(true); form.reset(); setInvoice(null);
    } catch (error) { setTariffError((error as Error).message); }
    finally { setSaving(false); }
  }
  async function compare(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    setComparing(true); setInvoiceError(""); setInvoice(null);
    try {
      setInvoice(await request<Invoice>("/api/invoice-reconciliation", {
        start_date: data.get("start_date"), end_date: data.get("end_date"),
        invoice_eur: Number(data.get("invoice_eur")), fixed_cost_eur: Number(data.get("fixed_cost_eur")),
      }));
    } catch (error) { setInvoiceError((error as Error).message); }
    finally { setComparing(false); }
  }
  return <section className="card tariff-tools" data-testid="tariff-tools" aria-label="Tariffs and invoice">
    <h2>Tariffs and invoice</h2>
    <p>Use your contract’s VAT-inclusive rates. Dates follow {tariffs?.timezone ?? "the site timezone"}; end dates are exclusive.</p>
    {!tariffs && !tariffError && <p role="status">Loading tariffs…</p>}
    {tariffs && <div data-testid="tariff-period-list">
      {tariffs.periods.length === 0 ? <p>No date-effective periods. Existing tariff settings apply.</p> :
        tariffs.periods.map(period => <details key={period.start_date}>
          <summary>{period.start_date} to {period.end_date} (exclusive)</summary>
          <dl>{COMPONENTS.map(([key, label]) => <div key={key}><dt>{label}</dt><dd>€{period[key].toFixed(4)}/kWh</dd></div>)}
            <div><dt>Provider price includes import tax and surcharge</dt><dd>{period.raw_includes_import_components ? "Yes" : "No"}</dd></div>
            <div><dt>Export basis</dt><dd>{period.fixed_export_eur_per_kwh == null ? "Spot plus export adjustment and tax, minus fee" : `Fixed €${period.fixed_export_eur_per_kwh.toFixed(4)}/kWh minus fee`}</dd></div>
          </dl>
        </details>)}
    </div>}
    {tariffError && <p role="alert">{tariffError}</p>}
    {saved && <p role="status">Tariff period saved.</p>}
    {canOperate ? <details className="tariff-add">
      <summary>Add a tariff period</summary>
      <p>Saved periods cannot be edited. Add subsequent periods after the last end date. Gaps remain unpriced; assumptions before the first period are preserved.</p>
      <form onSubmit={addPeriod}>
        <div className="tariff-fields">
          <label className="field">Tariff start date<input name="start_date" type="date" required /></label>
          <label className="field">Tariff end date (exclusive)<input name="end_date" type="date" required /></label>
          <label className="field">Provider price includes import tax and surcharge<select name="includes" defaultValue="" required>
            <option value="" disabled>Choose from your contract</option><option value="yes">Yes</option><option value="no">No</option>
          </select></label>
          {COMPONENTS.map(([key, label]) => <label className="field" key={key}>{label} (€/kWh)<input name={key} type="number" step="any" required /></label>)}
          <label className="field">Fixed export rate (€/kWh, optional)<input name="fixed_export" type="number" step="any" /></label>
        </div>
        <p>Leave fixed export blank to use spot plus the export components above. The export fee is deducted in either case.</p>
        <button type="submit" disabled={saving}>{saving ? "Saving…" : "Save tariff period"}</button>
      </form>
    </details> : <p>Operator access is required to add tariff periods.</p>}
    <h3>Compare an electricity invoice</h3>
    <p>Enter the electricity total and its fixed costs for the same period. This comparison is read-only and uses recorded energy.</p>
    <form onSubmit={compare}>
      <div className="tariff-fields">
        <label className="field">Invoice start date<input type="date" name="start_date" required /></label>
        <label className="field">Invoice end date (exclusive)<input type="date" name="end_date" required /></label>
        <label className="field">Invoice amount (€)<input name="invoice_eur" type="number" step="0.01" required /></label>
        <label className="field">Fixed costs for this period (€)<input name="fixed_cost_eur" type="number" step="0.01" required /></label>
      </div>
      <button type="submit" disabled={comparing}>{comparing ? "Comparing…" : "Compare invoice"}</button>
    </form>
    {invoiceError && <p role="alert">{invoiceError}</p>}
    {invoice && <div data-testid="invoice-result" role="status">
      <p>{Math.round(invoice.sample_coverage * 100)}% observed · {Math.round(invoice.price_coverage * 100)}% of observed time priced</p>
      <p>Observed variable cost: {money(invoice.observed_variable_cost_eur)}</p>
      {invoice.complete ? <><p>Estimated total: {money(invoice.estimated_total_eur)}</p><p>Model minus invoice: {money(invoice.difference_eur)}. A positive value means the model is higher.</p></> :
        <p>Full invoice comparison unavailable. Missing observations, prices or tariff coverage prevent a reliable total.</p>}
      <p>{invoice.basis}</p>
    </div>}
  </section>;
}
