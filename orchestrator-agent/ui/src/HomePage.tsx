import { useEffect, useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { ingestPackage, listSessions, type WorkflowSummary } from "./api";

// Generic error message handler - provides user-friendly messages without exposing backend details
function getUserFriendlyError(_err: unknown): string {
  // Always return a generic message regardless of the actual error
  return "Something went wrong. Please try again.";
}

const FLOW_STEPS = [
  {
    n: "1",
    title: "Ingest the design package",
    body: "Packages arrive from the architect (same design session ID) or you can paste one here to test.",
  },
  {
    n: "2",
    title: "Plan each core microservice",
    body: "Distributed designs open a tile per service. Confirm related entities, then features, then stack. The first approve ships spec version 1 to the Engineer.",
  },
  {
    n: "3",
    title: "Incremental spec updates",
    body: "After that first ship, add or update features and bugs on the same tile and ship a new spec version. The agreed entity map and stack stay.",
  },
  {
    n: "4",
    title: "Full update after a new package",
    body: "Walking every planning phase again (relationships, features, stack) starts only when the architect ships a new design package.",
  },
];

export function HomePage() {
  const navigate = useNavigate();
  const [sessions, setSessions] = useState<WorkflowSummary[]>([]);
  const [markdown, setMarkdown] = useState(
    "# System Design Package\n\nDesign session: `00000000-0000-0000-0000-000000000000`\nDesign version: `1`\nTrack: `hld` step `6`\n",
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
          <p className="brand">Orchestrator Agent</p>
          <h1>Plan the build from an architect package.</h1>
          <p className="lede">
            Workflows use the same design session ID as the architect. Packages arrive via A2A,
            or paste one here to test.
          </p>
        </div>
      </header>

      <section className="panel flow-guide" aria-labelledby="flow-heading">
        <div className="panel-head">
          <h2 id="flow-heading">How a planning round works</h2>
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
          <li>
            After the first engineer ship, keep the tile open for feature and bug
            spec updates. Ship those as new spec versions.
          </li>
          <li>
            A full update of a core microservice — every working phase — waits for
            a new architect design package.
          </li>
          <li>
            Stand-alone apps lock after the first handoff until the architect sends
            an updated package.
          </li>
          <li>
            Please approve in chat if you have no other concerns, or use the UI action. Either works.
          </li>
        </ul>
      </section>

      {error ? (
        <div className="error banner" role="alert">
          <p>{error}</p>
          <button
            className="btn ghost"
            onClick={() => setError(null)}
            type="button"
          >
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
                v{row.design_version} · {row.architect_track} · {row.topology} · {row.phase}
              </span>
            </Link>
          ))}
        </section>
      ) : (
        <p className="lede">No workflows yet.</p>
      )}

      <form className="panel start-panel" onSubmit={onSubmit}>
        <div className="panel-head">
          <h2>Design package</h2>
          <span className="panel-kicker">Markdown</span>
        </div>
        <textarea
          value={markdown}
          onChange={(e) => setMarkdown(e.target.value)}
          rows={16}
          required
          disabled={busy}
        />
        <button className="btn primary" type="submit" disabled={busy}>
          {busy ? "Ingesting…" : "Ingest design package"}
        </button>
      </form>
    </div>
  );
}
