import { useEffect, useState } from "react";

import { apiFetch } from "./auth";
import {
  KIND_LABEL,
  type HaProbeDevice,
  type HaProbeResult,
  type HaWizardStep,
  probeErrorMessage,
  tokenCreateUrl,
  trimHaUrl,
  validateHaUrlClient,
} from "./haWizard";

type SettingsValues = Record<string, string | number | boolean>;

type Props = {
  values: SettingsValues;
  canOperate?: boolean;
  onSaved?: () => void;
};

/**
 * Settings → Appliances: step-by-step Home Assistant link (#263).
 * Owns its own POSTs (consumers.ha.* are hidden from the generic schema list).
 */
export function HaWizard({ values, canOperate = true, onSaved }: Props) {
  const connected = Boolean(values["consumers.ha.token.__set"]);
  const storedUrl = String(values["consumers.ha.url"] || "");
  const enabled = Boolean(values["consumers.ha.enabled"]);

  const [step, setStep] = useState<HaWizardStep>(connected ? "status" : "url");
  const [url, setUrl] = useState(storedUrl);
  const [token, setToken] = useState("");
  const [showToken, setShowToken] = useState(false);
  const [urlHint, setUrlHint] = useState<string | null>(null);
  const [httpWarn, setHttpWarn] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [probe, setProbe] = useState<HaProbeResult | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [manualId, setManualId] = useState("");
  const [statusMsg, setStatusMsg] = useState<string | null>(null);

  useEffect(() => {
    if (connected) setStep("status");
  }, [connected]);

  useEffect(() => {
    // Prefill selection from stored device ids when entering pick.
    if (step !== "pick") return;
    try {
      const raw = String(values["consumers.ha.device_ids"] || "[]");
      const ids = JSON.parse(raw) as unknown;
      if (Array.isArray(ids)) {
        setSelected(new Set(ids.map(String).filter(Boolean)));
      }
    } catch {
      /* ignore */
    }
  }, [step, values]);

  function beginConnect() {
    setError(null);
    setStatusMsg(null);
    setToken("");
    setProbe(null);
    setStep("url");
  }

  function onUrlNext() {
    const check = validateHaUrlClient(url);
    if (!check.ok) {
      setUrlHint(check.error ?? "Invalid address");
      setHttpWarn(false);
      return;
    }
    setUrl(check.normalized);
    setUrlHint(null);
    setHttpWarn(Boolean(check.httpLanWarning));
    setStep("create_token");
  }

  async function runTest() {
    setBusy(true);
    setError(null);
    try {
      const body: Record<string, string | boolean> = {
        url: trimHaUrl(url) || storedUrl,
      };
      if (token.trim()) body.token = token.trim();
      else body.use_stored_token = true;
      const r = await apiFetch("/api/consumers/ha/test-connection", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const data = (await r.json().catch(() => ({}))) as HaProbeResult & { detail?: string };
      if (r.status === 429) {
        setError(
          typeof data.detail === "string" && data.detail.trim()
            ? data.detail
            : "Too many connection tests — wait a minute and try again.",
        );
        return;
      }
      setProbe(data);
      if (!data.ok) {
        setError(probeErrorMessage(data));
        return;
      }
      if (data.url) setUrl(data.url);
      const devices = data.devices ?? [];
      setSelected((prev) => {
        const next = new Set(prev);
        for (const d of devices) next.add(d.id);
        return next;
      });
      setStep("pick");
    } catch {
      setError("Couldn’t reach EMS to test the connection. Try again.");
    } finally {
      setBusy(false);
    }
  }

  function toggleDevice(id: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function addManual() {
    const id = manualId.trim();
    if (!id || !id.includes(".")) return;
    setSelected((prev) => new Set(prev).add(id));
    setManualId("");
  }

  async function saveConnection() {
    setBusy(true);
    setError(null);
    try {
      const payload: Record<string, string | boolean> = {
        "consumers.ha.url": trimHaUrl(url) || storedUrl,
        "consumers.ha.enabled": true,
        "consumers.ha.device_ids": JSON.stringify([...selected]),
      };
      if (token.trim()) payload["consumers.ha.token"] = token.trim();
      const r = await apiFetch("/api/settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!r.ok) {
        setError("Couldn’t save the Home Assistant link. Check the fields and try again.");
        return;
      }
      setStep("done");
      setStatusMsg(
        "Saved. Use Apply & restart at the top of Settings so EMS starts using Home Assistant.",
      );
      onSaved?.();
    } catch {
      setError("Couldn’t save the Home Assistant link.");
    } finally {
      setBusy(false);
    }
  }

  async function disconnect() {
    if (!canOperate) return;
    setBusy(true);
    setError(null);
    try {
      const r = await apiFetch("/api/consumers/ha/disconnect", { method: "POST" });
      if (!r.ok) {
        setError("Couldn’t disconnect Home Assistant.");
        return;
      }
      setToken("");
      setProbe(null);
      setSelected(new Set());
      setStep("url");
      setStatusMsg("Disconnected. Apply & restart to finish unloading the adapter.");
      onSaved?.();
    } catch {
      setError("Couldn’t disconnect Home Assistant.");
    } finally {
      setBusy(false);
    }
  }

  const devices: HaProbeDevice[] = probe?.devices ?? [];

  return (
    <section className="ha-wizard" data-testid="ha-wizard">
      <h3 className="ha-wizard-title">Home Assistant</h3>
      <p className="ha-wizard-lead">
        Link washers and dryers from Home Assistant. EMS only reads status — it never starts
        cycles from here.
      </p>

      {statusMsg && (
        <p className="ha-wizard-status" data-testid="ha-wizard-status" role="status">
          {statusMsg}
        </p>
      )}
      {error && (
        <p className="ha-wizard-error" data-testid="ha-wizard-error" role="alert">
          {error}
        </p>
      )}

      {step === "status" && connected && (
        <div className="ha-wizard-panel" data-testid="ha-wizard-connected">
          <p>
            <strong>{enabled ? "Connected" : "Token saved (disabled)"}</strong>
            {storedUrl ? <> — {storedUrl}</> : null}
          </p>
          <div className="ha-wizard-actions">
            <button
              type="button"
              className="btn-ghost"
              disabled={!canOperate || busy}
              onClick={beginConnect}
              data-testid="ha-wizard-replace"
            >
              Replace token
            </button>
            <button
              type="button"
              className="btn-ghost"
              disabled={!canOperate || busy}
              onClick={() => void disconnect()}
              data-testid="ha-wizard-disconnect"
            >
              Disconnect
            </button>
          </div>
        </div>
      )}

      {step === "url" && (
        <div className="ha-wizard-panel" data-testid="ha-wizard-step-url">
          <label className="ha-wizard-label" htmlFor="ha-url">
            Home Assistant address
          </label>
          <input
            id="ha-url"
            className="ha-wizard-input"
            value={url}
            disabled={!canOperate || busy}
            onChange={(e) => setUrl(e.target.value)}
            placeholder="http://homeassistant.local:8123"
            autoComplete="off"
            data-testid="ha-wizard-url"
          />
          <p className="ha-wizard-hint">
            Examples: <code>http://homeassistant.local:8123</code> or your Nabu Casa URL
            {" "}(<code>https://….ui.nabu.casa</code>).
          </p>
          {urlHint && <p className="ha-wizard-error">{urlHint}</p>}
          <div className="ha-wizard-actions">
            <button
              type="button"
              className="btn-primary"
              disabled={!canOperate || busy}
              onClick={onUrlNext}
              data-testid="ha-wizard-url-next"
            >
              Next
            </button>
          </div>
        </div>
      )}

      {step === "create_token" && (
        <div className="ha-wizard-panel" data-testid="ha-wizard-step-create">
          {httpWarn && (
            <p className="ha-wizard-warn" data-testid="ha-wizard-http-warn">
              You’re using http:// with a non-local host. Prefer https:// outside your home
              network — the long-lived token would otherwise travel in cleartext.
            </p>
          )}
          <ol className="ha-wizard-steps">
            <li>
              Open{" "}
              <a
                href={tokenCreateUrl(url)}
                target="_blank"
                rel="noreferrer"
                data-testid="ha-wizard-token-link"
              >
                {tokenCreateUrl(url)}
              </a>
              {" "}(Profile → Security).
            </li>
            <li>Under <strong>Long-lived access tokens</strong>, choose <strong>Create token</strong>.</li>
            <li>Name it <strong>EMS</strong> and create it.</li>
            <li>Copy the token now — Home Assistant shows it only once.</li>
          </ol>
          <div className="ha-wizard-actions">
            <button
              type="button"
              className="btn-ghost"
              disabled={busy}
              onClick={() => setStep("url")}
            >
              Back
            </button>
            <button
              type="button"
              className="btn-primary"
              disabled={!canOperate || busy}
              onClick={() => setStep("paste_token")}
              data-testid="ha-wizard-create-next"
            >
              I have the token
            </button>
          </div>
        </div>
      )}

      {step === "paste_token" && (
        <div className="ha-wizard-panel" data-testid="ha-wizard-step-paste">
          {httpWarn && (
            <p className="ha-wizard-warn" data-testid="ha-wizard-http-warn">
              You’re using http:// with a non-local host. Prefer https:// outside your home
              network — the long-lived token would otherwise travel in cleartext.
            </p>
          )}
          <label className="ha-wizard-label" htmlFor="ha-token">
            Long-lived access token
          </label>
          <div className="ha-wizard-token-row">
            <input
              id="ha-token"
              className="ha-wizard-input"
              type={showToken ? "text" : "password"}
              value={token}
              disabled={!canOperate || busy}
              onChange={(e) => setToken(e.target.value)}
              autoComplete="off"
              data-testid="ha-wizard-token"
            />
            <button
              type="button"
              className="btn-ghost"
              onClick={() => setShowToken((v) => !v)}
              data-testid="ha-wizard-token-toggle"
            >
              {showToken ? "Hide" : "Show"}
            </button>
          </div>
          <div className="ha-wizard-actions">
            <button
              type="button"
              className="btn-ghost"
              disabled={busy}
              onClick={() => setStep("create_token")}
            >
              Back
            </button>
            <button
              type="button"
              className="btn-primary"
              disabled={!canOperate || busy || !token.trim()}
              onClick={() => {
                setToken(token.trim());
                setStep("test");
              }}
              data-testid="ha-wizard-paste-next"
            >
              Next
            </button>
          </div>
        </div>
      )}

      {step === "test" && (
        <div className="ha-wizard-panel" data-testid="ha-wizard-step-test">
          {httpWarn && (
            <p className="ha-wizard-warn" data-testid="ha-wizard-http-warn">
              You’re using http:// with a non-local host. Prefer https:// outside your home
              network — the long-lived token would otherwise travel in cleartext.
            </p>
          )}
          <p>EMS will ask Home Assistant for appliance entities (read-only).</p>
          <div className="ha-wizard-actions">
            <button
              type="button"
              className="btn-ghost"
              disabled={busy}
              onClick={() => setStep("paste_token")}
            >
              Back
            </button>
            <button
              type="button"
              className="btn-primary"
              disabled={!canOperate || busy}
              onClick={() => void runTest()}
              data-testid="ha-wizard-test"
            >
              {busy ? "Testing…" : "Test connection"}
            </button>
          </div>
        </div>
      )}

      {step === "pick" && (
        <div className="ha-wizard-panel" data-testid="ha-wizard-step-pick">
          <p className="ha-wizard-hint">{probe?.summary ?? "Choose appliances to show in Insights."}</p>
          {probe?.warning === "no_devices" && (
            <p className="ha-wizard-warn">{probe.message}</p>
          )}
          <ul className="ha-wizard-devices">
            {devices.map((d) => (
              <li key={d.id}>
                <label>
                  <input
                    type="checkbox"
                    checked={selected.has(d.id)}
                    disabled={!canOperate || busy}
                    onChange={() => toggleDevice(d.id)}
                    data-testid={`ha-wizard-device-${d.id}`}
                  />
                  <span>{d.name}</span>
                  <span className="ha-wizard-kind">
                    {KIND_LABEL[d.kind] ?? KIND_LABEL.other}
                  </span>
                </label>
              </li>
            ))}
          </ul>
          <div className="ha-wizard-manual">
            <label className="ha-wizard-label" htmlFor="ha-manual">
              Add entity id manually
            </label>
            <div className="ha-wizard-token-row">
              <input
                id="ha-manual"
                className="ha-wizard-input"
                value={manualId}
                disabled={!canOperate || busy}
                onChange={(e) => setManualId(e.target.value)}
                placeholder="switch.washer"
                data-testid="ha-wizard-manual"
              />
              <button
                type="button"
                className="btn-ghost"
                disabled={!canOperate || busy || !manualId.trim()}
                onClick={addManual}
                data-testid="ha-wizard-manual-add"
              >
                Add
              </button>
            </div>
            {[...selected]
              .filter((id) => !devices.some((d) => d.id === id))
              .map((id) => (
                <label key={id} className="ha-wizard-manual-item">
                  <input
                    type="checkbox"
                    checked
                    disabled={!canOperate || busy}
                    onChange={() => toggleDevice(id)}
                  />
                  <span>{id}</span>
                </label>
              ))}
          </div>
          <div className="ha-wizard-actions">
            <button
              type="button"
              className="btn-ghost"
              disabled={busy}
              onClick={() => setStep("test")}
            >
              Back
            </button>
            <button
              type="button"
              className="btn-primary"
              disabled={!canOperate || busy}
              onClick={() => void saveConnection()}
              data-testid="ha-wizard-save"
            >
              {busy ? "Saving…" : "Save & finish"}
            </button>
          </div>
        </div>
      )}

      {step === "done" && (
        <div className="ha-wizard-panel" data-testid="ha-wizard-step-done">
          <p>
            Home Assistant is saved
            {selected.size > 0 ? ` with ${selected.size} selected appliance${selected.size === 1 ? "" : "s"}` : ""}.
          </p>
          <p className="ha-wizard-hint">
            Restart is required for the URL and token. Use <strong>Apply &amp; restart</strong> when
            the banner appears.
          </p>
          <div className="ha-wizard-actions">
            <button
              type="button"
              className="btn-primary"
              onClick={() => setStep("status")}
              data-testid="ha-wizard-done"
            >
              Done
            </button>
          </div>
        </div>
      )}

      {!connected && step === "status" && (
        <div className="ha-wizard-actions">
          <button
            type="button"
            className="btn-primary"
            disabled={!canOperate}
            onClick={beginConnect}
            data-testid="ha-wizard-start"
          >
            Connect Home Assistant
          </button>
        </div>
      )}
    </section>
  );
}
