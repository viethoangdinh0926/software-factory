import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { startDesign } from "./api";

const FLOW_STEPS = [
  {
    n: "0",
    title: "Phase 0 — Scope & spec",
    body: "Clarify the product, actors, v1 scope, and invariants. Classify LLD (one process) vs HLD (distributed).",
  },
  {
    n: "1–N",
    title: "Design track, in order",
    body: "LLD has 3 steps (rules, blueprint, verify). HLD has 6 (capacity, domain objects, services, communication & diagram, FMEA, synthesis). Confirm each step before the next.",
  },
  {
    n: "M",
    title: "Market evaluation",
    body: "After you approve a full design version, the architect grades the idea against alternatives.",
  },
  {
    n: "H",
    title: "Handoff, then a new round",
    body: "The package is sent to the Orchestrator only if the design changed since the last delivery (success or failure). The session then returns to Phase 0 so you can update the spec or walk the track again.",
  },
];

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
            Paste a business specification. The principal architect walks a fixed
            design flow with you — one confirmed stage at a time — then hands a
            package to the Orchestrator.
          </p>
        </div>
      </header>

      <section className="panel flow-guide" aria-labelledby="flow-heading">
        <div className="panel-head">
          <h2 id="flow-heading">How a design round works</h2>
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
            You cannot jump ahead. Each phase must be confirmed before the next
            begins.
          </li>
          <li>
            If a change belongs to an earlier phase — for example a new spec
            requirement while modeling domain objects — the architect returns
            there and walks forward again. Later-step work stays; it is patched
            only where that change requires it.
          </li>
          <li>
            A package is sent to the Orchestrator only when the design changed
            since the last delivery. Unchanged rounds stay at Phase 0.
          </li>
          <li>
            Confirm, approve, or agree in chat, or use the UI action. Either
            works.
          </li>
        </ul>
      </section>

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
