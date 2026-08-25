import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { Link, useParams } from "react-router-dom";
import mermaid from "mermaid";
import { warmupMermaid } from "./mermaidRender";
import {
  approve,
  chat,
  endSession,
  finalDownloadUrl,
  getSession,
  marketEvaluationDownloadUrl,
  retryHandoff,
  specDownloadUrl,
  formatMessageNode,
  sessionHeaderChips,
  shouldShowMessageNode,
  type DesignSession,
} from "./api";
import {
  catalogCoversDiagram,
  chatDescribesComponents,
  diagramIsDue,
  extractSpecSection,
  fallbackComponentCatalog,
  fallbackDesignDiagram,
  stripSpecSection,
} from "./fallbackDiagram";
import { MarkdownView } from "./MarkdownView";
import { MermaidDiagram } from "./MermaidDiagram";

// Generic error message handler - provides user-friendly messages without exposing backend details
function getUserFriendlyError(_err: unknown): string {
  // Always return a generic message regardless of the actual error
  return "Something went wrong. Please try again.";
}

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
    fontSize: "16px",
    fontWeight: "700",
  },
  flowchart: {
    htmlLabels: true,
    wrappingWidth: 200,
    padding: 12,
    nodeSpacing: 48,
    rankSpacing: 56,
  },
});
void warmupMermaid();

