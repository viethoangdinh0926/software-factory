from __future__ import annotations

import json
import logging
from typing import Any, Iterable

from architect_agent.config import get_settings
from architect_agent.json_util import parse_llm_json_object

logger = logging.getLogger(__name__)

# Approx chars-per-token for English/markdown budgeting (conservative).
_CHARS_PER_TOKEN = 4

# Compact digests — keep interview technique + principal-architect workflow without
# shipping full skill files on every turn. Full skills remain on disk for humans/docs.

# Every user-visible chat message must read like a Principal Architect briefing a peer:
# what changed, why, what was rejected, and what it costs. Terse status lines are a
# failed turn. Included in JSON_OUTPUT_DIGEST + PRINCIPAL_ARCHITECT_DIGEST so every
# node that builds a prompt from either digest inherits the same depth bar.
EXPLANATION_DEPTH_DIGEST = """
CHAT DEPTH (assistant_message) — write like a Principal Architect briefing a peer:
- Target 200-500 words of substance. NEVER a bare status line such as "Design updated.",
  "Here is the diagram." or "Step 3 complete." A recap with no reasoning is a FAILED turn.
- Every time you propose or change something (a diagram, topology, service split, tech stack,
  capacity number, communication scheme, failure mitigation), justify it by covering:
  1. WHAT you produced or changed, naming the concrete elements — actual service names,
     owned objects, protocols, stores, queues, diagram nodes — not "the design".
  2. WHY this is the right call: the driving forces from the spec/artifacts (scale, latency
     budget, consistency need, read/write ratio, cost, team size, compliance) that force it.
  3. ALTERNATIVES considered and the explicit reason each was rejected
     (e.g. "GraphQL rejected: one mobile client, no field-selection pressure to justify
     resolver complexity and N+1 risk").
  4. TRADE-OFFS accepted — what this choice costs (operational burden, eventual-consistency
     window, hot partitions, vendor lock-in, added hop latency) and why that cost is worth it.
  5. ASSUMPTIONS you had to make, each labeled, with the default you already wrote in.
  6. IMPLICATIONS for later steps so the user sees where the decision propagates.
- Teach while you decide. Name the pattern, principle or law you are applying (CAP/PACELC,
  CQRS, saga, outbox, idempotency keys, backpressure, bulkhead, cell-based isolation,
  SOLID, Little's Law) and explain in one clause what it buys HERE. Quote concrete numbers
  from the artifacts whenever they exist.
- Use tight markdown structure (bold lead-ins, short bullets) so it stays scannable.
  Depth means information density, NOT padding — no filler, no restating the question.
- Still at most ONE ❓ question, and still invite Approve once the artifact is ready.
  Elaboration replaces terseness; it does not replace the approve flow.
""".strip()

_JSON_OUTPUT_RULES = """
OUTPUT FORMAT (non-negotiable):
- Reply with ONE JSON object. The first non-whitespace character MUST be `{`.
- No markdown essays, no # headings, no ``` fences, no bare Mermaid outside JSON.
- Put Mermaid only in design_diagram_lines as an array of short strings
  (one statement per element: "flowchart LR", "  Client --> GW[API Gateway]", ...).
- Escape newlines in strings as \\n (this includes the markdown inside assistant_message).
- Do not use LaTeX. Never write $\\text{...}$, $\\approx$, or other $...$ math. Use
  plain words or unicode (≈, ×, ≤).
- assistant_message: an elaborated, knowledgeable justification per CHAT DEPTH below,
  then invite Approve. At most ONE ❓ question, and only if a decision would change
  architecture.
- Exception: if this turn is answering a user question (not rewriting artifacts),
  assistant_message is the full answer. Do not invite Approve in place of the answer.
""".strip()

# Concatenated (not f-string) because the rules text contains literal `{` / `}`.
JSON_OUTPUT_DIGEST = _JSON_OUTPUT_RULES + "\n\n" + EXPLANATION_DEPTH_DIGEST

