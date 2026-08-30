import { useCallback, useEffect, useId, useState, type FormEvent, type KeyboardEvent, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { Link, useParams } from "react-router-dom";
import {
  approve,
  chat,
  execute,
  getSession,
  packageDownloadUrl,
  pause,
  subLabel,
  type ExecutionPlan,
  type FleetSession,
  type PlanItem,
  type SubEngineer,
} from "./api";
import { MarkdownView } from "./MarkdownView";
import { sessionHolderId, useSessionPresence } from "./sessionPresence";

function getUserFriendlyError(err: unknown) {
  if (err instanceof Error && err.message.trim()) return err.message;
  return "Something went wrong. Please try again.";
}

function onComposerKey(e: KeyboardEvent<HTMLTextAreaElement>) {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    e.currentTarget.form?.requestSubmit();
  }
}

function sortedItems(plan?: ExecutionPlan): PlanItem[] {
  const items = plan?.items || [];
  return [...items].sort((a, b) => (a.priority || 0) - (b.priority || 0) || a.id.localeCompare(b.id));
}

function composerPlaceholder(sub: SubEngineer): string {
  if (sub.status === "executing") {
    return "Stop/resume/undo this item, pause the plan, or ask a question…";
  }
  if (sub.status === "paused") return "Revise the plan, then execute it…";
  if (sub.status === "blocked" && sub.block_issue?.kind === "pi_questions") {
    return "Answer Pi's questions so it can continue…";
  }
  if (sub.status === "blocked") return "Tell me how to resolve this issue, then approve to continue…";
  if (sub.status === "shipped") return "Ask a follow-up, or wait for the next spec…";
  return "Revise the execution plan, or ask about a feature…";
}

function StatusIcon() {
  return (
    <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true" focusable="false">
      <path
        fill="currentColor"
        d="M7 3.5h7.2L19.5 9v11.5A1.5 1.5 0 0 1 18 22H7a1.5 1.5 0 0 1-1.5-1.5v-16A1.5 1.5 0 0 1 7 3.5Zm6.5 1.2v5.3h5.1l-5.1-5.3ZM8.5 12.25h7v1.4h-7v-1.4Zm0 3.1h7v1.4h-7v-1.4Zm0 3.1h4.5v1.4H8.5v-1.4Z"
      />
    </svg>
  );
}