export function SessionPage() {
  const { sessionId = "" } = useParams();
  const [session, setSession] = useState<DesignSession | null>(null);
  const [message, setMessage] = useState("");
  const [pendingUserText, setPendingUserText] = useState<string | null>(null);
  const [chatBusy, setChatBusy] = useState(false);
  const [approveBusy, setApproveBusy] = useState(false);
  const [retryBusy, setRetryBusy] = useState(false);
  const [endConfirm, setEndConfirm] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const phaseBeforeApprove = useRef<string | null>(null);
  const kindBeforeApprove = useRef<string | null>(null);

  const load = useCallback(async () => {
    const data = await getSession(sessionId);
    setSession(data);
  }, [sessionId]);

  useEffect(() => {
    load().catch((err) => setError(getUserFriendlyError(err)));
    setEndConfirm(false);
  }, [load]);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [session?.messages, pendingUserText, chatBusy]);

  const inMarket = session?.phase === "market_research";
  const diagramSource = session
    ? session.design_diagram?.trim() ||
      (diagramIsDue(session.phase, session.design_track, session.design_step)
        ? fallbackDesignDiagram(session.business_spec, session.design_track)
        : "")
    : "";
  const showDiagram = Boolean(diagramSource);
  const componentCatalog = session && diagramSource
    ? (() => {
        const fromSpec = extractSpecSection(session.business_spec, "Diagram components");
        if (fromSpec && catalogCoversDiagram(fromSpec, diagramSource)) {
          return `## Diagram components\n\n${fromSpec}`;
        }
        if (catalogCoversDiagram(session.design_justification, diagramSource)) {
          return session.design_justification;
        }
        return fallbackComponentCatalog(diagramSource, session.business_spec);
      })()
    : "";
  const showComponents = Boolean(componentCatalog.trim());
  const specForPanel = session
    ? stripSpecSection(session.business_spec, "Diagram components") || session.business_spec
    : "";
  const showComponentWalkthrough =
    Boolean(session && showComponents && !chatDescribesComponents(session.messages, diagramSource));
  const showJustification = Boolean(
    session?.design_justification?.trim() &&
      !(showComponents && catalogCoversDiagram(session.design_justification, diagramSource)),
  );
  const headerChips = session ? sessionHeaderChips(session) : [];

  const nodeTitle = useMemo(() => {
    if (!session) return "Loading…";
    if (session.finalized) return "Design finalized";
    if (inMarket) return "Market evaluation";
    if (session.phase === "lld") return "Low-level design";
    if (session.phase === "hld") return "High-level design";
    if (session.phase === "phase0") return "Scope classification";
    return "Design session";
  }, [session, inMarket]);

  async function onChat(e: FormEvent) {
    e.preventDefault();
    const text = message.trim();
    if (!text || chatBusy || approveBusy || retryBusy) return;
    setEndConfirm(false);
    setChatBusy(true);
    setPendingUserText(text);
    setMessage("");
    setError(null);
    try {
      const data = await chat(sessionId, text);
      setSession(data);
      setPendingUserText(null);
    } catch (err) {
      setError(getUserFriendlyError(err));
      setMessage(text);
      setPendingUserText(null);
    } finally {
      setChatBusy(false);
    }
  }

  function onComposerKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key !== "Enter" || e.shiftKey || e.nativeEvent.isComposing) return;
    e.preventDefault();
    if (chatBusy || approveBusy || retryBusy || !message.trim()) return;
    e.currentTarget.form?.requestSubmit();
  }

  async function onApprove() {
    if (approveBusy || retryBusy || !session) return;
    setEndConfirm(false);
    phaseBeforeApprove.current = session.phase;
    kindBeforeApprove.current = session.approve_kind;
    setApproveBusy(true);
    setError(null);
    try {
      const data = await approve(sessionId);
      setSession(data);
    } catch (err) {
      setError(getUserFriendlyError(err));
    } finally {
      setApproveBusy(false);
      phaseBeforeApprove.current = null;
      kindBeforeApprove.current = null;
    }
  }

  async function onRetryHandoff() {
    if (retryBusy || approveBusy || chatBusy || !session?.can_retry_handoff) return;
    setEndConfirm(false);
    setRetryBusy(true);
    setError(null);
    try {
      const data = await retryHandoff(sessionId);
      setSession(data);
    } catch (err) {
      setError(getUserFriendlyError(err));
    } finally {
      setRetryBusy(false);
    }
  }

  async function onEndSession() {
    if (approveBusy || chatBusy || retryBusy || !endConfirm) return;
    setApproveBusy(true);
    setError(null);
    try {
      const data = await endSession(sessionId);
      setSession(data);
      setEndConfirm(false);
    } catch (err) {
      setError(getUserFriendlyError(err));
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

  const busy = chatBusy || approveBusy || retryBusy;
  const canApprove = Boolean(session.can_approve) && !session.finalized;
  const canRetry = Boolean(session.can_retry_handoff) && !session.finalized;
  const approveLabel = approveBusy
    ? kindBeforeApprove.current === "design"
      ? "Researching alternatives…"
      : phaseBeforeApprove.current === "market_research"
        ? "Handing off & resuming design…"
        : "Advancing…"
    : session.approve_label || "Continue";

  const thinkingLabel =
    retryBusy
      ? "Retrying handoff of this design package to the Orchestrator…"
      : approveBusy && kindBeforeApprove.current === "design"
        ? "Researching alternatives and grading the idea…"
        : approveBusy && phaseBeforeApprove.current === "market_research"
          ? "Handing off the design package and resuming design…"
          : approveBusy
            ? "Advancing the design track…"
            : "Working on your message…";

  const chatPlaceholder = chatBusy
    ? "Wait for the architect to finish…"
    : session.phase === "market_research"
      ? "Optional note before handoff; a new round then starts at Phase 0…"
      : session.phase === "lld" || session.phase === "hld"
        ? "Ask for design changes or answer the current step…"
        : "Answer the architect…";

  const showMarketReport = Boolean(session.market_evaluation_report?.trim());
  const showLedger = Boolean(session.tradeoff_ledger?.trim());
  const showScale = Boolean(session.scale_estimates?.trim());
  const showServices = Boolean(session.api_contracts?.trim());
  const showComms = Boolean(session.communication_schemes?.trim());
  const showFmea = Boolean(session.fmea_notes?.trim());

  return (
    <div className="app session-app">
      <div className="atmosphere" aria-hidden />
      <header className="top">
        <div className="top-copy">
          <p className="brand">Architect Agent</p>
          <h1>Design atelier</h1>
          <div className="meta-row">
            {headerChips.map((label) => (
              <span key={label} className="chip">
                {label}
              </span>
            ))}
            {session.market_evaluation_grade ? (
              <span className="chip accent">Grade {session.market_evaluation_grade}</span>
            ) : null}
            {session.design_version > 0 ? (
              <span className={session.last_handoff?.status === "failed" ? "chip" : "chip accent"}>
                {session.last_handoff?.status === "failed"
                  ? `v${session.design_version} handoff failed`
                  : session.last_handoff?.status === "queued"
                    ? `v${session.design_version} queued`
                    : `v${session.design_version} sent`}
              </span>
            ) : null}
            {busy ? <span className="chip pulse">Thinking</span> : null}
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
          {!session.finalized ? (
            <button
              className="btn ghost"
              type="button"
              disabled={busy}
              onClick={() => setEndConfirm(true)}
              aria-expanded={endConfirm}
            >
              End session
            </button>
          ) : null}
        </div>
      </header>

      {endConfirm && !session.finalized ? (
        <div className="end-confirm" role="alertdialog" aria-labelledby="end-confirm-title">
          <p id="end-confirm-title" className="end-confirm-copy">
            End this design session? Chat and approve will stop. This cannot be undone.
          </p>
          <button
            className="btn ghost"
            type="button"
            disabled={busy}
            onClick={() => setEndConfirm(false)}
          >
            Cancel
          </button>
          <button
            className="btn danger"
            type="button"
            disabled={approveBusy || chatBusy || retryBusy}
            onClick={onEndSession}
          >
            {approveBusy ? "Ending…" : "Yes, end session"}
          </button>
        </div>
      ) : null}

      <div className="approve-bar">
        {canRetry ? (
          <button
            className="btn primary approve-btn"
            type="button"
            disabled={busy}
            onClick={onRetryHandoff}
          >
            {retryBusy ? "Retrying handoff…" : `Retry handoff v${session.design_version}`}
          </button>
        ) : null}
        <button
          className={canRetry ? "btn ghost approve-btn" : "btn primary approve-btn"}
          type="button"
          disabled={!canApprove || session.finalized || busy}
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
            <span className="panel-kicker">
              {session.design_step_title?.trim() || "Conversation"}
            </span>
          </div>
          <div className="messages" aria-live="polite">
            {session.messages.map((m, i) => (
              <div key={`${m.role}-${i}-${m.content.slice(0, 24)}`} className={`bubble ${m.role}`}>
                <span className="who">
                  {m.role === "assistant" ? "Architect" : "You"}
                  {shouldShowMessageNode(m.node, session.phase)
                    ? ` · ${formatMessageNode(m.node)}`
                    : ""}
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
            {chatBusy || approveBusy || retryBusy ? (
              <div className="bubble assistant thinking" aria-label="Architect is processing">
                <span className="who">Architect</span>
                <div className="thinking-row">
                  <span className="dot" />
                  <span className="dot" />
                  <span className="dot" />
                  <span>{thinkingLabel}</span>
                </div>
              </div>
            ) : null}
            {showComponentWalkthrough ? (
              <div className="bubble assistant">
                <span className="who">Architect · diagram</span>
                <MarkdownView
                  content={`Here is what each box on the **system design diagram** is responsible for.\n\n${componentCatalog}`}
                  className="bubble-md"
                />
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
                disabled={busy}
                required
                aria-keyshortcuts="Enter Shift+Enter"
                title="Enter to send · Shift+Enter for a new line"
              />
              <button
                className="btn primary"
                type="submit"
                disabled={busy || !message.trim()}
              >
                {chatBusy ? "Sending…" : "Send"}
              </button>
            </form>
          ) : (
            <p className="meta finalize-note">This design session is finalized.</p>
          )}
        </section>

        <section className="panel view-panel">
          {showDiagram ? (
            <div className="artifact diagram-artifact">
              <div className="panel-head">
                <h2>System design diagram</h2>
                <span className="panel-kicker">Diagram</span>
              </div>
              <MermaidDiagram source={diagramSource} />
            </div>
          ) : null}
          {showComponents ? (
            <div className="artifact">
              <div className="panel-head">
                <h2>Diagram components</h2>
                <span className="panel-kicker">Spec</span>
              </div>
              <MarkdownView content={componentCatalog} className="doc" />
            </div>
          ) : null}
          <div className="artifact">
            <div className="panel-head">
              <h2>Living business specification</h2>
              <span className="panel-kicker">Spec</span>
            </div>
            <MarkdownView content={specForPanel} className="doc" />
          </div>
          {showLedger ? (
            <div className="artifact">
              <div className="panel-head">
                <h2>Trade-off ledger</h2>
                <span className="panel-kicker">Decisions</span>
              </div>
              <MarkdownView content={session.tradeoff_ledger} className="doc" />
            </div>
          ) : null}
          {showScale ? (
            <div className="artifact">
              <div className="panel-head">
                <h2>Scale estimates</h2>
                <span className="panel-kicker">Capacity</span>
              </div>
              <MarkdownView content={session.scale_estimates} className="doc" />
            </div>
          ) : null}
          {showServices ? (
            <div className="artifact">
              <div className="panel-head">
                <h2>Core microservices</h2>
                <span className="panel-kicker">Ownership</span>
              </div>
              <MarkdownView content={session.api_contracts} className="doc" />
            </div>
          ) : null}
          {showComms ? (
            <div className="artifact">
              <div className="panel-head">
                <h2>Communication schemes</h2>
                <span className="panel-kicker">Protocols</span>
              </div>
              <MarkdownView content={session.communication_schemes} className="doc" />
            </div>
          ) : null}
          {showFmea ? (
            <div className="artifact">
              <div className="panel-head">
                <h2>FMEA notes</h2>
                <span className="panel-kicker">Risks</span>
              </div>
              <MarkdownView content={session.fmea_notes} className="doc" />
            </div>
          ) : null}
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
          {showJustification ? (
            <div className="artifact">
              <div className="panel-head">
                <h2>Justification</h2>
                <span className="panel-kicker">Rationale</span>
              </div>
              <MarkdownView content={session.design_justification} className="doc" />
            </div>
          ) : null}
        </section>
      </main>
    </div>
  );
}
