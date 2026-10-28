import { useCallback, useEffect, useRef, useState, type MutableRefObject } from "react";

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
type ReserveField = {
  key: string;
  default: number;
  min: number | null;
  max: number | null;
  step: number | null;
  unit: string;
};
export const RESERVE_EXPLANATION =
  "Zoveel houdt de batterij altijd achter voor 's nachts of bij storing.";

export function reserveFieldFromSettings(body: {
  schema: ReserveField[];
  values?: Record<string, unknown>;
}): { field: ReserveField; value: string } {
  const field = body.schema.find((candidate) => candidate.key === "battery.min_reserve_soc");
  if (!field || typeof field.default !== "number") throw new Error("reserve schema missing");
  const current = body.values?.[field.key];
  return { field, value: String(typeof current === "number" ? current : field.default) };
}

export function ReserveInput({
  field, value, onChange,
}: {
  field: ReserveField;
  value: string;
  onChange: (value: string) => void;
}) {
  return (
    <div>
      <input
        id="setup-reserve-value"
        className="setup-field-input"
        type="number"
        min={field.min ?? undefined}
        max={field.max ?? undefined}
        step={field.step ?? undefined}
        value={value}
        onChange={(e) => onChange(e.target.value)}
      />
      <span>{field.unit}</span>
    </div>
  );
}

type Props = {
  onOpenSettings: (section: string) => void;
};

const BATTERY_RESTART_MESSAGE =
  "EMS start opnieuw om je batterij te zoeken. Dit duurt even, laat dit scherm open.";
const PRICES_RESTART_MESSAGE =
  "EMS start opnieuw om je dynamische prijzen op te halen. Dit duurt even, laat dit scherm open.";

type WizardRestartHandle = {
  requestedRef: MutableRefObject<boolean>;
  setPhase: (phase: BatteryWizardPhase) => void;
  setPollTimedOut: (timedOut: boolean) => void;
  setBusyMsg: (msg: string | null) => void;
  setSaveError: (msg: string | null) => void;
};

function WizardRestartStatus({
  phase,
  restartingMessage,
  restartingTestId,
  manualTestId,
}: {
  phase: BatteryWizardPhase;
  restartingMessage: string;
  restartingTestId: string;
  manualTestId: string;
}) {
  return (
    <>
      {phase === "restarting" && (
        <p className="setup-wizard-msg" data-testid={restartingTestId}>
          {restartingMessage}
        </p>
      )}
      {phase === "manual_restart" && (
        <p className="setup-wizard-msg" data-testid={manualTestId}>
          Herstart EMS handmatig met{" "}
          <code>make restart</code> of <code>scripts/restart.sh</code>, en herlaad daarna deze
          pagina.
        </p>
      )}
    </>
  );
}

