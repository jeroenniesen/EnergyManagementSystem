// Ask-the-assistant chat. Questions are answered ONLY from the current dashboard/plan data
// (the backend grounds every answer + guards against invented numbers). Off unless AI is enabled.
import { useEffect, useRef, useState } from "react";

import { authHeaders } from "./auth";

type Msg = { role: "you" | "assistant"; text: string; source?: string };
type Faq = { key: string; question: string; answer: string };

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
  const [faq, setFaq] = useState<Faq[]>([]);
  const [openFaq, setOpenFaq] = useState<string | null>(null);
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
    // Grounded FAQ answers come from the deterministic plan/readiness — they work with AI off.
    fetch("/api/faq")
      .then((r) => (r.ok ? r.json() : null))
      .then((b) => b && setFaq(b.items ?? []))
      .catch(() => {});
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
    <section className="chat" data-testid="chat" data-density-surface="chat" data-density-kind="card">
      <div className="override-head">
        <span className="metric-label">Ask the assistant</span>
        {active === false && (
          <span className="badge badge-muted" data-testid="chat-off">AI off</span>
        )}
      </div>

      {faq.length > 0 && (
        <div className="faq" data-testid="faq" data-density-kind="subordinate">
          <p className="chat-suggest-lead">
            Quick answers{active === false ? " — these work without AI" : ""}:
          </p>
          {faq.map((f) => (
            <div key={f.key} className="faq-item">
              <button
                type="button"
                className="chat-chip"
                data-testid={`faq-${f.key}`}
                aria-expanded={openFaq === f.key}
                onClick={() => setOpenFaq(openFaq === f.key ? null : f.key)}
              >
                {f.question}
              </button>
              {openFaq === f.key && (
                <p className="faq-answer" data-testid={`faq-answer-${f.key}`}>
                  {f.answer}
                </p>
              )}
            </div>
          ))}
        </div>
      )}

      {active === false ? (
        <p className="plan-reason" data-testid="chat-disabled">
          The answers above always work. For free-form questions, turn on{" "}
          <b>AI explanations &amp; chat</b> in Manage → Settings.
        </p>
      ) : (
        <>
          <p className="chat-intro">
            Ask about the plan, a decision, prices or savings — answers come only from your current
            dashboard data{active ? `, in ${language}` : ""}.
          </p>
          <div className="chat-log" data-testid="chat-log">
            {msgs.length === 0 && (
              <div className="chat-empty">
                <p className="chat-suggest-lead">Try asking…</p>
                <div className="chat-suggest">
                  {SUGGESTIONS.map((s) => (
                    <button key={s} type="button" className="chat-chip" onClick={() => send(s)}>
                      {s}
                    </button>
                  ))}
                </div>
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
                <p className="chat-text chat-typing" aria-label="Assistant is typing">
                  <span /><span /><span />
                </p>
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
