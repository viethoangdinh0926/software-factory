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
    <div className="app home">
      <div className="atmosphere" aria-hidden />
      <header className="top home-hero">
        <div className="top-copy">
          <p className="brand">Architect Agent</p>
          <h1>Shape the system before the build.</h1>
          <p className="lede">
            Paste a business specification. The architect interviews you, then drafts a living
            design you can revise and hand off.
          </p>
        </div>
      </header>

      <form className="panel start-panel" onSubmit={onSubmit}>
        <div className="panel-head">
          <h2>Business specification</h2>
          <span className="panel-kicker">Markdown</span>
        </div>
        <textarea
          value={markdown}
          onChange={(e) => setMarkdown(e.target.value)}
          rows={18}
          required
          disabled={busy}
          placeholder="# Business Specification&#10;&#10;Your idea…"
        />
        {error ? <p className="error">{error}</p> : null}
        <button className="btn primary" type="submit" disabled={busy}>
          {busy ? "Opening atelier…" : "Start design session"}
        </button>
      </form>
    </div>
  );
}
