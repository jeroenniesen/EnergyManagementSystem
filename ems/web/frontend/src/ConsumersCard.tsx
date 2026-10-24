import { useEffect, useState } from "react";

import { apiFetch } from "./auth";

type ConsumerStatus = {
  state: string;
  state_label: string;
  power_w: number | null;
  energy_kwh: number | null;
  can_start: boolean;
  observed_at: string | null;
  detail: string | null;
};

type ConsumerDevice = {
  id: string;
  name: string;
  kind: string;
  integration: string;
  opted_in: boolean;
  status: ConsumerStatus | null;
};

type ConsumersResp = {
  title: string;
  summary: string;
  read_only: boolean;
  devices: ConsumerDevice[];
};

type StartWindow = {
  start: string;
  end: string;
  slots: number;
  avg_eur_per_kwh: number;
  est_cost_eur?: number;
  assumed_kwh?: number;
  reason: string;
};

type StartAdviceEntry = {
  device_id: string;
  device_name: string;
  kind: string;
  eligible: boolean;
  window: StartWindow | null;
  skip_reason: string | null;
  accepted?: boolean;
};

type StartAdviceResp = {
  title: string;
  enabled: boolean;
  dry_run: boolean;
  live_armed: boolean;
  summary: string;
  advice: StartAdviceEntry[];
  footnote: string;
};

const KIND_LABEL: Record<string, string> = {
  washer: "Washing machine",
  dryer: "Dryer",
  dishwasher: "Dishwasher",
  other: "Appliance",
  unknown: "Appliance",
};

function formatPower(w: number | null): string | null {
  if (w == null || !Number.isFinite(w)) return null;
  if (Math.abs(w) >= 1000) return `${(w / 1000).toFixed(1)} kW`;
  return `${Math.round(w)} W`;
}

function formatEnergy(kwh: number | null): string | null {
  if (kwh == null || !Number.isFinite(kwh)) return null;
  return `${kwh.toFixed(2)} kWh`;
}

/** Weekday + 24h clock — windows reach up to 24h ahead (same shape as CarCard). */
function formatWindowTime(iso: string): string {
  try {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso;
    const day = d.toLocaleDateString([], { weekday: "short" });
    const time = d.toLocaleTimeString([], {
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    });
    return `${day} ${time}`;
  } catch {
    return iso;
  }
}

function deviceAccepted(
  deviceId: string,
  serverFlag: boolean | undefined,
  overrides: Readonly<Record<string, boolean>>,
): boolean {
  if (Object.prototype.hasOwnProperty.call(overrides, deviceId)) return overrides[deviceId];
  return serverFlag === true;
}

export type ConsumersCardModel = {
  data: ConsumersResp;
  advice: StartAdviceResp | null;
  overrides: Readonly<Record<string, boolean>>;
  actionError: string | null;
  busyId: string | null;
  onAccept: (deviceId: string, window: StartWindow) => void;
  onCancel: (deviceId: string) => void;
};