INTERVIEW_TECHNIQUE_DIGEST = """
Effortless interview (grill-me, low friction):
1. Do the work for the interviewer. On every turn, WRITE the current step's primary
   artifact in full using explicit labeled assumptions. Do not stall waiting for numbers.
2. Ask at most ONE question per turn, and only if the answer would change a major
   boundary (LLD vs HLD, consistency vs availability, monolith vs services).
3. Always include a (Recommended) default. Treat silence / "ok" / Approve as accepting it.
4. NEVER repeat or rephrase a question already asked. If they did not answer, keep the
   recommended default in the artifact and invite Approve.
5. Capture decisions in living artifacts (spec, ledger, scale, core microservices,
   communication schemes, FMEA, diagram) —
   they stay the source of truth. Chat is not the design document, but it IS the
   architect's reasoning: explain what you wrote, why, what you rejected, and what it
   costs. Never let chat degrade into a one-line "done" pointer at the artifact.
6. After the last Approve ask: answer queries, apply concerns/comments to the artifact,
   then state **Updates to this proposal** (or None) before inviting Approve again.
   The Approve button is for that updated version, not the previous one.
7. If the user says stop / ready / approve, stop questioning and mark ready_to_advance
   when the step artifact meets the depth bar.

Question format (optional; skip if artifacts are already sufficient):
❓ **<short title>**: <question>
➡️ (Recommended) <default you already wrote into the artifact>
""".strip()

# Backward-compatible alias used by older interview helpers.
GRILL_ME_DIGEST = INTERVIEW_TECHNIQUE_DIGEST

_PRINCIPAL_ARCHITECT_RULES = """
Principal Software Architect workflow:
- Propose labeled assumptions instead of blocking on missing details.
- Phase 0: classify LLD (single OS process) vs HLD (distributed) from the spec.
  "Like YouTube/Netflix/Uber/SaaS/marketplace" → HLD. Library/CLI/in-process → LLD.
  Classify on the first turn whenever the spec is enough; do not interview for scale yet.
- LLD: (1) gather rules with recommended defaults written into the spec (2) OO blueprint
  + patterns + SOLID + class/structure Mermaid (3) verify; invite Approve & send.
- HLD (strict order): (1) numeric capacity plan (2) domain model (3) core microservices
  with owned objects/operations (4) communication schemes + infra + concrete Mermaid
  (5) structured FMEA (6) synthesis. Do not ship HTTP API catalogs from HLD.
- Primary artifact this turn must be COMPLETE (never empty / never a one-liner).
  Other fields: "" so the server keeps prior values (avoids truncation).
- HLD Step 4 diagram: 12–25 nodes — clients, LB, API gateway, auth, each named service,
  Redis, Kafka, search, CDN, Postgres, object storage — not a 5-node concept pipeline.
  Also name user↔system, service↔service, and service↔infra protocols (request/response,
  stream, pub/sub).
- Steps 1/3/5 artifacts must be structured (bullets/tables with numbers or owned objects).
- Chat before Approve: answer questions from current artifacts. If they raised a
  concern or asked to change something, update this step's artifact, list
  **Updates to this proposal**, then invite Approve for that new version.
- Never hand the user a decision without its rationale. Every proposal you surface in
  chat carries its driving forces, rejected alternatives, and accepted trade-offs.
""".strip()

# Concatenated (not f-string) so nodes importing only this digest still get the depth bar.
PRINCIPAL_ARCHITECT_DIGEST = (
    _PRINCIPAL_ARCHITECT_RULES + "\n\n" + EXPLANATION_DEPTH_DIGEST
)

_SPEC_SECTIONS = (
    "Problem",
    "Actors",
    "Goals",
    "In scope (v1)",
    "Out of scope",
    "Critical invariants",
    "Success criteria",
    "Assumptions & risks",
)


