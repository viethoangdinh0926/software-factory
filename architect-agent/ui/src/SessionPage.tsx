import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { Link, useParams } from "react-router-dom";
import mermaid from "mermaid";
import {
  approve,
  chat,
  finalDownloadUrl,
  getSession,
  marketEvaluationDownloadUrl,
  specDownloadUrl,
  type DesignSession,
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
    secondaryColor: "#c8f0d8",
    secondaryTextColor: "#000000",
    secondaryBorderColor: "#4a6a8a",
    tertiaryColor: "#ffe2b8",
    tertiaryTextColor: "#000000",
    tertiaryBorderColor: "#4a6a8a",
    lineColor: "#4a5a70",
    textColor: "#000000",
    mainBkg: "#b8d4f0",
    nodeBkg: "#b8d4f0",
    nodeBorder: "#4a6a8a",
    clusterBkg: "#e2d0ff",
    clusterBorder: "#5a7088",
    titleColor: "#000000",
    edgeLabelBackground: "#e8eef6",
    fontFamily: "IBM Plex Sans, sans-serif",
    fontWeight: "700",
  },
});

export function SessionPage() {
  const { sessionId = "" } = useParams();
  const [session, setSession] = useState<DesignSession | null>(null);
  const [message, setMessage] = useState("");
  const [pendingUserText, setPendingUserText] = useState<string | null>(null);
  const [chatBusy, setChatBusy] = useState(false);
  const [approveBusy, setApproveBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);

  const load = useCallback(async () => {
    const data = await getSession(sessionId);
    setSession(data);
  }, [sessionId]);

  useEffect(() => {
    load().catch((err) => setError(err instanceof Error ? err.message : String(err)));
  }, [load]);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [session?.messages, pendingUserText, chatBusy]);

  const inMarket = session?.phase === "market_research";
  const inDesign = session?.phase === "system_design" || session?.phase === "done";
  const nodeTitle = useMemo(() => {
    if (!session) return "Loading…";
    if (session.finalized) return "Design finalized";
    if (inMarket) return "Market evaluation";
    if (inDesign) return "System design";
    return "Specification interview";
  }, [session, inDesign, inMarket]);

  async function onChat(e: FormEvent) {
    e.preventDefault();
    const text = message.trim();
    if (!text || chatBusy || approveBusy) return;
    setChatBusy(true);
    setPendingUserText(text);
    setMessage("");
    setError(null);
    try {
      const data = await chat(sessionId, text);
      setSession(data);
      setPendingUserText(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setMessage(text);
      setPendingUserText(null);
    } finally {
      setChatBusy(false);
    }
  }

  function onComposerKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key !== "Enter" || e.shiftKey || e.nativeEvent.isComposing) return;
    e.preventDefault();
    if (chatBusy || approveBusy || !message.trim()) return;
    e.currentTarget.form?.requestSubmit();
  }

  async function onApprove() {
    if (approveBusy) return;
    setApproveBusy(true);
    setError(null);
    try {
      const data = await approve(sessionId);
      setSession(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setApproveBusy(false);
    }
  }

  if (!session && error) {
    return (
      <div className="app">
        <div className="state-card">
          <p className="brand">Architect Agent</p>
          <h1>Session unavailable</h1>
          <p className="error">{error}</p>
          <Link className="btn ghost" to="/">
            Back home
          </Link>
        </div>
      </div>
    );
  }

  if (!session) {
    return (
      <div className="app">
        <div className="state-card">
          <p className="brand">Architect Agent</p>
          <div className="loading-line" aria-hidden />
          <p className="meta">Restoring design session…</p>
        </div>
      </div>
    );
  }

  const canApprove =
    (session.phase === "spec_interview" && session.ready_for_design) ||
    (session.phase === "market_research" && session.market_evaluation_done) ||
    (session.phase === "system_design" && !session.finalized);

  const phaseLabel =
    session.phase === "spec_interview"
      ? "Interview"
      : session.phase === "market_research"
        ? "Market eval"
        : session.phase === "system_design"
          ? "Design"
          : session.phase;

  const approveLabel = approveBusy
    ? session.phase === "spec_interview"
      ? "Researching market…"
      : session.phase === "market_research"
        ? "Starting design…"
        : "Sending…"
    : session.phase === "system_design"
      ? "Approve & send design"
      : session.phase === "market_research"
        ? "Continue to system design"
        : "Approve business spec";

  const chatPlaceholder = chatBusy
    ? "Wait for the architect to finish…"
    : session.phase === "system_design"
      ? "Ask for design changes…"
      : session.phase === "market_research"
        ? "Optional note before continuing to design…"
        : "Answer the architect…";

  const showMarketReport = Boolean(session.market_evaluation_report?.trim());

  return (
    <div className="app session-app">
      <div className="atmosphere" aria-hidden />
      <header className="top">
        <div className="top-copy">
          <p className="brand">Architect Agent</p>
          <h1>Design atelier</h1>
          <div className="meta-row">
            <span className="chip">{phaseLabel}</span>
            {session.market_evaluation_grade ? (
              <span className="chip accent">Grade {session.market_evaluation_grade}</span>
            ) : null}
            {session.design_version > 0 ? (
              <span className="chip accent">v{session.design_version} sent</span>
            ) : null}
            {chatBusy || approveBusy ? <span className="chip pulse">Thinking</span> : null}
            <code className="session-id">{session.design_session_id}</code>
          </div>
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
            Download spec
          </a>
          {showMarketReport ? (
            <a className="btn ghost" href={marketEvaluationDownloadUrl(sessionId)}>
              Download market report
            </a>
          ) : null}
          {session.design_version > 0 ? (
            <a className="btn ghost" href={finalDownloadUrl(sessionId)}>
              Download package
            </a>
          ) : null}
        </div>
      </header>

      <div className="approve-bar">
        <button
          className="btn primary approve-btn"
          type="button"
          disabled={!canApprove || session.finalized || approveBusy || chatBusy}
          onClick={onApprove}
        >
          {approveLabel}
        </button>
      </div>

      {error ? <p className="error banner">{error}</p> : null}

      <main className="grid session-grid">
        <section className="panel chat-panel">
          <div className="panel-head">
            <h2>{nodeTitle}</h2>
            <span className="panel-kicker">Conversation</span>
          </div>
          <div className="messages" aria-live="polite">
            {session.messages.map((m, i) => (
              <div key={`${m.role}-${i}-${m.content.slice(0, 24)}`} className={`bubble ${m.role}`}>
                <span className="who">
                  {m.role === "assistant" ? "Architect" : "You"}
                  {m.node ? ` · ${m.node.replaceAll("_", " ")}` : ""}
                </span>
                <MarkdownView content={m.content} className="bubble-md" />
              </div>
            ))}
            {pendingUserText ? (
              <div className="bubble user pending">
                <span className="who">You</span>
                <MarkdownView content={pendingUserText} className="bubble-md" />
              </div>
            ) : null}
            {chatBusy || approveBusy ? (
              <div className="bubble assistant thinking" aria-label="Architect is processing">
                <span className="who">Architect</span>
                <div className="thinking-row">
                  <span className="dot" />
                  <span className="dot" />
                  <span className="dot" />
                  <span>
                    {approveBusy && session.phase === "spec_interview"
                      ? "Researching alternatives and grading the idea…"
                      : approveBusy && session.phase === "market_research"
                        ? "Drafting the system design…"
                        : "Working on your message…"}
                  </span>
                </div>
              </div>
            ) : null}
            <div ref={messagesEndRef} />
          </div>
          {!session.finalized ? (
            <form className="composer" onSubmit={onChat}>
              <textarea
                rows={3}
                value={message}
                onChange={(e) => setMessage(e.target.value)}
                onKeyDown={onComposerKeyDown}
                placeholder={chatPlaceholder}
                disabled={chatBusy || approveBusy}
                required
                aria-keyshortcuts="Enter Shift+Enter"
                title="Enter to send · Shift+Enter for a new line"
              />
              <button
                className="btn primary"
                type="submit"
                disabled={chatBusy || approveBusy || !message.trim()}
              >
                {chatBusy ? "Sending…" : "Send"}
              </button>
            </form>
          ) : (
            <p className="meta finalize-note">This design session is finalized.</p>
          )}
        </section>

        <section className="panel view-panel">
          <div className="artifact">
            <div className="panel-head">
              <h2>Living business specification</h2>
              <span className="panel-kicker">Spec</span>
            </div>
            <MarkdownView content={session.business_spec} className="doc" />
          </div>
          {showMarketReport ? (
            <div className="artifact">
              <div className="panel-head">
                <h2>Market evaluation</h2>
                <span className="panel-kicker">
                  {session.market_evaluation_grade
                    ? `Grade ${session.market_evaluation_grade}`
                    : "Report"}
                </span>
              </div>
              <MarkdownView content={session.market_evaluation_report} className="doc doc-tall" />
            </div>
          ) : null}
          {inDesign ? (
            <div className="artifact">
              <div className="panel-head">
                <h2>Justification</h2>
                <span className="panel-kicker">Rationale</span>
              </div>
              <MarkdownView content={session.design_justification} className="doc" />
            </div>
          ) : null}
        </section>

        {inDesign ? (
          <section className="panel diagram-panel">
            <div className="panel-head">
              <h2>System design diagram</h2>
              <span className="panel-kicker">Diagram</span>
            </div>
            <MermaidDiagram source={session.design_diagram} />
          </section>
        ) : null}
      </main>
    </div>
  );
}
