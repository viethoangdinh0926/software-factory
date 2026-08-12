import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";
import { Link, useParams } from "react-router-dom";
import mermaid from "mermaid";
import {
  approve,
  chat,
  finalDownloadUrl,
  getSession,
  specDownloadUrl,
  type DesignSession,
} from "./api";
import { MermaidDiagram } from "./MermaidDiagram";

mermaid.initialize({ startOnLoad: false, theme: "neutral" });

export function SessionPage() {
  const { sessionId = "" } = useParams();
  const [session, setSession] = useState<DesignSession | null>(null);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    const data = await getSession(sessionId);
    setSession(data);
  }, [sessionId]);

  useEffect(() => {
    load().catch((err) => setError(err instanceof Error ? err.message : String(err)));
  }, [load]);

  const inDesign = session?.phase === "system_design" || session?.phase === "done";
  const nodeTitle = useMemo(() => {
    if (!session) return "Loading…";
    if (session.finalized) return "Design finalized";
    if (inDesign) return "System design";
    return "Specification interview (grill-me)";
  }, [session, inDesign]);

  async function withBusy(fn: () => Promise<void>) {
    setBusy(true);
    setError(null);
    try {
      await fn();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function onChat(e: FormEvent) {
    e.preventDefault();
    const text = message.trim();
    if (!text) return;
    await withBusy(async () => {
      const data = await chat(sessionId, text);
      setSession(data);
      setMessage("");
    });
  }

  async function onApprove() {
    await withBusy(async () => {
      const data = await approve(sessionId);
      setSession(data);
    });
  }

  if (!session && error) {
    return (
      <div className="app">
        <p className="error">{error}</p>
        <Link to="/">Back</Link>
      </div>
    );
  }

  if (!session) {
    return (
      <div className="app">
        <p className="meta">Loading session…</p>
      </div>
    );
  }

  const canApprove =
    (session.phase === "spec_interview" && session.ready_for_design) ||
    (session.phase === "system_design" && !session.finalized);

  return (
    <div className={`app ${busy ? "busy" : ""}`}>
      <header className="top">
        <div>
          <p className="brand">Architect Agent</p>
          <h1>Design session</h1>
          <p className="meta">
            Session <code>{session.design_session_id}</code> · Phase{" "}
            <strong>{session.phase}</strong>
            {session.design_version > 0 ? (
              <>
                {" "}
                · Sent v<strong>{session.design_version}</strong>
              </>
            ) : null}
          </p>
          {session.last_handoff ? (
            <p className="meta">
              Last handoff: <strong>{session.last_handoff.status}</strong>
              {session.last_handoff.target_url
                ? ` → ${session.last_handoff.target_url}`
                : " (queued locally)"}
            </p>
          ) : null}
        </div>
        <div className="actions">
          <Link className="btn ghost" to="/">
            New session
          </Link>
          <a className="btn ghost" href={specDownloadUrl(sessionId)}>
            Download business spec
          </a>
          {session.design_version > 0 ? (
            <a className="btn ghost" href={finalDownloadUrl(sessionId)}>
              Download design package
            </a>
          ) : null}
          <button
            className="btn primary"
            type="button"
            disabled={!canApprove || session.finalized || busy}
            onClick={onApprove}
          >
            {session.phase === "system_design"
              ? "Approve & send design"
              : "Approve business spec"}
          </button>
        </div>
      </header>

      {error ? <p className="error banner">{error}</p> : null}

      <main className="grid">
        <section className="panel chat-panel">
          <h2>{nodeTitle}</h2>
          <div className="messages">
            {session.messages.map((m, i) => (
              <div key={`${m.role}-${i}-${m.content.slice(0, 24)}`} className={`bubble ${m.role}`}>
                <span className="who">
                  {m.role} · {m.node || ""}
                </span>
                {m.content}
              </div>
            ))}
          </div>
          {!session.finalized ? (
            <form className="composer" onSubmit={onChat}>
              <textarea
                rows={3}
                value={message}
                onChange={(e) => setMessage(e.target.value)}
                placeholder={
                  session.phase === "system_design"
                    ? "Ask for design changes…"
                    : "Answer the architect…"
                }
                required
              />
              <button className="btn primary" type="submit" disabled={busy}>
                Send
              </button>
            </form>
          ) : null}
        </section>

        <section className="panel view-panel">
          <div>
            <h2>Living business specification</h2>
            <pre className="doc">{session.business_spec}</pre>
          </div>
          {inDesign ? (
            <div>
              <h2>System design diagram</h2>
              <MermaidDiagram source={session.design_diagram} />
              <h2>Justification</h2>
              <pre className="doc">{session.design_justification}</pre>
            </div>
          ) : null}
        </section>
      </main>
    </div>
  );
}