def estimate_tokens(text: str | None) -> int:
    if not text:
        return 0
    return max(1, (len(text) + _CHARS_PER_TOKEN - 1) // _CHARS_PER_TOKEN)


def fit_text(text: str, max_tokens: int, *, label: str = "content") -> str:
    """Hard-cap text for prompt safety. Prefer calling compact_* before this."""
    if max_tokens <= 0 or estimate_tokens(text) <= max_tokens:
        return text
    budget = max_tokens * _CHARS_PER_TOKEN
    keep = max(0, budget - 80)
    trimmed = text[:keep].rstrip()
    return (
        f"{trimmed}\n\n…\n"
        f"[{label} truncated to ~{max_tokens} tokens for context budget]"
    )


def format_history_tail(
    messages: Iterable[dict[str, Any]],
    *,
    max_tokens: int | None = None,
    max_turns: int | None = None,
) -> str:
    """Build a recent transcript that fits the history token budget."""
    settings = get_settings()
    token_budget = max_tokens if max_tokens is not None else settings.context_history_max_tokens
    turn_budget = max_turns if max_turns is not None else settings.context_history_max_turns

    turns = list(messages)[-max(1, turn_budget) :]
    lines = [f"{m.get('role', 'unknown').upper()}: {m.get('content', '')}" for m in turns]
    # Drop oldest lines until under budget.
    while lines and estimate_tokens("\n".join(lines)) > token_budget:
        lines.pop(0)
    if not lines:
        return "(none)"
    return "\n".join(lines)


def maybe_compact_business_spec(spec: str, *, force: bool = False) -> str:
    """
    If the living spec exceeds the soft token limit, ask the LLM to rewrite it into a
    concise structured document. Falls back to hard truncation only if still oversized.
    """
    settings = get_settings()
    soft = settings.context_spec_soft_tokens
    hard = settings.context_spec_hard_tokens
    target = settings.context_spec_compact_target_tokens

    tokens = estimate_tokens(spec)
    if not force and tokens <= soft:
        return spec

    logger.info(
        "Compacting business_spec (%s tokens > soft limit %s; target %s)",
        tokens,
        soft,
        target,
    )
    compacted = _llm_compact_spec(spec, target_tokens=target)
    if estimate_tokens(compacted) > hard:
        logger.warning(
            "Compacted spec still over hard limit (%s > %s); truncating",
            estimate_tokens(compacted),
            hard,
        )
        compacted = fit_text(compacted, hard, label="business_spec")
    return compacted


def maybe_compact_design_justification(text: str) -> str:
    settings = get_settings()
    soft = settings.context_justification_soft_tokens
    hard = settings.context_justification_hard_tokens
    if estimate_tokens(text) <= soft:
        return text
    logger.info(
        "Compacting design_justification (%s tokens > soft limit %s)",
        estimate_tokens(text),
        soft,
    )
    compacted = _llm_compact_justification(text, target_tokens=settings.context_justification_compact_target_tokens)
    if estimate_tokens(compacted) > hard:
        compacted = fit_text(compacted, hard, label="design_justification")
    return compacted


def _llm_compact_spec(spec: str, *, target_tokens: int) -> str:
    from architect_agent.llm import get_chat_model
    from langchain_core.messages import HumanMessage, SystemMessage

    sections = ", ".join(f"## {s}" for s in _SPEC_SECTIONS)
    model = get_chat_model()
    response = model.invoke(
        [
            SystemMessage(
                content=(
                    "You compress a living business specification for a long interview session.\n"
                    "Preserve every durable decision, actor, scope item, invariant, metric, and risk.\n"
                    "Remove repetition, interview chatter, and 'notes' appendices.\n"
                    f"Target length: about {target_tokens} tokens or less.\n"
                    f"Use these section headings when content exists: {sections}.\n"
                    'Respond ONLY with JSON: {"updated_business_spec": string}.'
                )
            ),
            HumanMessage(content=f"Spec to compress:\n\n{spec}"),
        ]
    )
    content = response.content
    if isinstance(content, list):
        content = "".join(
            block.get("text", "") if isinstance(block, dict) else str(block) for block in content
        )
    payload = _parse_json_object(str(content))
    return str(payload.get("updated_business_spec") or spec).strip() or spec


def _llm_compact_justification(text: str, *, target_tokens: int) -> str:
    from architect_agent.llm import get_chat_model
    from langchain_core.messages import HumanMessage, SystemMessage

    model = get_chat_model()
    response = model.invoke(
        [
            SystemMessage(
                content=(
                    "You compress a system-design justification markdown.\n"
                    "Keep one short rationale per component; drop duplicates.\n"
                    f"Target length: about {target_tokens} tokens or less.\n"
                    'Respond ONLY with JSON: {"design_justification": string}.'
                )
            ),
            HumanMessage(content=f"Justification to compress:\n\n{text}"),
        ]
    )
    content = response.content
    if isinstance(content, list):
        content = "".join(
            block.get("text", "") if isinstance(block, dict) else str(block) for block in content
        )
    payload = _parse_json_object(str(content))
    return str(payload.get("design_justification") or text).strip() or text


def _parse_json_object(text: str) -> dict[str, Any]:
    try:
        return parse_llm_json_object(text)
    except (ValueError, json.JSONDecodeError):
        return {}
