import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { startDesign } from "./api";

export function HomePage() {
  const navigate = useNavigate();
  const [markdown, setMarkdown] = useState(
    "# Business Specification\n\nDescribe the software system you want to design.\n",
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const result = await startDesign(markdown);
      navigate(`/sessions/${result.design_session_id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className={`app home ${busy ? "busy" : ""}`}>
      <header className="top">
        <div>
          <p className="brand">Architect Agent</p>
          <h1>Design a system</h1>
          <p className="meta">
            Paste a business specification (or WIP design markdown). The architect will
            interview you, then propose a system design.
          </p>
        </div>
      </header>

      <form className="panel start-panel" onSubmit={onSubmit}>
        <h2>Business specification</h2>
        <textarea
          value={markdown}
          onChange={(e) => setMarkdown(e.target.value)}
          rows={18}
          required
          placeholder="# Business Specification&#10;&#10;Your idea…"
        />
        {error ? <p className="error">{error}</p> : null}
        <button className="btn primary" type="submit" disabled={busy}>
          {busy ? "Starting…" : "Start design session"}
        </button>
      </form>
    </div>
  );
}
