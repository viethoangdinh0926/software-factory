import { useEffect, useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { ingestPackage, listSessions, type FleetSummary } from "./api";

function getUserFriendlyError(_err: unknown): string {
  return "Something went wrong. Please try again.";
}

const FLOW_STEPS = [
  {
    n: "1",
    title: "Ingest a plan spec",
    body: "Each core microservice gets its own sub-engineer tile. The sub-engineer drafts an offered API and an execution plan in priority order.",
  },
  {
    n: "2",
    title: "Approve the plan",
    body: "Confirm the plan in chat or the UI. Coding starts only after that. Pause later if you need to revise, then execute the updated plan.",
  },
  {
    n: "3",
    title: "One item at a time",
    body: "The sub-engineer follows the plan closely: one feature update, new feature, or bug at a time. It adds tests and moves on only when every workspace test passes.",
  },
  {
    n: "4",
    title: "Pause on issues",
    body: "If git pull fails, a peer contract cannot be settled, or tests fail, development pauses and the tile updates in real time. Chat with instructions, then approve to continue.",
  },
];

export function HomePage() {
  const navigate = useNavigate();
  const [sessions, setSessions] = useState<FleetSummary[]>([]);
  const [markdown, setMarkdown] = useState(
    "# Plan spec\n\n- action: `plan`\n- design_session_id: `00000000-0000-0000-0000-000000000000`\n- design_version: `1`\n- microservice_id: `svc-1`\n- microservice_name: `IdentityService`\n\n## Entity relationships\n\n### User (kind: user)\n- We initiate: no\n",
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    listSessions()
      .then(setSessions)
      .catch((err) => setError(getUserFriendlyError(err)));
  }, []);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const result = await ingestPackage(markdown);
      navigate(`/sessions/${result.design_session_id}`);
    } catch (err) {
      setError(getUserFriendlyError(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="app home">
      <div className="atmosphere" aria-hidden />
      <header className="top home-hero">
        <div className="top-copy">
          <p className="brand">Engineer Agent</p>
          <h1>Run a sub-engineer fleet from a plan spec.</h1>
          <p className="lede">
            Each tile is one sub-engineer: design session ID plus microservice ID. Specs arrive via
            A2A, or paste one here to test.
          </p>
        </div>
      </header>

      <section className="panel flow-guide" aria-labelledby="flow-heading">
        <div className="panel-head">
          <h2 id="flow-heading">How a sub-engineer codes a service</h2>
          <span className="panel-kicker">In order</span>
        </div>
        <ol className="flow-steps">
          {FLOW_STEPS.map((step) => (
            <li key={step.n}>
              <span className="flow-index">{step.n}</span>
              <div>
                <strong>{step.title}</strong>
                <p>{step.body}</p>
              </div>
            </li>
          ))}
        </ol>
        <ul className="flow-rules">
          <li>Follow the execution plan in the listed priority order. Do not skip ahead.</li>
          <li>
            Pause-and-revise is separate from a blocker: Execute plan resumes after you rewrite the
            plan; Approve to continue resumes after an issue is resolved and follows any
            instructions you chatted.
          </li>
          <li>
            The fleet page streams status as it happens. You do not need to refresh to see a
            blocker.
          </li>
          <li>Confirm, approve, or agree in chat, or use the UI action. Either works.</li>
        </ul>
      </section>

      {error ? (
        <div className="error banner" role="alert">
          <p>{error}</p>
          <button className="btn ghost" onClick={() => setError(null)} type="button">
            Dismiss
          </button>
        </div>
      ) : null}

      {sessions.length ? (
        <section className="workflow-list">
          {sessions.map((row) => (
            <Link key={row.design_session_id} className="workflow-row" to={row.ui_path}>
              <span className="mono">{row.design_session_id}</span>
              <span>
                v{row.design_version} · {row.sub_agent_count} sub-engineers
              </span>
            </Link>
          ))}
        </section>
      ) : (
        <p className="lede">No fleets yet.</p>
      )}

      <form className="panel" onSubmit={onSubmit}>
        <h2>Ingest plan spec</h2>
        <textarea
          value={markdown}
          onChange={(e) => setMarkdown(e.target.value)}
          rows={14}
          spellCheck={false}
        />
        <button className="btn" type="submit" disabled={busy}>
          {busy ? "Ingesting…" : "Open fleet"}
        </button>
      </form>
    </div>
  );
}
