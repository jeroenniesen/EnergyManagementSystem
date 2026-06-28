// The cluster at a glance: capacity-weighted average + each Indevolt tower's SoC/role/capacity.
export type Tower = {
  ip: string;
  role: string | null;
  soc_pct: number | null;
  power_w: number;
  capacity_kwh: number | null;
  online: boolean;
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

function lastOctet(ip: string): string {
  const parts = ip.split(".");
  return parts.length === 4 ? `…${parts[3]}` : ip;
}

export function BatteryChips({ battery }: { battery: Battery | null }) {
  if (!battery) return null;
  const agg = battery.aggregate;
  if (!agg && battery.towers.length === 0) return null;
  return (
    <div className="tower-chips" data-testid="tower-chips">
      {agg && (
        <span className="tower-chip tower-chip-agg" data-testid="tower-chip-aggregate">
          <span className="tower-chip-soc">{agg.soc_pct.toFixed(0)}%</span>
          <span className="tower-chip-meta">
            cluster avg · {agg.online_towers}/{agg.total_towers} online
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
            {t.online && t.soc_pct != null ? `${t.soc_pct.toFixed(0)}%` : "—"}
          </span>
          <span className="tower-chip-meta">
            {lastOctet(t.ip)} {t.role ?? ""}
            {t.capacity_kwh != null ? ` · ${t.capacity_kwh.toFixed(2)} kWh` : ""}
            {t.online ? "" : " · offline"}
          </span>
        </span>
      ))}
    </div>
  );
}
