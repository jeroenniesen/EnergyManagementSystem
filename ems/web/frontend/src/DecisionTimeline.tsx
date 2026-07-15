// Recent-decisions timeline (2026-07-15 plan). A compact, keyboard-accessible list of what EMS
// did or chose not to do; each row opens the decision drawer (`#dashboard/decision/<id>`) with the
// full what-happened / why / consequence / action. Data comes from /api/decisions (homeowner copy
// already; this component only lays it out).
export type DecisionEvent = {
  id: string;
  time: string;
  title: string;
  reason: string;
  consequence: string;
  action: string;
  severity: string;
};

function fmtTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleString(undefined, {
    weekday: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function DecisionTimeline({
  events,
  onOpen,
}: {
  events: DecisionEvent[];
  onOpen: (id: string) => void;
}) {
  if (events.length === 0) return null;
  return (
    <section className="decision-timeline" data-testid="decision-timeline">
      <h3 className="decision-timeline-title">Recent decisions</h3>
      <ul className="decision-list">
        {events.map((e) => (
          <li key={e.id}>
            <button
              type="button"
              className={`decision-item decision-sev-${e.severity}`}
              data-testid={`decision-item-${e.id}`}
              data-severity={e.severity}
              onClick={() => onOpen(e.id)}
            >
              <span className="decision-item-title">{e.title}</span>
              <span className="decision-item-time">{fmtTime(e.time)}</span>
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}
