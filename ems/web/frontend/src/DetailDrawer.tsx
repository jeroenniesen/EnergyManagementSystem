// Contextual dashboard drawer (2026-07-15 plan). One reusable shell for every "tell me more"
// surface on the dashboard: a right-side panel on desktop, a full-width sheet on mobile (CSS-only —
// the component never branches on viewport width). Accessible dialog: role="dialog",
// aria-modal, a labelled heading, Escape to close, focus moved to the close button on open and
// RESTORED to the trigger on close, and a body-scroll lock while open. The full focus trap +
// reduced-motion polish lands in Task 6; this is the shell.
import { useEffect, useRef } from "react";

export type DetailDrawerProps = {
  open: boolean;
  title: string;
  eyebrow?: string;
  onClose: () => void;
  children: React.ReactNode;
  testId?: string;
};

export function DetailDrawer({
  open,
  title,
  eyebrow,
  onClose,
  children,
  testId = "detail-drawer",
}: DetailDrawerProps) {
  const closeRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  // The element that had focus when the drawer opened, so it can be restored on close.
  const restoreRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (!open) return;
    restoreRef.current = (document.activeElement as HTMLElement) ?? null;
    closeRef.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        onClose();
        return;
      }
      // Focus trap: Tab must cycle within the dialog, never escaping to the page behind it.
      if (e.key === "Tab") {
        const panel = panelRef.current;
        if (!panel) return;
        const focusables = Array.from(
          panel.querySelectorAll<HTMLElement>(
            'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), ' +
              'textarea:not([disabled]), summary, [tabindex]:not([tabindex="-1"])',
          ),
        ).filter((el) => el.offsetParent !== null || el === document.activeElement);
        if (focusables.length === 0) return;
        const first = focusables[0];
        const last = focusables[focusables.length - 1];
        const active = document.activeElement;
        if (e.shiftKey && active === first) {
          e.preventDefault();
          last.focus();
        } else if (!e.shiftKey && active === last) {
          e.preventDefault();
          first.focus();
        }
      }
    };
    document.addEventListener("keydown", onKey);
    document.body.classList.add("drawer-open"); // body-scroll lock
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.classList.remove("drawer-open");
      // Return focus to whatever opened the drawer (the trigger), for keyboard users.
      restoreRef.current?.focus?.();
    };
  }, [open, onClose]);

  if (!open) return null;
  const labelId = `${testId}-title`;
  return (
    <div className="drawer-backdrop" onClick={onClose} data-testid={`${testId}-backdrop`}>
      <div
        ref={panelRef}
        className="drawer-panel"
        role="dialog"
        aria-modal="true"
        aria-labelledby={labelId}
        onClick={(e) => e.stopPropagation()}
        data-testid={testId}
      >
        <div className="drawer-head">
          <div className="drawer-titles">
            {eyebrow ? (
              <span className="drawer-eyebrow" data-testid={`${testId}-eyebrow`}>
                {eyebrow}
              </span>
            ) : null}
            <h2 className="drawer-title" id={labelId} data-testid={`${testId}-heading`}>
              {title}
            </h2>
          </div>
          <button
            ref={closeRef}
            type="button"
            className="drawer-close"
            onClick={onClose}
            aria-label="Close"
            data-testid={`${testId}-close`}
          >
            ×
          </button>
        </div>
        <div className="drawer-body" data-testid={`${testId}-body`}>
          {children}
        </div>
      </div>
    </div>
  );
}
