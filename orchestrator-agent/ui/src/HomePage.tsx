import { useEffect, useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { ingestPackage, listSessions, type WorkflowSummary } from "./api";

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
      .catch((err) => setError(err instanceof Error ? err.message : String(err)));
  }, []);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const result = await ingestPackage(markdown);
      navigate(`/sessions/${result.design_session_id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
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
        {error ? <p className="error">{error}</p> : null}
        <button className="btn primary" type="submit" disabled={busy}>
          {busy ? "Ingesting…" : "Ingest design package"}
        </button>
      </form>
    </div>
  );
}
