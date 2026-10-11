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

/** Read-only appliances insight for Insights (#224 / epic #222). No start/stop. */
export function ConsumersCard() {
  const [data, setData] = useState<ConsumersResp | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const r = await apiFetch("/api/consumers");
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const body = (await r.json()) as ConsumersResp;
        if (!cancelled) {
          setData(body);
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
              </li>
            );
          })}
        </ul>
      )}
      <p className="consumers-footnote">
        Read-only view of selected appliances. EMS does not control them from this page.
      </p>
    </section>
  );
}
