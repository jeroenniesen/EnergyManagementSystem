import { useCallback, useEffect, useRef, useState } from "react";

import { apiFetch } from "./auth";
import { SETUP_CHECKLIST } from "./labels";
import {
  batteryChecklistOutcome,
  buildBatteryLinkPayload,
  buildP1LinkPayload,
  needsP1IpField,
  p1ChecklistOutcome,
  P1_RESTART_MESSAGE,
  persistBatteryIp,
  persistP1Ip,
  persistSetupStep,
  privateIpv4Hint,
  readPersistedBatteryIp,
  readPersistedP1Ip,
  readPersistedSetupStep,
  clearSetupPersistence,
  interpretRestartResponse,
  restartPollConfig,
  type BatteryWizardPhase,
} from "./setupFlow";

type ChecklistItem = {
  key: string;
  sentence: string;
  status: "Werkt" | "Ontbreekt" | "Optioneel" | "Nodig om te sturen";
  button_label: string;
  settings_section: string;
};

type Checklist = {
  headline: string;
  steps_remaining: number;
  watch_ready: boolean;
  items: ChecklistItem[];
};

type RestartAvailability = { available: boolean; reason: string };

type Props = {
  onOpenSettings: (section: string) => void;
};

function statusClass(status: ChecklistItem["status"]): string {
  switch (status) {
    case "Werkt":
      return "setup-status-ok";
    case "Optioneel":
      return "setup-status-opt";
    case "Nodig om te sturen":
      return "setup-status-control";
    default:
      return "setup-status-miss";
  }
}

