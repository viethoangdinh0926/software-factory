import { useCallback, useEffect, useState, type FormEvent, type KeyboardEvent } from "react";
import { Link, useParams } from "react-router-dom";
import mermaid from "mermaid";
import {
  approve,
  chat,
  endSession,
  getSession,
  planDownloadUrl,
  serviceLabel,
  type MicroservicePlan,
  type WorkflowSession,
} from "./api";
import { MarkdownView } from "./MarkdownView";
import { MermaidDiagram } from "./MermaidDiagram";

mermaid.initialize({
  startOnLoad: false,
  securityLevel: "loose",
  theme: "base",
  themeVariables: {
    darkMode: false,
    background: "#e8eef6",
    primaryColor: "#b8d4f0",
    primaryTextColor: "#000000",
    primaryBorderColor: "#4a6a8a",
    lineColor: "#4a5a70",
    textColor: "#000000",
    fontFamily: "IBM Plex Sans, sans-serif",
    fontWeight: "700",
  },
});

function onComposerKey(e: KeyboardEvent<HTMLTextAreaElement>) {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    e.currentTarget.form?.requestSubmit();
  }
}

function ServiceTile({
  sessionId,
  svc,
  busy,
  onBusy,
  onUpdate,
  onError,
}: {
  sessionId: string;
  svc: MicroservicePlan;
  busy: boolean;
  onBusy: (id: string | null) => void;
  onUpdate: (session: WorkflowSession) => void;
  onError: (msg: string) => void;
}) {
  const [message, setMessage] = useState("");
  const [pending, setPending] = useState<string | null>(null);
  const open = svc.discussion_open && svc.status !== "suspended";

  async function onChat(e: FormEvent) {
    e.preventDefault();
    const text = message.trim();
    if (!text || busy || !open) return;
    onBusy(svc.microservice_id);
    setPending(text);
    setMessage("");
    try {
      onUpdate(await chat(sessionId, text, svc.microservice_id));
    } catch (err) {
      onError(err instanceof Error ? err.message : String(err));
    } finally {
      setPending(null);
      onBusy(null);
    }
  }

  async function onApprove() {
    if (busy || !svc.can_approve) return;
    onBusy(svc.microservice_id);
    try {
      onUpdate(await approve(sessionId, svc.microservice_id));
    } catch (err) {
      onError(err instanceof Error ? err.message : String(err));
    } finally {
      onBusy(null);
    }
  }

  const apiType = svc.api_type || svc.proposed_api_type;

  return (
    <section className={`panel service-tile${svc.status === "suspended" ? " suspended" : ""}`}>
      <div className="panel-head">
        <h2>{serviceLabel(svc)}</h2>
        <span className="panel-kicker">{svc.status}</span>
      </div>
      <p className="mono tile-id">{svc.microservice_id}</p>
      <div className="tile-body">
        <div className="messages">
          {(svc.messages || [])
            .filter((msg) => (msg.content || "").trim())
            .map((msg, i) => (
            <div key={`${msg.role}-${i}`} className={`bubble ${msg.role}`}>
              <span className="who">{msg.role}</span>
              <MarkdownView content={msg.content} className="bubble-md" />
            </div>
          ))}
          {pending ? (
            <div className="bubble user pending">
              <span className="who">you</span>
              <MarkdownView content={pending} className="bubble-md" />
            </div>
          ) : null}
          {busy ? (
            <div className="bubble assistant thinking">
              <span className="thinking-row">
                <span className="dot" />
                <span className="dot" />
                <span className="dot" />
                Planning…
              </span>
            </div>
          ) : null}
        </div>
        {apiType ? (
          <article className="artifact">
            <h3>API type</h3>
            <p>{apiType}</p>
          </article>
        ) : null}
        {svc.api_design ? (
          <article className="artifact">
            <h3>API design</h3>
            <div className="doc">
              <MarkdownView content={svc.api_design} />
            </div>
          </article>
        ) : null}
        {svc.tech_stack ? (
          <article className="artifact">
            <h3>Tech stack</h3>
            <div className="doc">
              <MarkdownView content={svc.tech_stack} />
            </div>
          </article>
        ) : null}
      </div>
      <div className="tile-footer">
        {svc.can_approve ? (
          <button className="btn primary" type="button" disabled={busy} onClick={onApprove}>
            {busy ? "Working…" : svc.approve_label || "Approve"}
          </button>
        ) : null}
        {open ? (
          <form className="composer" onSubmit={onChat}>
            <textarea
              value={message}
              onChange={(e) => setMessage(e.target.value)}
              onKeyDown={onComposerKey}
              rows={2}
              disabled={busy}
              placeholder="Discuss this service… Enter to send"
            />
            <button className="btn" type="submit" disabled={busy || !message.trim()}>
              Send
            </button>
          </form>
        ) : (
          <p className="finalize-note">This microservice is suspended.</p>
        )}
      </div>
    </section>
  );
}