function WizardFormRestartExtras({
  pollTimedOut,
  busyMsg,
  reloadTestId,
}: {
  pollTimedOut: boolean;
  busyMsg: string | null;
  reloadTestId: string;
}) {
  return (
    <>
      {busyMsg && <p className="setup-wizard-hint" role="status">{busyMsg}</p>}
      {pollTimedOut && (
        <button
          type="button"
          className="btn secondary"
          data-testid={reloadTestId}
          onClick={() => window.location.reload()}
        >
          Pagina herladen
        </button>
      )}
    </>
  );
}

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
  const [reserveOpen, setReserveOpen] = useState(false);
  const [reserveField, setReserveField] = useState<ReserveField | null>(null);
  const [reserveValue, setReserveValue] = useState("");
  const [reserveError, setReserveError] = useState<string | null>(null);
  const [reserveMessage, setReserveMessage] = useState<string | null>(null);
  const [reserveSaving, setReserveSaving] = useState(false);
  const [pricesOpen, setPricesOpen] = useState(false);
  const [tibberToken, setTibberToken] = useState("");
  const [pricesPhase, setPricesPhase] = useState<BatteryWizardPhase>("form");
  const [pricesMessage, setPricesMessage] = useState<string | null>(null);
  const [pricesError, setPricesError] = useState<string | null>(null);
  const [pricesSaving, setPricesSaving] = useState(false);
  const [pricesPollTimedOut, setPricesPollTimedOut] = useState(false);
  const [pricesRestartBusyMsg, setPricesRestartBusyMsg] = useState<string | null>(null);
  const [restartAvailable, setRestartAvailable] = useState<RestartAvailability | null>(null);
  const [restartBusyMsg, setRestartBusyMsg] = useState<string | null>(null);
  const [pollTimedOut, setPollTimedOut] = useState(false);
  const restartAliveRef = useRef(true);
  const restartRequestedRef = useRef(false);
  const p1RestartRequestedRef = useRef(false);
  const pricesRestartRequestedRef = useRef(false);

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

  async function loadReserveField() {
    try {
      const r = await apiFetch("/api/settings");
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const body = await r.json();
      const { field, value } = reserveFieldFromSettings(body);
      setReserveField(field);
      setReserveValue(value);
      setReserveError(null);
    } catch {
      setReserveError("Kon de batterijreserve niet laden. Probeer het opnieuw.");
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
    if (step === "prices") {
      setPricesOpen(true);
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
    const savedIp = readPersistedP1Ip();
    if (!savedIp.trim()) return;
    const p1 = data.items.find((i) => i.key === "p1");
    const battery = data.items.find((i) => i.key === "battery");
    const outcome = p1ChecklistOutcome(p1?.status, battery?.status, savedIp);
    if (outcome) {
      setP1WizardPhase(outcome.phase);
      setP1WizardMessage(outcome.message);
      setP1LinkBattery(Boolean(outcome.linkBattery));
    }
  }, [p1Open, data]);

  useEffect(() => {
    if (!pricesOpen || !data || readPersistedSetupStep() !== "prices") return;
    const prices = data.items.find((i) => i.key === "prices");
    if (prices?.status === "Werkt") {
      setPricesPhase("success");
      setPricesMessage("Je dynamische prijzen komen binnen.");
      clearSetupPersistence();
    } else {
      setPricesPhase("error");
      setPricesMessage("Er komen nog geen live prijzen binnen. Controleer je Tibber-token en probeer het opnieuw.");
    }
  }, [pricesOpen, data]);

  const batteryRestartHandle: WizardRestartHandle = {
    requestedRef: restartRequestedRef,
    setPhase: setWizardPhase,
    setPollTimedOut: setPollTimedOut,
    setBusyMsg: setRestartBusyMsg,
    setSaveError,
  };

  const p1RestartHandle: WizardRestartHandle = {
    requestedRef: p1RestartRequestedRef,
    setPhase: setP1WizardPhase,
    setPollTimedOut: setP1PollTimedOut,
    setBusyMsg: setP1RestartBusyMsg,
    setSaveError: setP1SaveError,
  };

  const pricesRestartHandle: WizardRestartHandle = {
    requestedRef: pricesRestartRequestedRef,
    setPhase: setPricesPhase,
    setPollTimedOut: setPricesPollTimedOut,
    setBusyMsg: setPricesRestartBusyMsg,
    setSaveError: setPricesError,
  };

  function openReserveWizard() {
    setReserveOpen(true);
    setReserveMessage(null);
    void loadReserveField();
  }

  async function saveReserve() {
    if (!reserveField || reserveSaving) return;
    setReserveSaving(true);
    setReserveError(null);
    setReserveMessage(null);
    try {
      const r = await apiFetch("/api/setup/settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ "battery.min_reserve_soc": Number(reserveValue) }),
      });
      const body = await r.json().catch(() => ({}));
      if (!r.ok) {
        setReserveError(String(body.errors?.[reserveField.key] ?? body.detail ?? `HTTP ${r.status}`));
      } else {
        setReserveMessage("Je batterijreserve is opgeslagen en meteen actief.");
      }
    } catch {
      setReserveError("Opslaan is niet gelukt. Probeer het opnieuw.");
    } finally {
      setReserveSaving(false);
    }
  }

  function openPricesWizard() {
    setPricesOpen(true);
    setPricesPhase("form");
    setPricesMessage(null);
    setPricesError(null);
    setPricesRestartBusyMsg(null);
    setPricesPollTimedOut(false);
    pricesRestartRequestedRef.current = false;
    void refreshRestartCapability();
  }

  function retryPrices() {
    setPricesPhase("form");
    setPricesMessage(null);
    setPricesError(null);
    setPricesRestartBusyMsg(null);
    setPricesPollTimedOut(false);
    pricesRestartRequestedRef.current = false;
    persistSetupStep(null);
  }

  async function savePrices() {
    if (pricesSaving || pricesPhase === "restarting") return;
    setPricesSaving(true);
    setPricesError(null);
    try {
      const r = await apiFetch("/api/setup/settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          "prices.tibber_token": tibberToken,
          "connection.use_live_prices": true,
        }),
      });
      const body = await r.json().catch(() => ({}));
      if (!r.ok) {
        setPricesError(String(body.errors?.["prices.tibber_token"] ?? body.detail ?? `HTTP ${r.status}`));
        return;
      }
      setTibberToken("");
      persistSetupStep("prices");
      if (body.restart_required) {
        if (restartAvailable?.available === false) {
          setPricesPhase("manual_restart");
        } else {
          setPricesPhase("restarting");
          await requestRestartOnce(pricesRestartHandle);
        }
      } else {
        setData(await loadChecklist());
      }
    } catch {
      setPricesError("Opslaan is niet gelukt. Probeer het opnieuw.");
    } finally {
      setPricesSaving(false);
    }
  }

  async function pollForRestartBoot(initialBootId: string, handle: WizardRestartHandle) {
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
      handle.setPollTimedOut(true);
      handle.setPhase("form");
    }
  }

  async function requestRestartOnce(handle: WizardRestartHandle) {
    if (handle.requestedRef.current) return;
    handle.requestedRef.current = true;
    handle.setBusyMsg(null);
    handle.setPollTimedOut(false);
    try {
      const r = await apiFetch("/api/system/restart", { method: "POST" });
      const b = (await r.json().catch(() => ({}))) as Record<string, unknown>;
      const dispatch = interpretRestartResponse(r.status, b);
      if (dispatch.kind === "started") {
        handle.setPhase("restarting");
        void pollForRestartBoot(dispatch.bootId, handle);
        return;
      }
      if (dispatch.kind === "busy" || dispatch.kind === "not_supervised") {
        handle.requestedRef.current = false;
        if (dispatch.kind === "not_supervised") {
          setRestartAvailable({ available: false, reason: "not_supervised" });
          handle.setPhase("manual_restart");
        } else {
          handle.setBusyMsg("Het systeem is bezig — probeer het over enkele seconden opnieuw.");
          handle.setPhase("form");
        }
        return;
      }
      throw new Error(dispatch.message);
    } catch (e) {
      handle.requestedRef.current = false;
      handle.setSaveError(e instanceof Error ? e.message : String(e));
      handle.setPhase("form");
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
        await requestRestartOnce(batteryRestartHandle);
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
    await requestRestartOnce(p1RestartHandle);
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
                    <WizardRestartStatus
                      phase={p1WizardPhase}
                      restartingMessage={P1_RESTART_MESSAGE}
                      restartingTestId="setup-p1-restarting"
                      manualTestId="setup-p1-restart-manual-hint"
                    />
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
                        <WizardFormRestartExtras
                          pollTimedOut={p1PollTimedOut}
                          busyMsg={p1RestartBusyMsg}
                          reloadTestId="setup-p1-manual-reload"
                        />
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
                ) : item.key === "prices" && pricesOpen ? (
                  <div className="setup-prices-wizard" data-testid="setup-prices-wizard">
                    <WizardRestartStatus
                      phase={pricesPhase}
                      restartingMessage={PRICES_RESTART_MESSAGE}
                      restartingTestId="setup-prices-restarting"
                      manualTestId="setup-prices-restart-manual-hint"
                    />
                    {pricesPhase === "success" && pricesMessage && (
                      <p className="setup-wizard-success" role="status">{pricesMessage}</p>
                    )}
                    {pricesPhase === "error" && pricesMessage && (
                      <>
                        <p className="setup-wizard-error" role="alert">{pricesMessage}</p>
                        <button type="button" className="btn secondary" onClick={retryPrices}>
                          Opnieuw proberen
                        </button>
                      </>
                    )}
                    {(pricesPhase === "form" || pricesPollTimedOut) && (
                      <>
                        <label className="setup-field-label" htmlFor="setup-tibber-token">
                          Tibber-token
                        </label>
                        <p className="setup-wizard-hint">
                          Vul je toegangscode van Tibber in om je actuele stroomprijzen op te halen.
                          Laat het veld leeg om je eerder opgeslagen code te behouden.
                        </p>
                        <input
                          id="setup-tibber-token"
                          className="setup-field-input"
                          type="password"
                          autoComplete="off"
                          value={tibberToken}
                          onChange={(e) => setTibberToken(e.target.value)}
                        />
                        {pricesError && <p className="setup-wizard-error" role="alert">{pricesError}</p>}
                        <WizardFormRestartExtras
                          pollTimedOut={pricesPollTimedOut}
                          busyMsg={pricesRestartBusyMsg}
                          reloadTestId="setup-prices-manual-reload"
                        />
                        <button
                          type="button"
                          className="btn primary"
                          onClick={() => void savePrices()}
                          disabled={pricesSaving}
                        >
                          Opslaan en testen
                        </button>
                      </>
                    )}
                  </div>
                ) : item.key === "battery" && batteryOpen ? (
                  <div className="setup-battery-wizard" data-testid="setup-battery-wizard">
                    <WizardRestartStatus
                      phase={wizardPhase}
                      restartingMessage={BATTERY_RESTART_MESSAGE}
                      restartingTestId="setup-battery-restarting"
                      manualTestId="setup-restart-manual-hint"
                    />
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
                        <WizardFormRestartExtras
                          pollTimedOut={pollTimedOut}
                          busyMsg={restartBusyMsg}
                          reloadTestId="setup-manual-reload"
                        />
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
                      else if (item.key === "prices") openPricesWizard();
                      else onOpenSettings(item.settings_section);
                    }}
                  >
                    {item.button_label}
                  </button>
                )}
              </li>
            ))}
            <li className="setup-checklist-item" data-testid="setup-item-reserve">
              <div className="setup-checklist-row">
                <span className="setup-checklist-status setup-status-opt">Instellen</span>
                <p className="setup-checklist-sentence">Bepaal hoeveel stroom je batterij achterhoudt.</p>
              </div>
              {reserveOpen ? (
                <div className="setup-reserve-wizard" data-testid="setup-reserve-wizard">
                  <label className="setup-field-label" htmlFor="setup-reserve-value">
                    Batterijreserve
                  </label>
                  <p className="setup-wizard-hint">
                    {RESERVE_EXPLANATION}
                  </p>
                  {reserveField && (
                    <ReserveInput field={reserveField} value={reserveValue} onChange={setReserveValue} />
                  )}
                  {reserveError && <p className="setup-wizard-error" role="alert">{reserveError}</p>}
                  {reserveMessage && <p className="setup-wizard-success" role="status">{reserveMessage}</p>}
                  <button
                    type="button"
                    className="btn primary"
                    onClick={() => void saveReserve()}
                    disabled={!reserveField || reserveSaving || reserveValue.trim() === ""}
                  >
                    Reserve opslaan
                  </button>
                </div>
              ) : (
                <button type="button" className="btn secondary setup-checklist-btn" onClick={openReserveWizard}>
                  Reserve instellen
                </button>
              )}
            </li>
          </ul>
        </>
      )}
    </section>
  );
}
