import { useEffect, useState } from "react";

import { apiFetch } from "./auth";

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

/** Onboarding checklist (#290): read-only; links into Settings sections. */
export function Setup({ onOpenSettings }: Props) {
  const [data, setData] = useState<Checklist | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    apiFetch("/api/setup/checklist")
      .then(async (r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json() as Promise<Checklist>;
      })
      .then((body) => {
        if (alive) setData(body);
      })
      .catch(() => {
        if (alive) setError("Kon de checklist niet laden. Probeer het later opnieuw.");
      });
    return () => {
      alive = false;
    };
  }, []);

  return (
    <section className="setup-checklist" data-testid="setup-checklist">
      <h2 className="setup-checklist-title">Inrichten</h2>
      <p className="setup-checklist-lead">
        Zo zie je wat EMS nog nodig heeft voordat het veilig mee kan kijken. EMS blijft in
        kijkmodus tot je later zelf live sturen inschakelt.
      </p>
      {error && (
        <p className="setup-checklist-error" role="alert" data-testid="setup-checklist-error">
          {error}
        </p>
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
                <button
                  type="button"
                  className="btn secondary setup-checklist-btn"
                  data-testid={`setup-btn-${item.key}`}
                  onClick={() => onOpenSettings(item.settings_section)}
                >
                  {item.button_label}
                </button>
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  );
}
