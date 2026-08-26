import {
  useCallback,
  useEffect,
  useId,
  useState,
  type FormEvent,
  type KeyboardEvent,
  type ReactNode,
} from "react";
import { createPortal } from "react-dom";
import { Link, useParams } from "react-router-dom";
import mermaid from "mermaid";
import {
  approve,
  chat,
  endSession,
  getSession,
  planDownloadUrl,
  retryIngest,
  saveGit,
  sendGit,
  serviceLabel,
  type MicroservicePlan,
  type WorkflowSession,
  type WorkflowState,
} from "./api";
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
    lineColor: "#4a5a70",
    textColor: "#000000",
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

function onComposerKey(e: KeyboardEvent<HTMLTextAreaElement>) {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    e.currentTarget.form?.requestSubmit();
  }
}

function WorkflowStrip({ workflow }: { workflow?: WorkflowState }) {
  if (!workflow?.tiles?.length) return null;
  return (
    <nav className="workflow-rail" aria-label="Workflow steps">
      {workflow.tiles.map((tile) => (
        <span key={tile.id} className={`workflow-rail-item ${tile.status}`}>
          {tile.title}
        </span>
      ))}
    </nav>
  );
}

function SpecIcon() {
  return (
    <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true" focusable="false">
      <path
        fill="currentColor"
        d="M7 3.5h7.2L19.5 9v11.5A1.5 1.5 0 0 1 18 22H7a1.5 1.5 0 0 1-1.5-1.5v-16A1.5 1.5 0 0 1 7 3.5Zm6.5 1.2v5.3h5.1l-5.1-5.3ZM8.5 12.25h7v1.4h-7v-1.4Zm0 3.1h7v1.4h-7v-1.4Zm0 3.1h4.5v1.4H8.5v-1.4Z"
      />
    </svg>
  );
}

function InterviewResultsModal({
  title,
  open,
  onClose,
  children,
}: {
  title: string;
  open: boolean;
  onClose: () => void;
  children: ReactNode;
}) {
  const headingId = useId();

  useEffect(() => {
    if (!open) return;
    function onKey(e: globalThis.KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    document.addEventListener("keydown", onKey);
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = previousOverflow;
    };
  }, [open, onClose]);

  if (!open) return null;

  return createPortal(
    <div
      className="spec-modal-backdrop"
      role="presentation"
      onClick={onClose}
    >
      <div
        className="spec-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby={headingId}
        onClick={(e) => e.stopPropagation()}
      >
        <header className="spec-modal-head">
          <div>
            <p className="brand">Interview results</p>
            <h2 id={headingId}>{title}</h2>
          </div>
          <button className="btn ghost" type="button" onClick={onClose}>
            Close
          </button>
        </header>
        <div className="spec-modal-body">{children}</div>
      </div>
    </div>,
    document.body,
  );
}