function ImplementationStatusModal({
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
    <div className="spec-modal-backdrop" role="presentation" onClick={onClose}>
      <div
        className="spec-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby={headingId}
        onClick={(e) => e.stopPropagation()}
      >
        <header className="spec-modal-head">
          <div>
            <p className="brand">Implementation status</p>
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

function PlanList({ plan, locked }: { plan?: ExecutionPlan; locked: boolean }) {
  const items = sortedItems(plan);
  if (!items.length) return null;
  return (
    <article className="artifact">
      <h3>Execution plan{locked ? " (locked)" : ""}</h3>
      {plan?.summary ? <p className="lede">{plan.summary}</p> : null}
      {plan?.transition ? (
        <p className="lede">
          <strong>Transition:</strong> {plan.transition}
        </p>
      ) : null}
      <ol className="plan-list">
        {items.map((item) => (
          <li key={item.id} className={`plan-item status-${item.status}`}>
            <div className="plan-item-head">
              <span className="plan-priority">P{item.priority}</span>
              <span className="plan-kind">{item.kind}</span>
              <strong>{item.title}</strong>
              <span className="plan-status">{item.status}</span>
            </div>
            {item.depends_on?.length ? (
              <p className="lede">Depends on: {item.depends_on.join(", ")}</p>
            ) : null}
            {item.peer_services?.length ? (
              <p className="lede">Consult: {item.peer_services.join(", ")}</p>
            ) : null}
            {item.notes ? <p className="lede">{item.notes}</p> : null}
          </li>
        ))}
      </ol>
    </article>
  );
}

function SubTile({
  sessionId,
  sub,
  busy,
  readOnly,
  onBusy,
  onUpdate,
  onError,
}: {
  sessionId: string;
  sub: SubEngineer;
  busy: boolean;
  readOnly: boolean;
  onBusy: (id: string | null) => void;
  onUpdate: (session: FleetSession) => void;
  onError: (msg: string) => void;
}) {
  const [message, setMessage] = useState("");
  const [pending, setPending] = useState<string | null>(null);
  const [statusOpen, setStatusOpen] = useState(false);
  const open = sub.discussion_open && sub.status !== "suspended";
  const statusDoc = (sub.implementation_status || "").trim();

  async function onChat(e: FormEvent) {
    e.preventDefault();
    const text = message.trim();
    if (!text || busy || readOnly || !open) return;
    onBusy(sub.microservice_id);
    setPending(text);
    setMessage("");
    try {
      onUpdate(await chat(sessionId, text, sub.microservice_id));
    } catch (err) {
      onError(getUserFriendlyError(err));
    } finally {
      setPending(null);
      onBusy(null);
    }
  }

  async function run(action: () => Promise<FleetSession>) {
    if (busy || readOnly) return;
    onBusy(sub.microservice_id);
    try {
      onUpdate(await action());
    } catch (err) {
      onError(getUserFriendlyError(err));
    } finally {
      onBusy(null);
    }
  }

  const initiators = (sub.peer_consults || []).filter((c) => c.we_initiate);

  return (
    <section
      className={`panel service-tile${sub.status === "suspended" ? " suspended" : ""}${
        sub.status === "blocked" ? " blocked" : ""
      }`}
    >
      <div className="panel-head">
        <h2>{subLabel(sub)}</h2>
        <div className="tile-head-actions">
          <span className="panel-kicker">{sub.workflow?.title || sub.status}</span>
          <button
            className={`btn ghost icon-btn${statusDoc ? " has-results" : ""}`}
            type="button"
            title="Implementation status"
            aria-label={`View implementation status for ${subLabel(sub)}`}
            disabled={!statusDoc}
            onClick={() => setStatusOpen(true)}
          >
            <StatusIcon />
          </button>
        </div>
      </div>
      <ImplementationStatusModal
        title={subLabel(sub)}
        open={statusOpen}
        onClose={() => setStatusOpen(false)}
      >
        {statusDoc ? (
          <div className="doc">
            <MarkdownView content={statusDoc} />
          </div>
        ) : (
          <p className="lede">No implementation status yet. It appears after Pi finishes an item.</p>
        )}
      </ImplementationStatusModal>
      <p className="lede mono">{sub.sub_agent_id}</p>
      {sub.workflow?.tiles?.length ? (
        <>
          <nav className="workflow-rail" aria-label="Engineer workflow">
            {sub.workflow.tiles.map((tile) => (
              <a key={tile.id} className={`workflow-rail-item ${tile.status}`} href={`#${sub.sub_agent_id}-${tile.id}`}>
                {tile.title}
              </a>
            ))}
          </nav>
          {sub.workflow.tiles.map((tile) => (
            <article
              key={tile.id}
              id={`${sub.sub_agent_id}-${tile.id}`}
              className={`artifact workflow-tile ${tile.status}`}
            >
              <div className="panel-head">
                <h3>{tile.title}</h3>
                <span className="panel-kicker">{tile.status}</span>
              </div>
              {tile.id === "plan" ? (
                <PlanList plan={sub.execution_plan} locked={Boolean(sub.plan_locked)} />
              ) : tile.body ? (
                <div className="doc">
                  <MarkdownView content={tile.body} />
                </div>
              ) : (
                <p className="lede">This step has not started.</p>
              )}
            </article>
          ))}
        </>
      ) : (
        <>
          <PlanList plan={sub.execution_plan} locked={Boolean(sub.plan_locked)} />
          {sub.offered_api ? (
            <article className="artifact">
              <h3>Offered API</h3>
              <div className="doc">
                <MarkdownView content={sub.offered_api} />
              </div>
            </article>
          ) : null}
        </>
      )}
      {sub.status === "blocked" && sub.block_issue ? (
        <div className="error banner block-issue" role="alert">
          <div>
            <p>
              <strong>{sub.block_issue.title}</strong>
            </p>
            <p className="lede">{sub.block_issue.detail}</p>
            {sub.block_issue.item_title ? (
              <p className="lede">
                {sub.block_issue.kind === "pi_questions" ? "Waiting item: " : "Paused item: "}
                {sub.block_issue.item_title}
              </p>
            ) : null}
            {sub.block_issue.instructions ? (
              <p className="lede">
                <strong>Your instructions:</strong> {sub.block_issue.instructions}
              </p>
            ) : null}
          </div>
        </div>
      ) : null}
      {sub.git_ship_status ? (
        <p className="lede">
          Git ship: <span className="mono">{sub.git_ship_status}</span>
        </p>
      ) : null}
      {initiators.length ? (
        <p className="lede">
          Initiates toward: {initiators.map((c) => `${c.peer_name} (${c.status})`).join(", ")}
        </p>
      ) : (
        <p className="lede">Does not initiate toward other core microservices.</p>
      )}
      {initiators.some((c) => c.offered_api) ? (
        <article className="artifact">
          <h3>Consulted peer APIs</h3>
          {initiators.map((c) =>
            c.offered_api ? (
              <div className="doc" key={c.peer_sub_agent_id || c.peer_name}>
                <p>
                  <strong>{c.peer_name}</strong>
                </p>
                <MarkdownView content={c.offered_api} />
              </div>
            ) : null,
          )}
        </article>
      ) : null}
      <div className="chat-log">
        {(sub.messages || []).slice(-12).map((msg, i) => (
          <div key={`${msg.role}-${i}`} className={`bubble ${msg.role}`}>
            <MarkdownView content={msg.content} />
          </div>
        ))}
        {pending ? (
          <div className="bubble user pending">
            <MarkdownView content={pending} />
          </div>
        ) : null}
      </div>
      {open ? (
        <form className="composer" onSubmit={onChat}>
          <textarea
            value={message}
            onChange={(e) => setMessage(e.target.value)}
            onKeyDown={onComposerKey}
            rows={3}
            placeholder={composerPlaceholder(sub)}
            disabled={busy || readOnly}
          />
          <div className="composer-actions">
            {sub.can_approve ? (
              <button className="btn" type="button" onClick={() => void run(() => approve(sessionId, sub.microservice_id))} disabled={busy || readOnly}>
                {sub.approve_label || "Approve plan"}
              </button>
            ) : null}
            {sub.can_pause ? (
              <button className="btn" type="button" onClick={() => void run(() => pause(sessionId, sub.microservice_id))} disabled={busy || readOnly}>
                Pause
              </button>
            ) : null}
            {sub.can_execute ? (
              <button className="btn" type="button" onClick={() => void run(() => execute(sessionId, sub.microservice_id))} disabled={busy || readOnly}>
                Execute plan
              </button>
            ) : null}
            <button className="btn ghost" type="submit" disabled={busy || readOnly || !message.trim()}>
              Send
            </button>
          </div>
        </form>
      ) : (
        <p className="lede">This sub-engineer is suspended.</p>
      )}
    </section>
  );
}

export function SessionPage() {
  const { sessionId = "" } = useParams();
  const [session, setSession] = useState<FleetSession | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const presence = useSessionPresence(sessionId);
  const viewOnly = !presence.interactive;

  const load = useCallback(async () => {
    try {
      setSession(await getSession(sessionId));
    } catch (err) {
      setError(getUserFriendlyError(err));
    }
  }, [sessionId]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (!sessionId) return;
    const source = new EventSource(`/api/sessions/${sessionId}/events`);
    source.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data) as FleetSession;
        if (data?.design_session_id) {
          const lease = data.interaction?.holder_id || "";
          const mine = sessionHolderId(sessionId);
          setSession((prev) => ({
            ...prev,
            ...data,
            sub_agents: data.sub_agents ?? prev?.sub_agents ?? [],
            interaction: {
              holder_id: lease,
              is_holder: Boolean(lease) && lease === mine,
              interactive: Boolean(lease) && lease === mine,
              locked: Boolean(lease) && lease !== mine,
            },
          }));
        }
      } catch {
        /* ignore a malformed frame */
      }
    };
    return () => source.close();
  }, [sessionId]);

  if (!session) {
    const notFound = Boolean(error && /not found/i.test(error));
    return (
      <div className="app">
        <div className="atmosphere" aria-hidden />
        <div className="state-card">
          <p className="brand">Engineer Agent</p>
          <h1>{notFound ? "Session not found" : error ? "Session unavailable" : "Loading fleet…"}</h1>
          {error ? (
            <>
              <p className="error">
                {notFound ? `No fleet matches ${sessionId || "this URL"}.` : error}
              </p>
              <Link className="btn ghost" to="/">
                All fleets
              </Link>
            </>
          ) : (
            <div className="loading-line" aria-hidden />
          )}
        </div>
      </div>
    );
  }

  return (
    <div className="app session-app">
      <div className="atmosphere" aria-hidden />
      <header className="top">
        <div className="top-copy">
          <p className="brand">Engineer Agent</p>
          <h1>Sub-engineer fleet</h1>
          <p className="lede mono">{session.design_session_id}</p>
          {session.git_key_configured ? (
            <p className="lede">
              Git remote for this fleet: <span className="mono">{session.git_repo_url}</span>
            </p>
          ) : (
            <p className="lede">No git repo yet. Send SSH access from the orchestrator session.</p>
          )}
        </div>
        <div className="actions">
          <Link className="btn ghost" to="/">
            All fleets
          </Link>
          <a className="btn ghost" href={packageDownloadUrl(session.design_session_id)}>
            Download package
          </a>
        </div>
      </header>
      {viewOnly ? (
        <p className="view-only banner" role="status">
          Someone else is working on this session. You can watch progress; controls unlock when they close the tab.
        </p>
      ) : null}
      {error ? (
        <div className="error banner" role="alert">
          <p>{error}</p>
          <button className="btn ghost" type="button" onClick={() => setError(null)}>
            Dismiss
          </button>
        </div>
      ) : null}
      <div className="service-grid">
        {(session.sub_agents || []).map((sub) => (
          <SubTile
            key={sub.sub_agent_id}
            sessionId={session.design_session_id}
            sub={sub}
            busy={busyId !== null}
            readOnly={viewOnly}
            onBusy={setBusyId}
            onUpdate={setSession}
            onError={setError}
          />
        ))}
      </div>
    </div>
  );
}
