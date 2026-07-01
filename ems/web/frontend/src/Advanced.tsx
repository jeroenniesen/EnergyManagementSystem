// A collapsible "Advanced" section for the technical detail the dashboard doesn't need to shout —
// the power breakdown, the Sankey, the charge target, the controller decision, the AI note and the
// data-status chips. Default collapsed so the home stays calm (scores + plan first); children only
// mount when opened, so their fetches don't run until asked for.
import { type ReactNode, useState } from "react";

export function Advanced({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState(false);
  return (
    <section className="advanced" data-testid="advanced">
      <button
        type="button"
        className={`advanced-toggle${open ? " open" : ""}`}
        data-testid="advanced-toggle"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
      >
        <span className="advanced-chevron" aria-hidden="true">›</span>
        <span>{open ? "Hide details" : "All the details"}</span>
      </button>
      {open && (
        <div className="advanced-body" data-testid="advanced-body">
          {children}
        </div>
      )}
    </section>
  );
}
