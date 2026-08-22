import { useCallback, useEffect, useState, type FormEvent, type KeyboardEvent } from "react";
import { Link, useParams } from "react-router-dom";
import {
  approve,
  chat,
  execute,
  getSession,
  pause,
  subLabel,
  type ExecutionPlan,
  type FleetSession,
  type PlanItem,
  type SubEngineer,
} from "./api";
import { MarkdownView } from "./MarkdownView";

function getUserFriendlyError(_err: unknown) {
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
  if (sub.status === "executing") return "Pause to update the plan, or ask a question…";
  if (sub.status === "paused") return "Revise the plan, then execute it…";
  if (sub.status === "shipped") return "Ask a follow-up, or wait for the next spec…";
  return "Revise the execution plan, or ask about a feature…";
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
  onBusy,
  onUpdate,
  onError,
}: {
  sessionId: string;
  sub: SubEngineer;
  busy: boolean;
  onBusy: (id: string | null) => void;
  onUpdate: (session: FleetSession) => void;
  onError: (msg: string) => void;
}) {
  const [message, setMessage] = useState("");
  const [pending, setPending] = useState<string | null>(null);
  const open = sub.discussion_open && sub.status !== "suspended";

  async function onChat(e: FormEvent) {
    e.preventDefault();
    const text = message.trim();
    if (!text || busy || !open) return;
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
    if (busy) return;
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
    <section className={`panel service-tile${sub.status === "suspended" ? " suspended" : ""}`}>
      <div className="panel-head">
        <h2>{subLabel(sub)}</h2>
        <span className="panel-kicker">{sub.status}</span>
      </div>
      <p className="lede mono">{sub.sub_agent_id}</p>
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
      <PlanList plan={sub.execution_plan} locked={Boolean(sub.plan_locked)} />
      {sub.offered_api ? (
        <article className="artifact">
          <h3>Offered API</h3>
          <div className="doc">
            <MarkdownView content={sub.offered_api} />
          </div>
        </article>
      ) : null}
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
        {(sub.messages || []).slice(-8).map((msg, i) => (
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
            disabled={busy}
          />
          <div className="composer-actions">
            {sub.can_approve ? (
              <button className="btn" type="button" onClick={() => void run(() => approve(sessionId, sub.microservice_id))} disabled={busy}>
                {sub.approve_label || "Approve plan"}
              </button>
            ) : null}
            {sub.can_pause ? (
              <button className="btn" type="button" onClick={() => void run(() => pause(sessionId, sub.microservice_id))} disabled={busy}>
                Pause
              </button>
            ) : null}
            {sub.can_execute ? (
              <button className="btn" type="button" onClick={() => void run(() => execute(sessionId, sub.microservice_id))} disabled={busy}>
                Execute plan
              </button>
            ) : null}
            <button className="btn ghost" type="submit" disabled={busy || !message.trim()}>
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

  const executing = Boolean(session?.sub_agents.some((sub) => sub.status === "executing"));
  useEffect(() => {
    if (!executing) return;
    const timer = window.setInterval(() => {
      void load();
    }, 2000);
    return () => window.clearInterval(timer);
  }, [executing, load]);

  if (!session) {
    return (
      <div className="app">
        <p className="lede">{error || "Loading fleet…"}</p>
      </div>
    );
  }

  return (
    <div className="app">
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
        <Link className="btn ghost" to="/">
          All fleets
        </Link>
      </header>
      {error ? (
        <div className="error banner" role="alert">
          <p>{error}</p>
          <button className="btn ghost" type="button" onClick={() => setError(null)}>
            Dismiss
          </button>
        </div>
      ) : null}
      <div className="service-grid">
        {session.sub_agents.map((sub) => (
          <SubTile
            key={sub.sub_agent_id}
            sessionId={session.design_session_id}
            sub={sub}
            busy={busyId !== null}
            onBusy={setBusyId}
            onUpdate={setSession}
            onError={setError}
          />
        ))}
      </div>
    </div>
  );
}
