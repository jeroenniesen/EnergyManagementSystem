// Ask-the-assistant chat. Questions are answered ONLY from the current dashboard/plan data
// (the backend grounds every answer + guards against invented numbers). Off unless AI is enabled.
import { useEffect, useRef, useState } from "react";

import { authHeaders } from "./auth";

type Msg = { role: "you" | "assistant"; text: string; source?: string };

const SUGGESTIONS = [
  "Why isn't the battery charging right now?",
  "What's the plan for tonight?",
  "How much am I saving today?",
];

export function ChatPanel() {
  const [active, setActive] = useState<boolean | null>(null);
  const [language, setLanguage] = useState("English");
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    fetch("/api/explainer")
      .then((r) => (r.ok ? r.json() : null))
      .then((b) => {
        if (b) {
          setActive(b.active);
          setLanguage(b.language ?? "English");
        }
      })
      .catch(() => setActive(false));
  }, []);
  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [msgs, busy]);

  async function send(q: string) {
    const question = q.trim();
    if (!question || busy) return;
    setInput("");
    setMsgs((m) => [...m, { role: "you", text: question }]);
    setBusy(true);
    try {
      const r = await fetch("/api/chat", {
        method: "POST",
        headers: { "content-type": "application/json", ...authHeaders() },
        body: JSON.stringify({ question }),
      });
      const b = await r.json();
      setMsgs((m) => [...m, { role: "assistant", text: b.answer ?? "(no answer)", source: b.source }]);
    } catch {
      setMsgs((m) => [
        ...m,
        { role: "assistant", text: "Sorry — I couldn't reach the assistant.", source: "error" },
      ]);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="chat" data-testid="chat">
      <div className="override-head">
        <span className="metric-label">Ask the assistant</span>
        {active === false && (
          <span className="badge badge-muted" data-testid="chat-off">AI off</span>
        )}
      </div>

      {active === false ? (
        <p className="plan-reason" data-testid="chat-disabled">
          The chat is powered by AI, which is currently off. Turn on <b>AI explanations &amp; chat</b>{" "}
          in Settings to ask questions about your battery&apos;s decisions and the dashboard.
        </p>
      ) : (
        <>
          <p className="chat-intro">
            Ask about the plan, a decision, prices or savings — answers come only from your current
            dashboard data{active ? `, in ${language}` : ""}.
          </p>
          <div className="chat-log" data-testid="chat-log">
            {msgs.length === 0 && (
              <div className="chat-suggest">
                {SUGGESTIONS.map((s) => (
                  <button key={s} type="button" className="chat-chip" onClick={() => send(s)}>
                    {s}
                  </button>
                ))}
              </div>
            )}
            {msgs.map((m, i) => (
              <div key={i} className={`chat-msg chat-${m.role}`}>
                <span className="chat-who">{m.role === "you" ? "You" : "Assistant"}</span>
                <p className="chat-text">{m.text}</p>
              </div>
            ))}
            {busy && (
              <div className="chat-msg chat-assistant">
                <span className="chat-who">Assistant</span>
                <p className="chat-text chat-typing">…</p>
              </div>
            )}
            <div ref={endRef} />
          </div>
          <form
            className="chat-form"
            onSubmit={(e) => {
              e.preventDefault();
              send(input);
            }}
          >
            <input
              className="chat-input"
              data-testid="chat-input"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder="Ask a question…"
              disabled={busy}
              aria-label="Your question"
            />
            <button
              className="btn-primary"
              type="submit"
              disabled={busy || !input.trim()}
              data-testid="chat-send"
            >
              Send
            </button>
          </form>
        </>
      )}
    </section>
  );
}