/** Onboarding checklist (#290) + battery link step (#291). */
export function Setup({ onOpenSettings }: Props) {
  const [data, setData] = useState<Checklist | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [fetchGen, setFetchGen] = useState(0);
  const [batteryOpen, setBatteryOpen] = useState(false);
  const [batteryIp, setBatteryIp] = useState("");
  const [p1Ip, setP1Ip] = useState("");
  const [settingsP1, setSettingsP1] = useState("");
  const [wizardPhase, setWizardPhase] = useState<BatteryWizardPhase>("form");
  const [wizardMessage, setWizardMessage] = useState<string | null>(null);
  const [clientHint, setClientHint] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [p1Open, setP1Open] = useState(false);
  const [p1WizardIp, setP1WizardIp] = useState("");
  const [p1WizardPhase, setP1WizardPhase] = useState<BatteryWizardPhase>("form");
  const [p1WizardMessage, setP1WizardMessage] = useState<string | null>(null);
  const [p1LinkBattery, setP1LinkBattery] = useState(false);
  const [p1ClientHint, setP1ClientHint] = useState<string | null>(null);
  const [p1SaveError, setP1SaveError] = useState<string | null>(null);
  const [p1RestartBusyMsg, setP1RestartBusyMsg] = useState<string | null>(null);
  const [p1PollTimedOut, setP1PollTimedOut] = useState(false);
  const [restartAvailable, setRestartAvailable] = useState<RestartAvailability | null>(null);
  const [restartBusyMsg, setRestartBusyMsg] = useState<string | null>(null);
  const [pollTimedOut, setPollTimedOut] = useState(false);
  const restartAliveRef = useRef(true);
  const restartRequestedRef = useRef(false);
  const p1RestartRequestedRef = useRef(false);

  const retry = useCallback(() => setFetchGen((g) => g + 1), []);

  async function loadChecklist() {
    const r = await apiFetch("/api/setup/checklist");
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    return r.json() as Promise<Checklist>;
  }

  useEffect(() => {
    let alive = true;
    setError(null);
    loadChecklist()
      .then((body) => {
        if (alive) setData(body);
      })
      .catch(() => {
        if (alive) {
          setData(null);
          setError("Kon de checklist niet laden. Probeer het later opnieuw.");
        }
      });
    return () => {
      alive = false;
    };
  }, [fetchGen]);

  useEffect(() => {
    restartAliveRef.current = true;
    return () => {
      restartAliveRef.current = false;
    };
  }, []);

  async function refreshRestartCapability() {
    try {
      const r = await apiFetch("/api/auth/me");
      if (!r.ok) return;
      const b = await r.json();
      if (b.restart_available) setRestartAvailable(b.restart_available);
    } catch {
      /* best-effort */
    }
  }

  async function refreshSettingsP1(): Promise<string> {
    try {
      const r = await apiFetch("/api/settings");
      if (!r.ok) return "";
      const b = await r.json();
      const val = String(b.values?.["meters.p1_ip"] ?? "");
      setSettingsP1(val);
      return val;
    } catch {
      return "";
    }
  }

  useEffect(() => {
    const step = readPersistedSetupStep();
    if (step === "battery") {
      setBatteryOpen(true);
      setBatteryIp(readPersistedBatteryIp());
      void refreshSettingsP1();
      void refreshRestartCapability();
    }
    if (step === "p1") {
      setP1Open(true);
      void refreshSettingsP1().then((fromSettings) => {
        setP1WizardIp((prev) => prev.trim() || fromSettings.trim() || readPersistedP1Ip());
      });
      void refreshRestartCapability();
    }
  }, []);

  useEffect(() => {
    if (!batteryOpen || !data) return;
    if (readPersistedSetupStep() !== "battery") return;
    const savedIp = readPersistedBatteryIp();
    if (!savedIp) return;
    const battery = data.items.find((i) => i.key === "battery");
    const outcome = batteryChecklistOutcome(battery?.status, savedIp);
    if (outcome) {
      setWizardPhase(outcome.phase);
      setWizardMessage(outcome.message);
    }
  }, [batteryOpen, data]);

  useEffect(() => {
    if (!p1Open || !data) return;
    if (readPersistedSetupStep() !== "p1") return;
    const savedIp = readPersistedP1Ip() || settingsP1;
    if (!savedIp.trim()) return;
    const p1 = data.items.find((i) => i.key === "p1");
    const battery = data.items.find((i) => i.key === "battery");
    const outcome = p1ChecklistOutcome(p1?.status, battery?.status, savedIp);
    if (outcome) {
      setP1WizardPhase(outcome.phase);
      setP1WizardMessage(outcome.message);
      setP1LinkBattery(Boolean(outcome.linkBattery));
    }
  }, [p1Open, data, settingsP1]);

  async function pollForRestart(initialBootId: string) {
    const { intervalMs, timeoutMs } = restartPollConfig();
    const start = Date.now();
    while (Date.now() - start < timeoutMs) {
      await new Promise((resolve) => setTimeout(resolve, intervalMs));
      try {
        const r = await fetch("/health/live");
        if (r.ok) {
          const b = await r.json();
          if (typeof b.boot_id === "string" && b.boot_id !== initialBootId) {
            window.location.reload();
            return;
          }
        }
      } catch {
        /* mid-restart */
      }
    }
    if (restartAliveRef.current) {
      setPollTimedOut(true);
      setWizardPhase("form");
    }
  }

  async function pollForP1Restart(initialBootId: string) {
    const { intervalMs, timeoutMs } = restartPollConfig();
    const start = Date.now();
    while (Date.now() - start < timeoutMs) {
      await new Promise((resolve) => setTimeout(resolve, intervalMs));
      try {
        const r = await fetch("/health/live");
        if (r.ok) {
          const b = await r.json();
          if (typeof b.boot_id === "string" && b.boot_id !== initialBootId) {
            window.location.reload();
            return;
          }
        }
      } catch {
        /* mid-restart */
      }
    }
    if (restartAliveRef.current) {
      setP1PollTimedOut(true);
      setP1WizardPhase("form");
    }
  }

  async function requestRestartOnce() {
    if (restartRequestedRef.current) return;
    restartRequestedRef.current = true;
    setRestartBusyMsg(null);
    setPollTimedOut(false);
    try {
      const r = await apiFetch("/api/system/restart", { method: "POST" });
      const b = (await r.json().catch(() => ({}))) as Record<string, unknown>;
      const dispatch = interpretRestartResponse(r.status, b);
      if (dispatch.kind === "started") {
        setWizardPhase("restarting");
        void pollForRestart(dispatch.bootId);
        return;
      }
      if (dispatch.kind === "busy" || dispatch.kind === "not_supervised") {
        restartRequestedRef.current = false;
        if (dispatch.kind === "not_supervised") {
          setRestartAvailable({ available: false, reason: "not_supervised" });
          setWizardPhase("manual_restart");
        } else {
          setRestartBusyMsg("Het systeem is bezig — probeer het over enkele seconden opnieuw.");
          setWizardPhase("form");
        }
        return;
      }
      throw new Error(dispatch.message);
    } catch (e) {
      restartRequestedRef.current = false;
      setSaveError(e instanceof Error ? e.message : String(e));
      setWizardPhase("form");
    }
  }

  async function requestP1RestartOnce() {
    if (p1RestartRequestedRef.current) return;
    p1RestartRequestedRef.current = true;
    setP1RestartBusyMsg(null);
    setP1PollTimedOut(false);
    try {
      const r = await apiFetch("/api/system/restart", { method: "POST" });
      const b = (await r.json().catch(() => ({}))) as Record<string, unknown>;
      const dispatch = interpretRestartResponse(r.status, b);
      if (dispatch.kind === "started") {
        setP1WizardPhase("restarting");
        void pollForP1Restart(dispatch.bootId);
        return;
      }
      if (dispatch.kind === "busy" || dispatch.kind === "not_supervised") {
        p1RestartRequestedRef.current = false;
        if (dispatch.kind === "not_supervised") {
          setRestartAvailable({ available: false, reason: "not_supervised" });
          setP1WizardPhase("manual_restart");
        } else {
          setP1RestartBusyMsg("Het systeem is bezig — probeer het over enkele seconden opnieuw.");
          setP1WizardPhase("form");
        }
        return;
      }
      throw new Error(dispatch.message);
    } catch (e) {
      p1RestartRequestedRef.current = false;
      setP1SaveError(e instanceof Error ? e.message : String(e));
      setP1WizardPhase("form");
    }
  }

  function openBatteryWizard() {
    setBatteryOpen(true);
    setWizardPhase("form");
    setWizardMessage(null);
    setSaveError(null);
    setClientHint(null);
    setRestartBusyMsg(null);
    setPollTimedOut(false);
    restartRequestedRef.current = false;
    persistSetupStep("battery");
    void refreshSettingsP1();
    void refreshRestartCapability();
  }

  function retryBattery() {
    setWizardPhase("form");
    setWizardMessage(null);
    setSaveError(null);
    setClientHint(null);
    setRestartBusyMsg(null);
    setPollTimedOut(false);
    restartRequestedRef.current = false;
  }

  async function saveAndTest() {
    const hint = privateIpv4Hint(batteryIp);
    if (needsP1IpField(settingsP1) && p1Ip.trim()) {
      const p1Hint = privateIpv4Hint(p1Ip);
      if (p1Hint) {
        setClientHint(p1Hint);
        return;
      }
    }
    if (hint) {
      setClientHint(hint);
      return;
    }
    setClientHint(null);
    setSaveError(null);
    const payload = buildBatteryLinkPayload(batteryIp, p1Ip, settingsP1);
    try {
      const r = await apiFetch("/api/setup/settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const b = await r.json().catch(() => ({}));
      if (!r.ok) {
        const errMsg =
          (b.errors && Object.values(b.errors).join(" ")) || b.detail || `HTTP ${r.status}`;
        setSaveError(String(errMsg));
        return;
      }
      persistSetupStep("battery");
      persistBatteryIp(batteryIp);
      if (b.restart_required) {
        if (restartAvailable?.available === false) {
          setWizardPhase("manual_restart");
          return;
        }
        setWizardPhase("restarting");
        await requestRestartOnce();
        return;
      }
      const body = await loadChecklist();
      setData(body);
      const battery = body.items.find((i) => i.key === "battery");
      const outcome = batteryChecklistOutcome(battery?.status, batteryIp);
      if (outcome) {
        setWizardPhase(outcome.phase);
        setWizardMessage(outcome.message);
        if (outcome.phase === "success") {
          clearSetupPersistence();
        }
      }
    } catch (e) {
      setSaveError(e instanceof Error ? e.message : String(e));
    }
  }

  function openP1Wizard() {
    setP1Open(true);
    setP1WizardPhase("form");
    setP1WizardMessage(null);
    setP1LinkBattery(false);
    setP1SaveError(null);
    setP1ClientHint(null);
    setP1RestartBusyMsg(null);
    setP1PollTimedOut(false);
    p1RestartRequestedRef.current = false;
    persistSetupStep("p1");
    void refreshSettingsP1().then((fromSettings) => {
      setP1WizardIp((prev) => prev.trim() || fromSettings.trim() || readPersistedP1Ip());
    });
    void refreshRestartCapability();
  }

  function retryP1() {
    setP1WizardPhase("form");
    setP1WizardMessage(null);
    setP1LinkBattery(false);
    setP1SaveError(null);
    setP1ClientHint(null);
    setP1RestartBusyMsg(null);
    setP1PollTimedOut(false);
    p1RestartRequestedRef.current = false;
  }

  async function beginP1Restart() {
    if (restartAvailable?.available === false) {
      setP1WizardPhase("manual_restart");
      return;
    }
    setP1WizardPhase("restarting");
    await requestP1RestartOnce();
  }

  async function saveAndTestP1() {
    const payload = buildP1LinkPayload(p1WizardIp, settingsP1);
    const effectiveIp = (p1WizardIp.trim() || settingsP1.trim());
    if (payload !== null) {
      const hint = privateIpv4Hint(effectiveIp, "p1");
      if (hint) {
        setP1ClientHint(hint);
        return;
      }
    } else if (!settingsP1.trim()) {
      setP1ClientHint(privateIpv4Hint("", "p1"));
      return;
    }
    setP1ClientHint(null);
    setP1SaveError(null);
    try {
      if (payload !== null) {
        const r = await apiFetch("/api/setup/settings", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
        const b = await r.json().catch(() => ({}));
        if (!r.ok) {
          const errMsg =
            (b.errors && Object.values(b.errors).join(" ")) || b.detail || `HTTP ${r.status}`;
          setP1SaveError(String(errMsg));
          return;
        }
        persistSetupStep("p1");
        persistP1Ip(effectiveIp);
        if (b.restart_required) {
          await beginP1Restart();
          return;
        }
      } else {
        persistSetupStep("p1");
        persistP1Ip(effectiveIp);
        await beginP1Restart();
        return;
      }
      const body = await loadChecklist();
      setData(body);
      const p1 = body.items.find((i) => i.key === "p1");
      const battery = body.items.find((i) => i.key === "battery");
      const outcome = p1ChecklistOutcome(p1?.status, battery?.status, effectiveIp);
      if (outcome) {
        setP1WizardPhase(outcome.phase);
        setP1WizardMessage(outcome.message);
        setP1LinkBattery(Boolean(outcome.linkBattery));
        if (outcome.phase === "success") {
          clearSetupPersistence();
        }
      }
    } catch (e) {
      setP1SaveError(e instanceof Error ? e.message : String(e));
    }
  }

  function goToBatteryFromP1() {
    setP1Open(false);
    openBatteryWizard();
    setBatteryIp(readPersistedBatteryIp());
  }

  const showP1 = needsP1IpField(settingsP1);

  return (
    <section className="setup-checklist" data-testid="setup-checklist">
      <h2 className="setup-checklist-title">Inrichten</h2>
      <p className="setup-checklist-lead">
        Zo zie je wat EMS nog nodig heeft voordat het veilig mee kan kijken. EMS blijft in
        kijkmodus tot je later zelf live sturen inschakelt.
      </p>
      {data === null && !error && (
        <p className="loading" data-testid="setup-checklist-loading">
          {SETUP_CHECKLIST.loading}
        </p>
      )}
      {error && (
        <div className="setup-checklist-error-row" data-testid="setup-checklist-error-row">
          <p className="error" role="alert" data-testid="setup-checklist-error">
            {error}
          </p>
          <button
            type="button"
            className="btn secondary setup-checklist-btn"
            data-testid="setup-checklist-retry"
            onClick={retry}
          >
            {SETUP_CHECKLIST.retry}
          </button>
        </div>
      )}
      {data && (
        <>
          <p className="setup-checklist-headline" data-testid="setup-checklist-headline">
            {data.headline}
          </p>
          <ul className="setup-checklist-items">
            {data.items.map((item) => (
              <li key={item.key} className="setup-checklist-item" data-testid={`setup-item-${item.key}`}>
                <div className="setup-checklist-row">
                  <span className={`setup-checklist-status ${statusClass(item.status)}`}>
                    {item.status}
                  </span>
                  <p className="setup-checklist-sentence">{item.sentence}</p>
                </div>
                {item.key === "p1" && p1Open ? (
                  <div className="setup-p1-wizard" data-testid="setup-p1-wizard">
                    {p1WizardPhase === "restarting" && (
                      <p className="setup-wizard-msg" data-testid="setup-p1-restarting">
                        {P1_RESTART_MESSAGE}
                      </p>
                    )}
                    {p1WizardPhase === "manual_restart" && (
                      <p className="setup-wizard-msg" data-testid="setup-p1-restart-manual-hint">
                        Herstart EMS handmatig met{" "}
                        <code>make restart</code> of <code>scripts/restart.sh</code>, en herlaad
                        daarna deze pagina.
                      </p>
                    )}
                    {p1WizardPhase === "success" && p1WizardMessage && (
                      <p className="setup-wizard-success" data-testid="setup-p1-success" role="status">
                        {p1WizardMessage}
                      </p>
                    )}
                    {p1WizardPhase === "error" && p1WizardMessage && (
                      <>
                        <p className="setup-wizard-error" data-testid="setup-p1-error" role="alert">
                          {p1WizardMessage}
                        </p>
                        {p1LinkBattery ? (
                          <button
                            type="button"
                            className="btn secondary"
                            data-testid="setup-p1-goto-battery"
                            onClick={goToBatteryFromP1}
                          >
                            Batterij instellen
                          </button>
                        ) : (
                          <button
                            type="button"
                            className="btn secondary"
                            data-testid="setup-p1-retry"
                            onClick={retryP1}
                          >
                            Opnieuw proberen
                          </button>
                        )}
                      </>
                    )}
                    {(p1WizardPhase === "form" || p1PollTimedOut) && (
                      <>
                        <label className="setup-field-label" htmlFor="setup-p1-wizard-ip">
                          IP-adres P1-meter (privé IPv4)
                        </label>
                        <input
                          id="setup-p1-wizard-ip"
                          className="setup-field-input"
                          data-testid="setup-p1-wizard-ip"
                          type="text"
                          autoComplete="off"
                          value={p1WizardIp}
                          onChange={(e) => setP1WizardIp(e.target.value)}
                          placeholder="192.168.1.10"
                        />
                        {p1ClientHint && (
                          <p className="setup-wizard-hint" role="alert">{p1ClientHint}</p>
                        )}
                        {p1SaveError && (
                          <p className="setup-wizard-error" role="alert">{p1SaveError}</p>
                        )}
                        {p1RestartBusyMsg && (
                          <p className="setup-wizard-hint" role="status">{p1RestartBusyMsg}</p>
                        )}
                        {p1PollTimedOut && (
                          <button
                            type="button"
                            className="btn secondary"
                            data-testid="setup-p1-manual-reload"
                            onClick={() => window.location.reload()}
                          >
                            Pagina herladen
                          </button>
                        )}
                        <button
                          type="button"
                          className="btn primary setup-p1-save"
                          data-testid="setup-p1-save"
                          onClick={() => void saveAndTestP1()}
                          disabled={p1WizardPhase === "restarting"}
                        >
                          Opslaan en testen
                        </button>
                      </>
                    )}
                  </div>
                ) : item.key === "battery" && batteryOpen ? (
                  <div className="setup-battery-wizard" data-testid="setup-battery-wizard">
                    {wizardPhase === "restarting" && (
                      <p className="setup-wizard-msg" data-testid="setup-battery-restarting">
                        EMS start opnieuw om je batterij te zoeken. Dit duurt even, laat dit scherm
                        open.
                      </p>
                    )}
                    {wizardPhase === "manual_restart" && (
                      <p className="setup-wizard-msg" data-testid="setup-restart-manual-hint">
                        Herstart EMS handmatig met{" "}
                        <code>make restart</code> of <code>scripts/restart.sh</code>, en herlaad
                        daarna deze pagina.
                      </p>
                    )}
                    {wizardPhase === "success" && wizardMessage && (
                      <p className="setup-wizard-success" data-testid="setup-battery-success" role="status">
                        {wizardMessage}
                      </p>
                    )}
                    {wizardPhase === "error" && wizardMessage && (
                      <>
                        <p className="setup-wizard-error" data-testid="setup-battery-error" role="alert">
                          {wizardMessage}
                        </p>
                        <button
                          type="button"
                          className="btn secondary"
                          data-testid="setup-battery-retry"
                          onClick={retryBattery}
                        >
                          Opnieuw proberen
                        </button>
                      </>
                    )}
                    {(wizardPhase === "form" || pollTimedOut) && (
                      <>
                        <label className="setup-field-label" htmlFor="setup-battery-ip">
                          IP-adres batterij (privé IPv4)
                        </label>
                        <input
                          id="setup-battery-ip"
                          className="setup-field-input"
                          data-testid="setup-battery-ip"
                          type="text"
                          autoComplete="off"
                          value={batteryIp}
                          onChange={(e) => setBatteryIp(e.target.value)}
                          placeholder="192.168.1.50"
                        />
                        {showP1 && (
                          <>
                            <label className="setup-field-label" htmlFor="setup-p1-ip">
                              IP-adres P1-meter (privé IPv4)
                            </label>
                            <input
                              id="setup-p1-ip"
                              className="setup-field-input"
                              data-testid="setup-p1-ip"
                              type="text"
                              autoComplete="off"
                              value={p1Ip}
                              onChange={(e) => setP1Ip(e.target.value)}
                              placeholder="192.168.1.10"
                            />
                          </>
                        )}
                        {clientHint && (
                          <p className="setup-wizard-hint" role="alert">{clientHint}</p>
                        )}
                        {saveError && (
                          <p className="setup-wizard-error" role="alert">{saveError}</p>
                        )}
                        {restartBusyMsg && (
                          <p className="setup-wizard-hint" role="status">{restartBusyMsg}</p>
                        )}
                        {pollTimedOut && (
                          <button
                            type="button"
                            className="btn secondary"
                            data-testid="setup-manual-reload"
                            onClick={() => window.location.reload()}
                          >
                            Pagina herladen
                          </button>
                        )}
                        <button
                          type="button"
                          className="btn primary setup-battery-save"
                          data-testid="setup-battery-save"
                          onClick={() => void saveAndTest()}
                          disabled={wizardPhase === "restarting"}
                        >
                          Opslaan en testen
                        </button>
                      </>
                    )}
                  </div>
                ) : (
                  <button
                    type="button"
                    className="btn secondary setup-checklist-btn"
                    data-testid={`setup-btn-${item.key}`}
                    onClick={() => {
                      if (item.key === "battery") openBatteryWizard();
                      else if (item.key === "p1") openP1Wizard();
                      else onOpenSettings(item.settings_section);
                    }}
                  >
                    {item.button_label}
                  </button>
                )}
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  );
}
