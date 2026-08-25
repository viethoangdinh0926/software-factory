import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { startDesign } from "./api";

const ROUND_STEPS = [
  {
    n: "0",
    title: "Phase 0 — Scope & spec",
    body: "Clarify the product, actors, v1 scope, and invariants. Classify LLD (one OS process, including a local stand-alone app) vs HLD (distributed). Product analogies like YouTube do not override an explicit stand-alone request. You confirm the classified track before design starts.",
  },
];

const LLD_STEPS = [
  {
    n: "1",
    title: "Information gathering",
    body: "Lock the in-process rules: business invariants, concurrency, and data lifecycle inside one process. This is the contract the class design must obey.",
  },
  {
    n: "2",
    title: "Architectural blueprint",
    body: "Draw the class/structure diagram: objects, interfaces, patterns (Strategy, Factory, Observer, …), and how the design meets SOLID. Each box is a type in the single process, not a network service.",
  },
  {
    n: "3",
    title: "Verification",
    body: "Walk the blueprint against the spec invariants. Iterate until the structure is complete enough to approve as a design version.",
  },
];

const HLD_STEPS = [
  {
    n: "1",
    title: "Requirements & capacity estimation",
    body: "Functional journeys plus NFRs (availability, latency, durability, security). Propose scale numbers (DAU, peak concurrency, ingest, storage growth) and have you confirm or correct them.",
  },
  {
    n: "2",
    title: "Domain object modeling",
    body: "Name the core entities, attributes, and relationships (1:1, 1:N, N:M), and who owns each object. This catalog is the input to service splits — not a class diagram.",
  },
  {
    n: "3",
    title: "Core microservices",
    body: "Split owned objects into bounded-context services. Each service lists owned objects, operations, and collaborators. No HTTP METHOD/path catalogs — protocols come next.",
  },
  {
    n: "4",
    title: "Communication, infrastructure & system diagram",
    body: "Name how clients, services, and infra talk (REST, gRPC, WebSocket, Kafka, …), pick gateways, stores, caches, brokers, and CAP/PACELC trade-offs, then draw the system diagram (clients → gateway → services → stores/CDN).",
  },
  {
    n: "5",
    title: "Vulnerability & edge-case analysis (FMEA)",
    body: "Structured failure modes: SPOFs, bottlenecks, races, split-brain. Each row has impact and mitigation, checked against the NFRs and CAP choices.",
  },
  {
    n: "6",
    title: "Session synthesis & wrap-up",
    body: "Architectural summary: stack, residual risks, and a walkthrough of every diagram box. Confirm this version to run market evaluation.",
  },
];

const AFTER_TRACK = [
  {
    n: "M",
    title: "Market evaluation",
    body: "After you approve a full design version, the architect grades the idea against alternatives and writes a market report.",
  },
  {
    n: "H",
    title: "Handoff, then a new round",
    body: "The package is sent to the Orchestrator only if the design changed since the last delivery (success or failure). The session then returns to Phase 0 so you can update the spec or walk the track again.",
  },
];

const SPEC_PLACEHOLDER =
  "# Business Specification\n\nDescribe the software system you want to design.";

export function HomePage() {
  const navigate = useNavigate();
  const [markdown, setMarkdown] = useState("");
  const [showPlaceholder, setShowPlaceholder] = useState(true);
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
          {ROUND_STEPS.map((step) => (
            <li key={step.n}>
              <span className="flow-index">{step.n}</span>
              <div>
                <strong>{step.title}</strong>
                <p>{step.body}</p>
              </div>
            </li>
          ))}
        </ol>

        <div className="track-grid">
          <article className="track-card" aria-labelledby="lld-heading">
            <h3 id="lld-heading">LLD — one process</h3>
            <p className="track-lede">
              Chosen when the system is a local, self-contained, desktop, CLI, or
              single-host monolith. Three steps; each must be confirmed before the next.
            </p>
            <ol className="track-steps">
              {LLD_STEPS.map((step) => (
                <li key={step.n}>
                  <span className="flow-index">{step.n}</span>
                  <div>
                    <strong>{step.title}</strong>
                    <p>{step.body}</p>
                  </div>
                </li>
              ))}
            </ol>
          </article>
          <article className="track-card" aria-labelledby="hld-heading">
            <h3 id="hld-heading">HLD — distributed</h3>
            <p className="track-lede">
              Chosen when you want microservices, multi-node storage, a gateway, CDN,
              or other network topology. Six steps; LLD does not precede this track.
            </p>
            <ol className="track-steps">
              {HLD_STEPS.map((step) => (
                <li key={step.n}>
                  <span className="flow-index">{step.n}</span>
                  <div>
                    <strong>{step.title}</strong>
                    <p>{step.body}</p>
                  </div>
                </li>
              ))}
            </ol>
          </article>
        </div>

        <ol className="flow-steps">
          {AFTER_TRACK.map((step) => (
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
            You cannot jump ahead. Each phase and track step must be confirmed
            before the next begins.
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

      <form
        className="panel start-panel"
        onSubmit={onSubmit}
        onMouseDown={(e) => {
          const target = e.target as HTMLElement;
          if (target.closest("textarea, button, a")) return;
          const field = e.currentTarget.querySelector("textarea");
          if (field && document.activeElement !== field) {
            e.preventDefault();
            field.focus();
          }
        }}
      >
        <div className="panel-head">
          <h2>Business specification</h2>
          <span className="panel-kicker">Markdown</span>
        </div>
        <textarea
          value={markdown}
          onChange={(e) => setMarkdown(e.target.value)}
          onMouseDown={() => setShowPlaceholder(false)}
          onFocus={() => setShowPlaceholder(false)}
          onBlur={() => {
            if (!markdown.trim()) setShowPlaceholder(true);
          }}
          rows={18}
          required
          disabled={busy}
          placeholder={showPlaceholder ? SPEC_PLACEHOLDER : ""}
        />
        {error ? <p className="error">{error}</p> : null}
        <button className="btn primary" type="submit" disabled={busy}>
          {busy ? "Opening atelier…" : "Start design session"}
        </button>
      </form>
    </div>
  );
}