export function SessionPage() {
  const { sessionId = "" } = useParams();
  const [session, setSession] = useState<WorkflowSession | null>(null);
  const [message, setMessage] = useState("");
  const [pendingUserText, setPendingUserText] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [endConfirm, setEndConfirm] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const chatBusy = busyId === "session";
  const anyBusy = Boolean(busyId);

  const load = useCallback(async () => {
    const data = await getSession(sessionId);
    setSession(data);
  }, [sessionId]);

  useEffect(() => {
    load().catch((err) => setError(err instanceof Error ? err.message : String(err)));
    setEndConfirm(false);
  }, [load]);

  useEffect(() => {
    if (anyBusy) return;
    const id = window.setInterval(() => {
      load().catch(() => undefined);
    }, 4000);
    return () => window.clearInterval(id);
  }, [load, anyBusy]);

  async function onChat(e: FormEvent) {
    e.preventDefault();
    const text = message.trim();
    if (!text || !session || anyBusy || session.discussion_locked) return;
    setEndConfirm(false);
    setBusyId("session");
    setPendingUserText(text);
    setMessage("");
    try {
      setSession(await chat(session.design_session_id, text));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setPendingUserText(null);
      setBusyId(null);
    }
  }

  async function onApprove() {
    if (!session || anyBusy || session.discussion_locked) return;
    setEndConfirm(false);
    setBusyId("session");
    try {
      setSession(await approve(session.design_session_id));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusyId(null);
    }
  }

  async function onEndSession() {
    if (!session || !endConfirm) return;
    setBusyId("session");
    try {
      setSession(await endSession(session.design_session_id));
      setEndConfirm(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusyId(null);
    }
  }

  if (!session) {
    return (
      <div className="app">
        <div className="atmosphere" aria-hidden />
        <div className="state-card">
          <p className="brand">Orchestrator Agent</p>
          <h1>Loading workflow…</h1>
          <div className="loading-line" />
          {error ? <p className="error">{error}</p> : null}
        </div>
      </div>
    );
  }

  const distributed = session.topology === "distributed";
  const locked = session.discussion_locked;
  const liveServices = session.services.filter((s) => s.status !== "suspended");
  const suspendedServices = session.services.filter((s) => s.status === "suspended");

  return (
    <div className="app session-app">
      <div className="atmosphere" aria-hidden />
      <header className="top">
        <div className="top-copy">
          <p className="brand">Orchestrator Agent</p>
          <h1>Delivery plan</h1>
          <p className="meta">
            <span className="session-id">{session.design_session_id}</span>
          </p>
          <div className="meta-row">
            <span className="chip">v{session.design_version}</span>
            <span className="chip">{session.architect_track || "track?"}</span>
            <span className="chip accent">{session.topology || "topology?"}</span>
            <span className="chip">{session.phase}</span>
            {locked ? <span className="chip">handed off</span> : null}
            {session.last_handoff ? (
              <span className="chip">
                Engineer {session.last_handoff.action}: {session.last_handoff.status}
              </span>
            ) : null}
          </div>
        </div>
        <div className="actions">
          <Link className="btn ghost" to="/">
            Home
          </Link>
          <a className="btn ghost" href={planDownloadUrl(session.design_session_id)}>
            Download plan
          </a>
          {!session.finalized ? (
            <button
              className="btn ghost"
              type="button"
              disabled={anyBusy}
              onClick={() => setEndConfirm(true)}
            >
              End session
            </button>
          ) : null}
        </div>
      </header>

      {endConfirm && !session.finalized ? (
        <div className="end-confirm" role="alertdialog">
          <p className="end-confirm-copy">End this workflow? Chat and approve will stop.</p>
          <button className="btn ghost" type="button" onClick={() => setEndConfirm(false)}>
            Cancel
          </button>
          <button className="btn danger" type="button" onClick={onEndSession}>
            {chatBusy ? "Ending…" : "Yes, end session"}
          </button>
        </div>
      ) : null}

      {error ? <p className="error banner">{error}</p> : null}

      {distributed ? (
        <>
          {session.messages.filter((m) => m.role === "system" || m.node === "prime" || m.node === "extract").length ? (
            <div className="doc workflow-banner">
              <MarkdownView
                content={session.messages
                  .filter((m) => m.role === "system" || m.node === "prime" || m.node === "extract" || m.node === "ingest")
                  .map((m) => m.content)
                  .slice(-4)
                  .join("\n\n")}
              />
            </div>
          ) : null}
          <div className="tile-grid">
            {liveServices.map((svc) => (
              <ServiceTile
                key={svc.microservice_id}
                sessionId={session.design_session_id}
                svc={svc}
                busy={busyId === svc.microservice_id}
                onBusy={setBusyId}
                onUpdate={setSession}
                onError={setError}
              />
            ))}
          </div>
          {suspendedServices.length ? (
            <p className="lede">
              Suspended: {suspendedServices.map((s) => serviceLabel(s)).join(", ")}
            </p>
          ) : null}
        </>
      ) : (
        <>
          {locked ? (
            <p className="end-confirm-copy lock-note">
              Plan spec handed off to the engineer. This session stays readable; a new discussion
              starts only if the architect sends an updated stand-alone package.
            </p>
          ) : null}
          <div className="approve-bar">
            <button
              className="btn primary approve-btn"
              type="button"
              disabled={!session.can_approve || anyBusy || session.finalized || locked}
              onClick={onApprove}
            >
              {chatBusy ? "Working…" : session.approve_label || "Approve"}
            </button>
          </div>
          <div className="grid session-grid">
            <section className="panel chat-panel">
              <div className="panel-head">
                <h2>Chat</h2>
                <span className="panel-kicker">application</span>
              </div>
              <div className="messages">
                {session.messages
                  .filter((msg) => (msg.content || "").trim())
                  .map((msg, i) => (
                  <div key={`${msg.role}-${i}`} className={`bubble ${msg.role}`}>
                    <span className="who">{msg.role}</span>
                    <MarkdownView content={msg.content} className="bubble-md" />
                  </div>
                ))}
                {pendingUserText ? (
                  <div className="bubble user pending">
                    <span className="who">you</span>
                    <MarkdownView content={pendingUserText} className="bubble-md" />
                  </div>
                ) : null}
                {chatBusy ? (
                  <div className="bubble assistant thinking">
                    <span className="thinking-row">
                      <span className="dot" />
                      <span className="dot" />
                      <span className="dot" />
                      Planning…
                    </span>
                  </div>
                ) : null}
              </div>
              {session.finalized || locked ? (
                <p className="finalize-note">
                  {session.finalized ? "This workflow has ended." : "Discussion locked until an architect update."}
                </p>
              ) : (
                <form className="composer" onSubmit={onChat}>
                  <textarea
                    value={message}
                    onChange={(e) => setMessage(e.target.value)}
                    onKeyDown={onComposerKey}
                    rows={3}
                    disabled={anyBusy}
                    placeholder="Reply… Enter to send, Shift+Enter for a new line"
                  />
                  <button className="btn" type="submit" disabled={anyBusy || !message.trim()}>
                    Send
                  </button>
                </form>
              )}
            </section>
            <section className="panel view-panel">
              <div className="panel-head">
                <h2>Plan artifacts</h2>
                <span className="panel-kicker">{session.app_status || "draft"}</span>
              </div>
              {session.tech_stack ? (
                <article className="artifact">
                  <h3>Tech stack</h3>
                  <div className="doc">
                    <MarkdownView content={session.tech_stack} />
                  </div>
                </article>
              ) : null}
              {session.plan_spec ? (
                <article className="artifact">
                  <h3>Plan spec</h3>
                  <div className="doc">
                    <MarkdownView content={session.plan_spec} />
                  </div>
                </article>
              ) : null}
            </section>
          </div>
        </>
      )}

      {session.design_diagram ? (
        <section className="panel diagram-panel">
          <div className="panel-head">
            <h2>Architect diagram</h2>
          </div>
          <MermaidDiagram source={session.design_diagram} />
        </section>
      ) : null}
    </div>
  );
}