function ServiceInterviewArtifacts({ svc }: { svc: MicroservicePlan }) {
  if (svc.workflow?.tiles?.length) {
    return (
      <>
        {svc.workflow.tiles.map((tile) => (
          <article key={tile.id} className={`artifact workflow-tile ${tile.status}`}>
            <h3>{tile.title}</h3>
            {tile.body ? (
              <div className="doc">
                <MarkdownView content={tile.body} />
              </div>
            ) : (
              <p className="lede">This step has not started.</p>
            )}
          </article>
        ))}
      </>
    );
  }
  const relations = svc.entity_relationships || svc.api_design;
  const hasAny = Boolean(
    svc.feature_spec || relations || svc.tech_stack || svc.plan_spec || svc.bug_spec,
  );
  if (!hasAny) {
    return (
      <p className="lede">
        No interview results yet. Discuss this service to agree related entities, features,
        and stack.
      </p>
    );
  }
  return (
    <>
      {relations ? (
        <article className="artifact">
          <h3>Entity relationships</h3>
          <div className="doc">
            <MarkdownView content={relations} />
          </div>
        </article>
      ) : null}
      {svc.feature_spec ? (
        <article className="artifact">
          <h3>Features</h3>
          <div className="doc">
            <MarkdownView content={svc.feature_spec} />
          </div>
        </article>
      ) : null}
      {svc.bug_spec ? (
        <article className="artifact">
          <h3>Bugs</h3>
          <div className="doc">
            <MarkdownView content={svc.bug_spec} />
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
      {svc.plan_spec ? (
        <article className="artifact">
          <h3>Plan spec</h3>
          <div className="doc">
            <MarkdownView content={svc.plan_spec} />
          </div>
        </article>
      ) : null}
    </>
  );
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
  const [specOpen, setSpecOpen] = useState(false);
  const open = svc.discussion_open && svc.status !== "suspended";
  const hasResults = Boolean(
    svc.feature_spec ||
      svc.entity_relationships ||
      svc.api_design ||
      svc.tech_stack ||
      svc.plan_spec ||
      svc.bug_spec,
  );
  const shipped = svc.status === "sent" || svc.status === "approved";
  const revisingSpec =
    svc.status === "awaiting_spec_update" || svc.status === "discussing_spec_update";
  const composerPlaceholder = shipped
    ? "Add or update features and bugs… Enter to send"
    : revisingSpec
      ? "Revise this spec increment… Enter to send"
      : "Discuss this service… Enter to send";
  const closeSpec = useCallback(() => setSpecOpen(false), []);

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
      onError(getUserFriendlyError(err));
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
      onError(getUserFriendlyError(err));
    } finally {
      onBusy(null);
    }
  }

  return (
    <section className={`panel service-tile${svc.status === "suspended" ? " suspended" : ""}`}>
      <div className="panel-head">
        <h2>{serviceLabel(svc)}</h2>
        <div className="tile-head-actions">
          <span className="panel-kicker">{svc.status}</span>
          <button
            className={`btn ghost icon-btn${hasResults ? " has-results" : ""}`}
            type="button"
            title="Interview results"
            aria-label={`View interview results for ${serviceLabel(svc)}`}
            onClick={() => setSpecOpen(true)}
          >
            <SpecIcon />
          </button>
        </div>
      </div>
      <p className="mono tile-id">{svc.microservice_id}</p>
      <WorkflowStrip workflow={svc.workflow} />
      {svc.workflow?.tiles?.length
        ? svc.workflow.tiles.map((tile) => (
            <article key={tile.id} className={`artifact workflow-tile ${tile.status}`}>
              <h3>{tile.title}</h3>
              {tile.body ? (
                <div className="doc">
                  <MarkdownView content={tile.body} />
                </div>
              ) : (
                <p className="lede">This step has not started.</p>
              )}
            </article>
          ))
        : null}
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
      </div>
      <InterviewResultsModal title={serviceLabel(svc)} open={specOpen} onClose={closeSpec}>
        <ServiceInterviewArtifacts svc={svc} />
      </InterviewResultsModal>
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
              placeholder={composerPlaceholder}
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

function GitAccessPanel({
  session,
  busy,
  onBusy,
  onUpdate,
  onError,
}: {
  session: WorkflowSession;
  busy: boolean;
  onBusy: (id: string | null) => void;
  onUpdate: (session: WorkflowSession) => void;
  onError: (msg: string) => void;
}) {
  const [repoUrl, setRepoUrl] = useState(session.git_repo_url || "");
  const [sshKey, setSshKey] = useState("");
  const [savedNote, setSavedNote] = useState<string | null>(null);

  async function onSave(e: FormEvent) {
    e.preventDefault();
    if (busy || session.finalized) return;
    onBusy("git");
    setSavedNote(null);
    onError("");
    try {
      const next = await saveGit(session.design_session_id, repoUrl.trim(), sshKey);
      onUpdate(next);
      setSshKey("");
      setSavedNote("Git repo and key saved on this session.");
    } catch (err) {
      onError(err instanceof Error ? err.message : "Something went wrong. Please try again.");
    } finally {
      onBusy(null);
    }
  }

  async function onSend() {
    if (busy || session.finalized || !session.can_send_git) return;
    onBusy("git");
    setSavedNote(null);
    onError("");
    try {
      const next = await sendGit(session.design_session_id);
      onUpdate(next);
      if (next.git_send_status !== "failed") {
        setSavedNote("Git data sent to the engineer.");
      }
    } catch (err) {
      onError(err instanceof Error ? err.message : "Something went wrong. Please try again.");
    } finally {
      onBusy(null);
    }
  }

  const sendLabel = session.git_send_status === "failed" ? "Resend to engineer" : "Send to engineer";

  return (
    <section className="panel git-panel">
      <div className="panel-head">
        <h2>Git repo</h2>
        <span className="panel-kicker">
          {session.git_send_status === "sent"
            ? "sent to engineer"
            : session.git_key_configured
              ? "key on file"
              : "not configured"}
        </span>
      </div>
      <p className="lede">
        Sub-engineers use this SSH remote to update code for this design session. The private key
        stays on the server and is never shown again after you save.
      </p>
      {session.git_send_status === "failed" && session.git_send_error ? (
        <div className="error banner" role="alert">
          <p>{session.git_send_error}</p>
        </div>
      ) : null}
      {savedNote ? <p className="lede git-ok">{savedNote}</p> : null}
      <form className="git-form" onSubmit={onSave}>
        <label className="git-field">
          <span>Repo URL (SSH)</span>
          <input
            type="text"
            value={repoUrl}
            onChange={(e) => setRepoUrl(e.target.value)}
            placeholder="git@github.com:org/repo.git"
            disabled={busy || session.finalized}
            autoComplete="off"
            spellCheck={false}
          />
        </label>
        <label className="git-field">
          <span>
            SSH private key
            {session.git_key_configured
              ? ` (on file${session.git_key_fingerprint ? ` · ${session.git_key_fingerprint}` : ""}; paste to replace)`
              : ""}
          </span>
          <textarea
            value={sshKey}
            onChange={(e) => setSshKey(e.target.value)}
            rows={5}
            placeholder={
              session.git_key_configured
                ? "Leave blank to keep the stored key, or paste a new key to replace it."
                : "-----BEGIN OPENSSH PRIVATE KEY-----"
            }
            disabled={busy || session.finalized}
            autoComplete="off"
            spellCheck={false}
          />
        </label>
        <div className="git-actions">
          <button className="btn ghost" type="submit" disabled={busy || session.finalized}>
            {busy ? "Working…" : "Save"}
          </button>
          <button
            className="btn primary"
            type="button"
            disabled={busy || session.finalized || !session.can_send_git}
            onClick={() => void onSend()}
          >
            {busy ? "Working…" : sendLabel}
          </button>
        </div>
      </form>
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
  const [retryingIngest, setRetryingIngest] = useState(false);
  const chatBusy = busyId === "session";
  const anyBusy = Boolean(busyId);

  const load = useCallback(async () => {
    const data = await getSession(sessionId);
    setSession(data);
  }, [sessionId]);

  useEffect(() => {
    load().catch((err) => setError(getUserFriendlyError(err)));
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
      setError(getUserFriendlyError(err));
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
      setError(getUserFriendlyError(err));
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
      setError(getUserFriendlyError(err));
    } finally {
      setBusyId(null);
    }
  }

  async function onRetryIngest() {
    if (!session || anyBusy) return;
    setRetryingIngest(true);
    setError(null);
    try {
      setSession(await retryIngest(session.design_session_id));
    } catch (err) {
      setError(getUserFriendlyError(err));
    } finally {
      setRetryingIngest(false);
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
          {error ? (
            <div>
              <p className="error">{error}</p>
              <button 
                className="btn ghost" 
                onClick={() => window.location.reload()}
                type="button"
              >
                Retry
              </button>
            </div>
          ) : null}
        </div>
      </div>
    );
  }

  // Check if session has ingest errors
  const hasIngestError = session.messages.some(
    msg => msg.role === "system" && msg.content.includes("encountered an error")
  );

  // Check if session has a new package notification
  const hasNewPackageNotification = session.messages.some(
    msg => msg.role === "system" && msg.content.includes("new design package was received")
  );

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
            <span className="chip">{session.workflow?.title || session.phase}</span>
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

      <GitAccessPanel
        session={session}
        busy={busyId === "git"}
        onBusy={setBusyId}
        onUpdate={setSession}
        onError={(msg) => setError(msg || null)}
      />

      {hasNewPackageNotification ? (
        <div className="info banner" role="status">
          <p>
            A new design package was received. Previous failed ingest has been replaced.
          </p>
        </div>
      ) : null}

      {hasIngestError ? (
        <div className="error banner" role="alert">
          <p>
            This session encountered an error during ingest. The package has been saved and can be retried.
          </p>
          <button 
            className="btn ghost" 
            onClick={onRetryIngest}
            disabled={retryingIngest || anyBusy}
            type="button"
          >
            {retryingIngest ? "Retrying…" : "Retry"}
          </button>
        </div>
      ) : null}

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
              {session.workflow?.tiles?.length
                ? session.workflow.tiles.map((tile) => (
                    <article key={tile.id} className={`artifact workflow-tile ${tile.status}`}>
                      <h3>{tile.title}</h3>
                      {tile.diagram ? <MermaidDiagram source={tile.diagram} /> : null}
                      {tile.body ? (
                        <div className="doc">
                          <MarkdownView content={tile.body} />
                        </div>
                      ) : (
                        <p className="lede">This step has not started.</p>
                      )}
                    </article>
                  ))
                : null}
              {!session.workflow?.tiles?.length && session.feature_spec ? (
                <article className="artifact">
                  <h3>Features</h3>
                  <div className="doc">
                    <MarkdownView content={session.feature_spec} />
                  </div>
                </article>
              ) : null}
              {!session.workflow?.tiles?.length && session.tech_stack ? (
                <article className="artifact">
                  <h3>Tech stack</h3>
                  <div className="doc">
                    <MarkdownView content={session.tech_stack} />
                  </div>
                </article>
              ) : null}
              {!session.workflow?.tiles?.length && session.plan_spec ? (
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
