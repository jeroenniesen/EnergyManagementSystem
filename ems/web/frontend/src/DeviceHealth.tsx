import {
  CONSUMER_SOURCE_LABEL,
  CONSUMER_SOURCES,
  type ConsumerSource,
  type DeviceHealth,
  type DeviceHealthSummary,
  FRESHNESS_STATE,
  summarizeDeviceHealth,
} from "./labels";

type Props = {
  freshness: Record<string, string> | null;
  deviceHealth: DeviceHealth | null;
  isDemo: boolean;
  /** Warning/critical alerts for the selected source — the three #73 answers. */
  alertsForSource?: (key: string) => {
    message?: string;
    ems_doing?: string;
    action?: string;
  } | null;
};

function hhmmFromIso(iso: string | null | undefined): string | null {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}

function formatAge(ageSeconds: number | null | undefined): string | null {
  if (ageSeconds == null || !Number.isFinite(ageSeconds)) return null;
  if (ageSeconds < 90) return "zojuist";
  if (ageSeconds < 3600) return `${Math.round(ageSeconds / 60)} min`;
  if (ageSeconds < 86400) return `${Math.round(ageSeconds / 3600)} u`;
  return `${Math.round(ageSeconds / 86400)} d`;
}

/** B-94: healthy/ok → one quiet line (no four-source card above the fold). */
export function isDeviceHealthQuiet(summary: DeviceHealthSummary): boolean {
  return summary.severity === "ok";
}

type SourceListProps = {
  freshness: Record<string, string> | null;
  deviceHealth: DeviceHealth | null;
  alertsForSource?: Props["alertsForSource"];
};

function DeviceHealthSources({ freshness, deviceHealth, alertsForSource }: SourceListProps) {
  const byKey = new Map((deviceHealth?.sources ?? []).map((s) => [s.key, s]));
  return (
    <ul className="device-health-sources" data-testid="device-health-sources">
      {CONSUMER_SOURCES.map((key) => {
        const api = byKey.get(key);
        // Prefer API state so battery_reachable=false (downgraded to missing) wins over a
        // stale freshness map that still says "fresh".
        const state = api?.state ?? freshness?.[key] ?? "missing";
        const hhmm = api?.updated_hhmm ?? hhmmFromIso(api?.updated_at ?? null);
        const note = api?.note;
        const alert =
          state !== "fresh" || deviceHealth?.battery_reachable === false
            ? alertsForSource?.(key)
            : null;
        const label = api?.label ?? CONSUMER_SOURCE_LABEL[key as ConsumerSource] ?? key;
        return (
          <li
            key={key}
            className={`device-health-source state-${state}`}
            data-testid={`device-health-${key}`}
            data-state={state}
          >
            <span className={`dh-dot dh-${state}`} aria-hidden="true" />
            <span className="dh-label">{label}</span>
            <span className="dh-state">{FRESHNESS_STATE[state] ?? state}</span>
            {hhmm && (
              <span className="dh-updated" data-testid={`device-health-${key}-updated`}>
                bijgewerkt om {hhmm}
              </span>
            )}
            {note && <span className="dh-note">{note}</span>}
            {alert && (alert.message || alert.ems_doing || alert.action) && (
              <div className="dh-alert-answers" data-testid={`device-health-${key}-answers`}>
                {alert.message && <span className="dh-answer-what">{alert.message}</span>}
                {alert.ems_doing && (
                  <span className="dh-answer-ems">{alert.ems_doing}</span>
                )}
                {alert.action && <span className="dh-answer-action">→ {alert.action}</span>}
              </div>
            )}
          </li>
        );
      })}
    </ul>
  );
}

/**
 * Compact per-source freshness strip (issue #79).
 * B-94: when overall health is ok, collapse to one quiet disclosure line — sources stay
 * one tap away and full detail remains under Manage → System / Advanced.
 */
export function DeviceHealthStrip({
  freshness,
  deviceHealth,
  isDemo,
  alertsForSource,
}: Props) {
  const summary = summarizeDeviceHealth(freshness, deviceHealth, isDemo);
  const quiet = isDeviceHealthQuiet(summary);

  const extras = (
    <>
      {deviceHealth?.battery_reachable === false && (
        <span className="device-health-reachable" data-testid="battery-reachable">
          Batterij niet bereikbaar
        </span>
      )}
      {deviceHealth?.forecast_age_seconds != null &&
        deviceHealth.forecast_age_seconds > 3600 && (
          <span className="device-health-forecast-age" data-testid="forecast-age">
            Voorspelling {formatAge(deviceHealth.forecast_age_seconds)} oud
          </span>
        )}
    </>
  );

  if (quiet) {
    return (
      <section
        className="device-health device-health--quiet"
        data-testid="device-health"
        data-density-kind="device-health"
        data-quiet="true"
      >
        <details className="device-health-quiet" data-testid="device-health-quiet">
          <summary
            className={`device-health-summary severity-${summary.severity}`}
            data-testid="device-health-summary"
            data-badge={summary.badge}
            title={summary.detail || "Per-bron status — tik om te tonen"}
          >
            <span className="device-health-summary-label">{summary.label}</span>
          </summary>
          {summary.detail && (
            <span className="device-health-summary-detail">{summary.detail}</span>
          )}
          {extras}
          <DeviceHealthSources
            freshness={freshness}
            deviceHealth={deviceHealth}
            alertsForSource={alertsForSource}
          />
        </details>
      </section>
    );
  }

  return (
    <section
      className="device-health"
      data-testid="device-health"
      data-density-kind="device-health"
      data-quiet="false"
    >
      <div
        className={`device-health-summary severity-${summary.severity}`}
        data-testid="device-health-summary"
        data-badge={summary.badge}
        title={summary.detail}
      >
        <span className="device-health-summary-label">{summary.label}</span>
        {summary.detail && (
          <span className="device-health-summary-detail">{summary.detail}</span>
        )}
        {extras}
      </div>
      <DeviceHealthSources
        freshness={freshness}
        deviceHealth={deviceHealth}
        alertsForSource={alertsForSource}
      />
    </section>
  );
}
