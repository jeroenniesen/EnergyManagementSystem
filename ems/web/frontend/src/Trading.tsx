import { useEffect, useRef, useState } from "react";

import { apiFetch } from "./auth";
import {
  type TradingStep,
  tradingDryRunStepOk,
  tradingStepStatusText,
} from "./tradingSteps";

type TradingStatus = {
  trading_enabled: boolean;
  min_extra_eur: number;
  max_export_kwh_per_day: number;
  export_mode: string;
  export_price_model: string;
  allow_export_discharge: boolean;
  probe_active?: boolean;
  dry_run: boolean;
  operational: boolean;
  writes_allowed: boolean;
  block_reason: string | null;
  test_battery_allowed?: boolean;
  test_battery_block_reason?: string | null;
  dry_run_reviewed_at?: string | null;
  arm_allowed?: boolean;
  arm_block_reason?: string | null;
  steps?: TradingStep[];
  evaluation: {
    enabled: boolean;
    would_trade: boolean;
    reason: string;
    t_eur?: number;
    z_eur?: number;
    extra_eur?: number;
    export_slots?: number;
    charge_slots?: number;
    projection?: boolean;
    projection_note?: string;
    data_quality?: string | null;
    validator?: string | null;
  };
  copy: {
    house: string;
    sell: string;
  };
};

type TestBatteryResp = {
  ok: boolean;
  detail: string;
  outcome?: string;
  mode?: string | null;
};

const EXPORT_MODES = [
  { value: "peak_slice", label: "Peak slice (best expensive windows)" },
  { value: "full_dump", label: "Full dump (empty to reserve)" },
];