/** Appliances insight + start-window advice (#224 / #227 / #334). */
export function ConsumersCardView({
  data,
  advice,
  overrides,
  actionError,
  busyId,
  onAccept,
  onCancel,
}: ConsumersCardModel) {

  const adviceById = new Map(
    (advice?.advice ?? []).map((a) => [a.device_id, a] as const),
  );

  return (
    <section className="consumers-card" data-testid="consumers-card">
      <h3 className="card-title flow-title">{data.title}</h3>
      <p className="consumers-summary" data-testid="consumers-summary">
        {data.summary}
      </p>
      {data.devices.length === 0 ? (
        <p className="advisor-hint" data-testid="consumers-empty">
          Insight only — EMS is not starting or stopping appliances yet.
        </p>
      ) : (
        <ul className="consumers-list">
          {data.devices.map((d) => {
            const st = d.status;
            const power = formatPower(st?.power_w ?? null);
            const energy = formatEnergy(st?.energy_kwh ?? null);
            const tip = adviceById.get(d.id);
            const win = tip?.window;
            const accepted = deviceAccepted(d.id, tip?.accepted, overrides);
            return (
              <li
                key={d.id}
                className="consumers-row"
                data-testid={`consumer-${d.id}`}
                data-state={st?.state ?? "unknown"}
              >
                <div className="consumers-row-head">
                  <span className="consumers-name">{d.name}</span>
                  <span className="consumers-kind">
                    {KIND_LABEL[d.kind] ?? KIND_LABEL.unknown}
                  </span>
                </div>
                <p className="consumers-state" data-testid={`consumer-${d.id}-state`}>
                  {st?.state_label ?? "Status unknown"}
                </p>
                {(power || energy) && (
                  <p className="consumers-metrics">
                    {power && <span data-testid={`consumer-${d.id}-power`}>{power}</span>}
                    {power && energy && " · "}
                    {energy && <span data-testid={`consumer-${d.id}-energy`}>{energy}</span>}
                  </p>
                )}
                {st?.detail && (
                  <p className="consumers-detail">{st.detail}</p>
                )}
                {win && (
                  <div
                    className="consumers-advice"
                    data-testid={`consumer-${d.id}-start-advice`}
                  >
                    <p className="consumers-advice-window">
                      Suggested start{" "}
                      <strong>{formatWindowTime(win.start)}</strong>
                      {" – "}
                      <strong>{formatWindowTime(win.end)}</strong>
                      {win.assumed_kwh != null && Number.isFinite(win.assumed_kwh) && (
                        <> · {win.assumed_kwh} kWh</>
                      )}
                      {win.est_cost_eur != null && (
                        <> · ≈€{win.est_cost_eur.toFixed(2)}</>
                      )}
                    </p>
                    <p className="consumers-advice-reason">{win.reason}</p>
                    {accepted ? (
                      <button
                        type="button"
                        className="btn-primary"
                        data-testid={`consumer-${d.id}-cancel-window`}
                        disabled={busyId === d.id}
                        onClick={() => onCancel(d.id)}
                      >
                        Annuleer
                      </button>
                    ) : (
                      <button
                        type="button"
                        className="btn-primary"
                        data-testid={`consumer-${d.id}-accept-window`}
                        disabled={busyId === d.id}
                        onClick={() => onAccept(d.id, win)}
                      >
                        Ik start hier
                      </button>
                    )}
                  </div>
                )}
                {!win && tip?.skip_reason && (
                  <p
                    className="consumers-advice-skip"
                    data-testid={`consumer-${d.id}-start-skip`}
                  >
                    {tip.skip_reason}
                  </p>
                )}
              </li>
            );
          })}
        </ul>
      )}
      {advice?.summary && data.devices.length > 0 && (
        <p className="consumers-advice-summary" data-testid="consumers-advice-summary">
          {advice.summary}
        </p>
      )}
      {actionError && (
        <p className="advisor-hint" role="alert" data-testid="consumers-action-error">
          {actionError}
        </p>
      )}
      <p className="consumers-footnote" data-testid="consumers-footnote">
        {advice?.footnote
          ?? "Read-only view of selected appliances. EMS does not control them from this page."}
      </p>
    </section>
  );
}

/** Appliances insight + start-window advice (#224 / #227 / #334). */
export function ConsumersCard() {
  const [data, setData] = useState<ConsumersResp | null>(null);
  const [advice, setAdvice] = useState<StartAdviceResp | null>(null);
  const [error, setError] = useState(false);
  const [overrides, setOverrides] = useState<Record<string, boolean>>({});
  const [busyId, setBusyId] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [rDevices, rAdvice] = await Promise.all([
          apiFetch("/api/consumers"),
          apiFetch("/api/consumers/start-advice"),
        ]);
        if (!rDevices.ok) throw new Error(`HTTP ${rDevices.status}`);
        const body = (await rDevices.json()) as ConsumersResp;
        let adviceBody: StartAdviceResp | null = null;
        if (rAdvice.ok) {
          adviceBody = (await rAdvice.json()) as StartAdviceResp;
        }
        if (!cancelled) {
          setData(body);
          setAdvice(adviceBody);
          setError(false);
        }
      } catch {
        if (!cancelled) setError(true);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  if (error) {
    return (
      <section className="consumers-card" data-testid="consumers-card">
        <h3 className="card-title flow-title">Appliances</h3>
        <p className="advisor-hint" data-testid="consumers-error">
          Couldn’t load appliances right now. Try again in a moment.
        </p>
      </section>
    );
  }
  if (!data) {
    return (
      <section className="consumers-card" data-testid="consumers-card" aria-busy="true">
        <h3 className="card-title flow-title">Appliances</h3>
        <p className="card-sub">Loading…</p>
      </section>
    );
  }

  async function acceptWindow(deviceId: string, window: StartWindow) {
    setBusyId(deviceId);
    setActionError(null);
    try {
      const res = await apiFetch("/api/consumers/advice/accept", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ device_id: deviceId, window }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setOverrides((prev) => ({ ...prev, [deviceId]: true }));
    } catch {
      setActionError("Couldn’t save that start window. Try again in a moment.");
    } finally {
      setBusyId(null);
    }
  }

  async function cancelWindow(deviceId: string) {
    setBusyId(deviceId);
    setActionError(null);
    try {
      const res = await apiFetch("/api/consumers/advice/accept", {
        method: "DELETE",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ device_id: deviceId }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setOverrides((prev) => ({ ...prev, [deviceId]: false }));
    } catch {
      setActionError("Couldn’t cancel that start window. Try again in a moment.");
    } finally {
      setBusyId(null);
    }
  }

  return (
    <ConsumersCardView
      data={data}
      advice={advice}
      overrides={overrides}
      actionError={actionError}
      busyId={busyId}
      onAccept={(deviceId, window) => {
        void acceptWindow(deviceId, window);
      }}
      onCancel={(deviceId) => {
        void cancelWindow(deviceId);
      }}
    />
  );
}
