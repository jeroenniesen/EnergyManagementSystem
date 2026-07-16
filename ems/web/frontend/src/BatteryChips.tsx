// The cluster at a glance: capacity-weighted average + each Indevolt tower's SoC/role/capacity.
export type Tower = {
  ip: string;
  role: string | null;
  soc_pct: number | null;
  power_w: number;
  capacity_kwh: number | null;
  online: boolean;
  mode?: string | null; // actual working mode: self-consumption / standby / charging / …
};
export type BatteryAggregate = {
  soc_pct: number;
  power_w: number;
  capacity_kwh: number | null;
  online_towers: number;
  total_towers: number;
} | null;
export type Battery = {
  current_mode: string | null;
  capabilities: { services: string[]; p1_paired: boolean } | null;
  towers: Tower[];
  aggregate: BatteryAggregate;
};

/** Which metric the breakdown emphasises (the big number on each chip). "soc" = how full,
 *  "power" = how much is flowing in/out right now. The other metric shows in the meta line. */
export type BatteryMetric = "soc" | "power";

function lastOctet(ip: string): string {
  const parts = ip.split(".");
  return parts.length === 4 ? `…${parts[3]}` : ip;
}

function fmtPower(w: number): string {
  const a = Math.abs(w);
  return a >= 1000 ? `${(a / 1000).toFixed(2)} kW` : `${Math.round(a)} W`;
}

// Domain sign (SPEC §4.1): +discharge (out, powering the home) / −charge (in) / ~0 idle.
function powerLabel(w: number): string {
  if (w > 5) return `${fmtPower(w)} out`;
  if (w < -5) return `${fmtPower(w)} in`;
  return "idle";
}

function socLabel(soc: number | null, online: boolean): string {
  return online && soc != null ? `${soc.toFixed(0)}%` : "—";
}

export function BatteryChips({
  battery,
  metric = "soc",
}: {
  battery: Battery | null;
  metric?: BatteryMetric;
}) {
  if (!battery) return null;
  const agg = battery.aggregate;
  if (!agg && battery.towers.length === 0) return null;
  return (
    <div className="tower-chips" data-testid="tower-chips">
      {agg && (
        <span className="tower-chip tower-chip-agg" data-testid="tower-chip-aggregate">
          <span className="tower-chip-soc">
            {metric === "power" ? powerLabel(agg.power_w) : `${agg.soc_pct.toFixed(0)}%`}
          </span>
          <span className="tower-chip-meta">
            cluster avg · {agg.online_towers}/{agg.total_towers} online
            {metric === "power"
              ? ` · ${agg.soc_pct.toFixed(0)}%`
              : ` · ${powerLabel(agg.power_w)}`}
            {agg.capacity_kwh != null ? ` · ${agg.capacity_kwh.toFixed(1)} kWh` : ""}
          </span>
        </span>
      )}
      {battery.towers.map((t) => (
        <span
          key={t.ip}
          className={`tower-chip ${t.online ? "" : "tower-chip-off"}`}
          data-testid="tower-chip"
          title={`${t.ip}${t.role ? ` (${t.role})` : ""}`}
        >
          <span className="tower-chip-soc">
            {!t.online
              ? "—"
              : metric === "power"
                ? powerLabel(t.power_w)
                : socLabel(t.soc_pct, t.online)}
          </span>
          <span className="tower-chip-meta">
            {lastOctet(t.ip)} {t.role ?? ""}
            {t.online
              ? metric === "power"
                ? ` · ${socLabel(t.soc_pct, t.online)}`
                : ` · ${powerLabel(t.power_w)}`
              : ""}
            {t.capacity_kwh != null ? ` · ${t.capacity_kwh.toFixed(1)} kWh` : ""}
            {t.online ? "" : " · offline"}
          </span>
          {t.online && t.mode && (
            <span className="tower-chip-mode" data-testid="tower-chip-mode">
              {t.mode}
            </span>
          )}
        </span>
      ))}
    </div>
  );
}
