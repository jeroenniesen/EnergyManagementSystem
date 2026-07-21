import { useEffect, useRef, useState } from "react";

import { apiFetch } from "./auth";
import { Icon } from "./icons";

// B-20: the header bell — an in-app surface for the notification outbox (GET /api/notifications).
// Best-effort, like the other dashboard polls: a failed fetch just leaves the last-known state.
export type NotificationItem = {
  id: number;
  ts: string;
  key: string;
  title: string;
  body: string;
  confidence: string | null;
  read: boolean;
  delivered: string[];
  dedupe_key: string | null;
};
type NotificationsResp = { items: NotificationItem[]; unread: number };

const POLL_MS = 60000;
const FEED_LIMIT = 10;

function relativeTime(iso: string): string {
  const diffSec = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 1000));
  if (diffSec < 60) return "just now";
  const diffMin = Math.round(diffSec / 60);
  if (diffMin < 60) return `${diffMin}m ago`;
  const diffHr = Math.round(diffMin / 60);
  if (diffHr < 24) return `${diffHr}h ago`;
  return `${Math.round(diffHr / 24)}d ago`;
}

export function NotificationBell({ canOperate = true }: { canOperate?: boolean } = {}) {
  const [items, setItems] = useState<NotificationItem[]>([]);
  const [unread, setUnread] = useState(0);
  const [open, setOpen] = useState(false);
  const wrapRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let alive = true;
    function poll() {
      apiFetch(`/api/notifications?limit=${FEED_LIMIT}`)
        .then((r) => (r.ok ? r.json() : null))
        .then((b: NotificationsResp | null) => {
          if (alive && b) {
            setItems(b.items);
            setUnread(b.unread);
          }
        })
        .catch(() => {
          /* best-effort — keep the last-known state */
        });
    }
    poll();
    const id = setInterval(poll, POLL_MS);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, []);

  // Esc closes; a click outside the bell+panel closes.
  useEffect(() => {
    if (!open) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false);
    }
    function onClick(e: MouseEvent) {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("keydown", onKey);
    document.addEventListener("mousedown", onClick);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("mousedown", onClick);
    };
  }, [open]);

  async function markAllRead() {
    // Optimistic: the dot clears immediately; a failed POST just leaves it stale until next poll.
    setItems((prev) => prev.map((n) => ({ ...n, read: true })));
    setUnread(0);
    try {
      await apiFetch("/api/notifications/read", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ all: true }),
      });
    } catch {
      /* best-effort — the unread count resyncs on the next poll */
    }
  }

  return (
    <div className="notif-bell-wrap" ref={wrapRef}>
      <button
        type="button"
        className="notif-bell"
        data-testid="notif-bell"
        aria-label={unread > 0 ? `Notifications — ${unread} unread` : "Notifications"}
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
      >
        <Icon name="bell" />
        {unread > 0 && <span className="notif-dot" data-testid="notif-unread-dot" />}
      </button>
      {open && (
        <div
          className="notif-panel"
          role="dialog"
          aria-label="Notifications"
          data-testid="notif-panel"
        >
          <div className="notif-panel-head">
            <span className="metric-label">Notifications</span>
            {canOperate && (
              <button
                type="button"
                className="btn-ghost notif-mark-all"
                data-testid="notif-mark-all-read"
                disabled={unread === 0}
                onClick={markAllRead}
              >
                Mark all read
              </button>
            )}
          </div>
          {items.length === 0 ? (
            <p className="notif-empty" data-testid="notif-empty">No notifications yet.</p>
          ) : (
            <ul className="notif-list">
              {items.map((n) => (
                <li
                  key={n.id}
                  className={`notif-item${n.read ? "" : " notif-unread"}`}
                  data-testid={`notif-item-${n.id}`}
                >
                  <span className="notif-item-title">{n.title}</span>
                  <span className="notif-item-body">{n.body}</span>
                  <span className="notif-item-time">{relativeTime(n.ts)}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