export function TradingView({ canOperate = true }: { canOperate?: boolean }) {
  const [status, setStatus] = useState<TradingStatus | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [armConfirm, setArmConfirm] = useState(false);
  const [testConfirm, setTestConfirm] = useState(false);
  const [testMsg, setTestMsg] = useState<string | null>(null);
  // Raw draft strings so typing "1." does not snap to 1 (Settings NumberInput pattern).
  const [thresholdDraft, setThresholdDraft] = useState<string | null>(null);
  const [capDraft, setCapDraft] = useState<string | null>(null);
  const editingRef = useRef(false);
  editingRef.current = thresholdDraft !== null || capDraft !== null;

  async function load(opts?: { force?: boolean }) {
    // Skip polling while the operator is mid-edit so a 15s refresh does not wipe drafts.
    if (!opts?.force && editingRef.current) return;
    try {
      const r = await apiFetch("/api/trading");
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      setStatus(await r.json());
      setErr(null);
    } catch (e) {
      setErr(String(e));
    }
  }

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const r = await apiFetch("/api/trading");
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const b = await r.json();
        if (alive) setStatus(b);
      } catch (e) {
        if (alive) setErr(String(e));
      }
    })();
    const id = setInterval(() => {
      if (alive) void load();
    }, 15000);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, []);

  async function patchSettings(body: Record<string, number | boolean | string>) {
    setBusy(true);
    setErr(null);
    try {
      const r = await apiFetch("/api/settings", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(body),
      });
      const b = await r.json();
      if (!r.ok) throw new Error(b.detail ?? `HTTP ${r.status}`);
      await load();
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(false);
      setArmConfirm(false);
    }
  }

  async function markDryRunReviewed() {
    setBusy(true);
    setErr(null);
    try {
      const r = await apiFetch("/api/trading/dry-run-reviewed", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({}),
      });
      const b = await r.json();
      if (!r.ok) throw new Error(b.detail ?? `HTTP ${r.status}`);
      await load({ force: true });
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(false);
    }
  }

  async function runTestBattery() {
    setBusy(true);
    setErr(null);
    setTestMsg(null);
    setTestConfirm(false);
    try {
      const r = await apiFetch("/api/trading/test-battery", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({}),
      });
      const b: TestBatteryResp = await r.json();
      if (r.status === 409 || r.status === 422) {
        setTestMsg(b.detail || "Test geweigerd");
      } else if (!r.ok) {
        throw new Error(b.detail ?? `HTTP ${r.status}`);
      } else {
        setTestMsg(b.detail || (b.ok ? "Test gestart" : "Test mislukt"));
      }
      await load();
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(false);
    }
  }

  if (!status && !err) {
    return <section className="trading-view" data-testid="trading-view">Laden…</section>;
  }

  const s = status;
  const liveLabel = !s
    ? ""
    : s.allow_export_discharge && s.writes_allowed
      ? "Live export armed — writes allowed"
      : s.allow_export_discharge
        ? "Armed in settings — writes still blocked by floors"
        : s.dry_run || !s.operational
          ? "Writes blocked (dry-run / Watch only) — export plan-only"
          : "Not armed — export plan-only (vendor AUTO)";
  const showScores =
    !!s?.evaluation.enabled &&
    (s.evaluation.would_trade ||
      (s.evaluation.t_eur ?? 0) !== 0 ||
      (s.evaluation.z_eur ?? 0) !== 0);
  const dryRunStepOk = s ? tradingDryRunStepOk(s.steps) : false;
  const armBlocked =
    !!s &&
    (!s.writes_allowed ||
      !dryRunStepOk ||
      (s.arm_block_reason != null && !s.allow_export_discharge));
  const armBlockHint =
    s && !s.allow_export_discharge
      ? !s.writes_allowed
        ? s.block_reason
        : !dryRunStepOk
          ? "Ontbreekt: stap 2 — bekijk de dry-run"
          : s.arm_block_reason
      : null;

  return (
    <section className="trading-view" data-testid="trading-view">
      <header className="trading-card">
        <h2>Trading</h2>
        <p>
          Compare selling surplus to the grid (path T) with keeping energy for the house
          (path Z). Mode switches only — no watt sliders.
        </p>
      </header>

      {err && (
        <p className="error" role="alert" data-testid="trading-error">
          {err}
        </p>
      )}

      {s && (
        <>
          <div className="trading-card" data-testid="trading-status">
            <h3>Status</h3>
            <p data-testid="trading-live-label">
              <strong>{liveLabel}</strong>
              {s.block_reason ? ` — ${s.block_reason}` : ""}
            </p>
            {showScores && (
              <dl className="trading-metrics" data-testid="trading-scores">
                <div>
                  <dt>Path T</dt>
                  <dd>€{(s.evaluation.t_eur ?? 0).toFixed(2)}</dd>
                </div>
                <div>
                  <dt>Path Z (house)</dt>
                  <dd>€{(s.evaluation.z_eur ?? 0).toFixed(2)}</dd>
                </div>
                <div>
                  <dt>Extra T−Z</dt>
                  <dd>€{(s.evaluation.extra_eur ?? 0).toFixed(2)}</dd>
                </div>
              </dl>
            )}
            <p data-testid="trading-eval-reason">{s.evaluation.reason}</p>
            <p className="muted" data-testid="trading-eval-projection">
              {s.evaluation.projection_note
                ?? "T−Z is a projection of the plan, not the act decision."}
              {s.evaluation.data_quality
                ? ` Data quality: ${s.evaluation.data_quality}.`
                : ""}
              {s.evaluation.validator
                ? ` Plan validator: ${s.evaluation.validator}.`
                : ""}
            </p>
            <p className="muted">
              {s.copy.house} · {s.copy.sell}
            </p>
            <p className="muted">
              Export price model: <code>{s.export_price_model}</code>
              {!s.writes_allowed && " · writes blocked by control floors"}
            </p>
          </div>

          <div className="trading-card" data-testid="trading-settings">
            <h3>Settings</h3>
            <label className="trading-row">
              <span>Enable trading (opt-in)</span>
              <input
                type="checkbox"
                data-testid="trading-enabled"
                checked={s.trading_enabled}
                disabled={!canOperate || busy}
                onChange={(e) =>
                  patchSettings({ "planner.trading_enabled": e.target.checked })
                }
              />
            </label>
            <label className="trading-row">
              <span>Min extra €/day (T over Z)</span>
              <input
                type="number"
                data-testid="trading-threshold"
                min={0}
                max={20}
                step={0.05}
                value={thresholdDraft ?? String(s.min_extra_eur)}
                disabled={!canOperate || busy}
                onChange={(e) => setThresholdDraft(e.target.value)}
                onBlur={() => {
                  const v = Number(thresholdDraft ?? s.min_extra_eur);
                  setThresholdDraft(null);
                  if (!Number.isFinite(v)) return;
                  void patchSettings({ "planner.trading_min_extra_eur": v });
                }}
              />
            </label>
            <label className="trading-row">
              <span>Max export kWh/day (0 = uncapped)</span>
              <input
                type="number"
                data-testid="trading-cap"
                min={0}
                max={50}
                step={0.5}
                value={capDraft ?? String(s.max_export_kwh_per_day)}
                disabled={!canOperate || busy}
                onChange={(e) => setCapDraft(e.target.value)}
                onBlur={() => {
                  const v = Number(capDraft ?? s.max_export_kwh_per_day);
                  setCapDraft(null);
                  if (!Number.isFinite(v)) return;
                  void patchSettings({ "planner.max_export_kwh_per_day": v });
                }}
              />
            </label>
            <label className="trading-row">
              <span>Export slot policy</span>
              <select
                data-testid="trading-export-mode"
                value={s.export_mode}
                disabled={!canOperate || busy}
                onChange={(e) =>
                  patchSettings({ "planner.export_mode": e.target.value })
                }
              >
                {EXPORT_MODES.map((m) => (
                  <option key={m.value} value={m.value}>
                    {m.label}
                  </option>
                ))}
              </select>
            </label>
          </div>

          <div className="trading-card" data-testid="trading-arm-card">
            <h3>Live arming</h3>
            <p>
              Until you arm live export, the planner may still emit{" "}
              <code>EXPORT_FOR_PROFIT</code> but the battery stays on vendor AUTO (no
              forced grid dump). Live export is armed from this page. Arming does not
              bypass Watch only / dry-run floors.
            </p>
            {s.steps && s.steps.length > 0 && (
              <ol className="trading-steps" data-testid="trading-arm-steps">
                {s.steps.map((step) => (
                  <li
                    key={step.id}
                    className={`trading-step ${step.ok ? "ok" : "fail"}`}
                    data-testid={`trading-step-${step.id}`}
                  >
                    <span className="trading-step-mark" aria-hidden="true">
                      {step.ok ? "✓" : "✗"}
                    </span>
                    <span>
                      <span className="sr-only">{tradingStepStatusText(step)}</span>
                      <span aria-hidden="true">{step.label}</span>
                      {step.missing && !step.ok && (
                        <span className="trading-step-missing"> — {step.missing}</span>
                      )}
                    </span>
                  </li>
                ))}
              </ol>
            )}
            {!dryRunStepOk && (
              <p
                className="muted"
                role="status"
                data-testid="trading-arm-step2-missing"
              >
                Ontbreekt: stap 2 — bekijk de dry-run
              </p>
            )}
            {!canOperate ? (
              <p className="muted">Operate permission required to arm.</p>
            ) : (
              <>
                {!dryRunStepOk && (
                  <button
                    type="button"
                    className="btn"
                    data-testid="trading-dry-run-reviewed"
                    disabled={busy}
                    onClick={() => void markDryRunReviewed()}
                  >
                    Ik heb de vergelijking bekeken
                  </button>
                )}
                {armBlockHint && !s.allow_export_discharge && (
                  <p className="muted" data-testid="trading-arm-block-reason">
                    {armBlockHint}
                  </p>
                )}
                {s.allow_export_discharge ? (
                  <button
                    type="button"
                    className="btn"
                    data-testid="trading-disarm"
                    disabled={busy}
                    onClick={() =>
                      patchSettings({ "control.allow_export_discharge": false })
                    }
                  >
                    Disarm live export
                  </button>
                ) : armConfirm ? (
                  <div className="trading-confirm">
                    <p>
                      Confirm: allow forced DISCHARGE into the grid when path T wins? Still
                      needs operational mode and dry-run off.
                    </p>
                    <button
                      type="button"
                      className="btn btn-danger"
                      data-testid="trading-arm-confirm"
                      disabled={busy || armBlocked}
                      onClick={() =>
                        patchSettings({ "control.allow_export_discharge": true })
                      }
                    >
                      Yes, arm live export
                    </button>
                    <button
                      type="button"
                      className="btn"
                      disabled={busy}
                      onClick={() => setArmConfirm(false)}
                    >
                      Cancel
                    </button>
                  </div>
                ) : (
                  <button
                    type="button"
                    className="btn btn-primary"
                    data-testid="trading-arm"
                    disabled={busy || armBlocked}
                    onClick={() => setArmConfirm(true)}
                  >
                    Arm live export…
                  </button>
                )}
              </>
            )}
          </div>

          <div className="trading-card" data-testid="trading-test">
            <h3>Test batterij</h3>
            <p>
              Optional short forced-DISCHARGE probe to confirm the cluster can export.
              Recommended before arming; not a hard gate. Refuses when Watch only / dry-run
              blocks writes, sensor data is unsafe, or while the car is charging.
            </p>
            {testMsg && (
              <p data-testid="trading-test-msg">{testMsg}</p>
            )}
            {!canOperate ? (
              <p className="muted">Operate permission required.</p>
            ) : s.test_battery_allowed === false || (!s.writes_allowed && s.test_battery_allowed === undefined) ? (
              <p className="muted" data-testid="trading-test-blocked">
                {s.test_battery_block_reason
                  || s.block_reason
                  || "Test unavailable — writes or sensor data blocked."}
              </p>
            ) : s.probe_active ? (
              <p className="muted" data-testid="trading-test-running">
                Test batterij running…
              </p>
            ) : testConfirm ? (
              <div className="trading-confirm">
                <p>
                  Start a ~2 minute max-power discharge toward the reserve floor, then
                  return to AUTO? Does not arm live trading. Refused if the car starts
                  charging before you confirm.
                </p>
                <button
                  type="button"
                  className="btn btn-primary"
                  data-testid="trading-test-confirm"
                  disabled={busy}
                  onClick={() => void runTestBattery()}
                >
                  Start test
                </button>
                <button
                  type="button"
                  className="btn"
                  disabled={busy}
                  onClick={() => setTestConfirm(false)}
                >
                  Cancel
                </button>
              </div>
            ) : (
              <button
                type="button"
                className="btn"
                data-testid="trading-test-btn"
                disabled={busy}
                onClick={() => setTestConfirm(true)}
              >
                Test batterij…
              </button>
            )}
          </div>
        </>
      )}
    </section>
  );
}
