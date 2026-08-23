from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
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
  "Here is the diagram.", "Step 3 complete.", "LLD step 1 update.", or "HLD step 2 update."
  A recap with no reasoning is a FAILED turn. Brief what this step locked in.
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
- Still at most ONE ❓ question, and still ask them to confirm, approve, or agree once
  the artifact is ready. Never tell them to click a button.
  That question must name a concrete choice (who / which data / which constraint /
  which alternative) plus a (Recommended) default. Never "tell me more about your
  system" or "I need more information to proceed".
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
  then ask them to confirm, approve, or agree. Never tell them to click a button.
  At most ONE ❓ question, and only if a decision would change architecture.
- Exception: if this turn is answering a user question (not rewriting artifacts),
  assistant_message is the full answer. Do not ask them to confirm in place of the answer.
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
4. NEVER repeat or rephrase a question already asked, and NEVER re-open an issue
   already settled in DISCUSSION MEMORY. If they did not answer, keep the
   recommended default in the artifact and ask them to confirm, approve, or agree.
5. Capture decisions in living artifacts (spec, ledger, scale, core microservices,
   communication schemes, FMEA, diagram) —
   they stay the source of truth. Chat is not the design document, but it IS the
   architect's reasoning: explain what you wrote, why, what you rejected, and what it
   costs. Never let chat degrade into a one-line "done" pointer at the artifact.
6. Every user message: address their questions/concerns first (never a canned next
   question alone), apply comments to the artifact, then ask them to confirm that
   updated version — never tell them to click a button. Do not add an
   **Updates to this proposal** section.
7. If the user says stop / ready / approve, stop questioning and mark ready_to_advance
   when the step artifact meets the depth bar.
8. Questions must be specific. NEVER stall with "I need more information", "tell me
   more about your system", or "please provide more details" without naming the
   missing fact and the decision it unlocks. Name who, which data, which constraint,
   or which alternative. Include a (Recommended) default. If they confirm ignoring
   or dropping a concern, LOCK it in the spec in one sentence and ask the next
   uncovered specific question — do not re-argue.

Question format (optional; skip if artifacts are already sufficient):
❓ **<short title>**: <question>
➡️ (Recommended) <default you already wrote into the artifact>
""".strip()

# Backward-compatible alias used by older interview helpers.
GRILL_ME_DIGEST = INTERVIEW_TECHNIQUE_DIGEST

_PRINCIPAL_ARCHITECT_RULES = """
Principal Software Architect workflow:
- Propose labeled assumptions instead of blocking on missing details.
- Phase 0: classify LLD vs HLD from deployment topology, not product analogies.
  LLD: single OS process, library, CLI, local self-contained stand-alone / desktop /
  single-machine app, modular monolith on one host.
  HLD: the user actually wants a distributed topology (microservices, multi-node
  storage, multi-region, CDN/Kafka as required infra).
  Analogies like YouTube/Netflix/Uber/SaaS do NOT lock HLD. An explicit local /
  stand-alone / self-contained / not-distributed request OVERRIDES a prior HLD call.
  Classify on the first turn whenever the spec is enough; do not interview for scale yet.
- LLD: (1) gather rules with recommended defaults written into the spec (2) OO blueprint
  + patterns + SOLID + class/structure Mermaid (3) verify; ask them to confirm & send.
- HLD (strict order): (1) numeric capacity plan (2) domain model (3) core microservices
  with owned objects/operations (4) communication schemes + infra + concrete Mermaid
  (5) structured FMEA (6) synthesis. Do not ship HTTP API catalogs from HLD.
- If a later-step change belongs earlier, rewind and walk forward. Keep later artifacts
  and patch them only where the earlier change requires it. Do not regenerate from scratch.
- Do not hand off a package that is unchanged vs the last Orchestrator delivery.
- Primary artifact this turn must be COMPLETE (never empty / never a one-liner).
  Other fields: "" so the server keeps prior values (avoids truncation).
- HLD Step 4 diagram: 12–25 nodes — clients, LB, API gateway, auth, each named service,
  Redis, Kafka, search, CDN, Postgres, object storage — not a 5-node concept pipeline.
  Also name user↔system, service↔service, and service↔infra protocols (request/response,
  stream, pub/sub).
- Steps 1/3/5 artifacts must be structured (bullets/tables with numbers or owned objects).
- Chat before Approve: answer questions from current artifacts. If they raised a
  concern or asked to change something, address that comment in chat, update this
  step's artifact, then ask them to confirm, approve, or agree for that new
  version. Never tell them to click a button. Do not add an
  **Updates to this proposal** section.
  Never ignore a user message to continue a prepared question list.
- Never hand the user a decision without its rationale. Every proposal you surface in
  chat carries its driving forces, rejected alternatives, and accepted trade-offs.
""".strip()

DISCUSSION_MEMORY_RULES = """
DISCUSSION MEMORY / CONVERSATION KEYNOTES (durable session memory — honor it over a short chat tail):
- A DISCUSSION MEMORY / CONVERSATION KEYNOTES block is the briefing of this isolated chat:
  decisions, approvals, rejections, and open questions.
- A DISCUSSION MEMORY block in the user prompt is the long-term record of this session.
- The labeled Latest user message is the work of this turn. Memory and the recent
  transcript are context — never ignore that message to restart discovery or replay
  a prepared question list.
- NEVER re-open a settled decision or re-suggest a solution the user already accepted
  or rejected (including issues closed in Phase 0). If they confirm ignoring sync,
  security, or any other concern, lock it and move on — do not rebut it again.
- If Deployment topology is locked (local stand-alone vs distributed), do not propose
  the other topology. Do not steer a local app toward Kafka, CDN, microservices, or
  multi-region "because similar products do that".
- Analogies like YouTube/Netflix/Uber never override an explicit local / self-contained
  / stand-alone / single-machine request — that is LLD.
- When memory grows, keep locked topology, settled issues, and rejected proposals;
  drop interview chatter.
""".strip()

# Concatenated (not f-string) so nodes importing only this digest still get the depth bar.
PRINCIPAL_ARCHITECT_DIGEST = (
    _PRINCIPAL_ARCHITECT_RULES
    + "\n\n"
    + DISCUSSION_MEMORY_RULES
    + "\n\n"
    + EXPLANATION_DEPTH_DIGEST
)

TRACK_CLASSIFICATION_RULES = """
LLD vs HLD is a deployment-topology call, not a product-category call:
- LLD: one OS process / library / CLI / local self-contained stand-alone / desktop /
  single-machine app / modular monolith on one host.
- HLD: distributed microservices, multi-node storage, multi-region, or CDN/Kafka
  as a topology the user actually wants.
- Explicit local / stand-alone / self-contained / not-distributed language OVERRIDES
  analogies like YouTube/Netflix/Uber/SaaS and OVERRIDES a prior HLD classification.
- Do not keep proposing distributed solutions after the user routed back to stand-alone.
- After the project specification is compiled, you MUST pick lld or hld — never unset.
  Recommend the closer topology and say so; the user can still correct it.
""".strip()

_SPEC_SECTIONS = (
    "Problem",
    "Actors",
    "Goals",
    "In scope (v1)",
    "Out of scope",
    "Deployment topology",
    "Critical invariants",
    "Success criteria",
    "Assumptions & risks",
    "Diagram components",
)

_SKIP_DIGEST_PENDING = {
    "ok",
    "okay",
    "looks good",
    "lgtm",
    "approve",
    "approved",
    "next step",
    "continue",
    "sounds good",
}


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


def format_phase_context(
    digest: str,
    messages: Iterable[dict[str, Any]],
    node: str = "phase",
    *,
    max_tokens: int | None = None,
    max_turns: int | None = None,
) -> str:
    """Long-term discussion memory plus a short recent transcript for this node."""
    tail = format_history_tail(messages, max_tokens=max_tokens, max_turns=max_turns)
    digest_s = (digest or "").strip()
    parts: list[str] = []
    if digest_s:
        parts.append(
            "CONVERSATION KEYNOTES (decisions, approvals, rejections — do not "
            "re-open settled items or re-suggest rejected solutions):\n"
            f"{digest_s}"
        )
    parts.append(f"Recent {node} turns:\n{tail}")
    return "\n\n".join(parts)


def conversation_turn_user(
    digest: str,
    messages: Iterable[dict[str, Any]],
    node: str,
    pending: str,
    *,
    artifacts: str = "",
    empty_pending_note: str = "(none)",
    max_tokens: int | None = None,
    max_turns: int | None = None,
) -> str:
    """Standard user blob: discussion memory, recent tail, artifacts, labeled latest message."""
    parts: list[str] = [
        format_phase_context(
            digest, messages, node, max_tokens=max_tokens, max_turns=max_turns
        )
    ]
    art = (artifacts or "").strip()
    if art:
        parts.append(art)
    body = (pending or "").strip()
    parts.append(f"Latest user message:\n{body or empty_pending_note}")
    return "\n\n".join(parts)


_TURN_KINDS = frozenset(
    {"answer", "concern", "complement", "approve", "disapprove", "unrelated", "unclear"}
)

_CONSULT_SYSTEM = """
You are the conversation keynotes consultant for ONE isolated chat session.
Judge the latest user message against the previous assistant message as a human would.
Do not decide from isolated keywords.

relevant=true when they answered a question, addressed a concern, complemented or
changed the idea, or approved/disapproved something in that prior message or the
live proposal.
vague=true when the reply is too unclear to act on (even if it might be related).
relevant=false when it is off-topic and does not engage the prior message.

If relevant and not vague: rewrite conversation keynotes as a brief briefing of THIS
chat — settled decisions, approvals, rejections, open questions. Keep prior keynotes.
Add this turn in your own words. Never quote the user verbatim.

If not relevant or vague: leave keynotes unchanged. Write clarify_message that names
the immediate open concern from the previous assistant message and asks them to
address it or clarify what they meant. Do not quote their message. Do not start a
new interview. Do not stall with "tell me more about your system".

Respond ONLY with JSON:
{
  "relevant": boolean,
  "vague": boolean,
  "kind": "answer" | "concern" | "complement" | "approve" | "disapprove" | "unrelated" | "unclear",
  "keynotes": string,
  "clarify_message": string
}
""".strip()


@dataclass
class UserTurnConsult:
    relevant: bool
    vague: bool
    kind: str
    keynotes: str
    clarify_message: str

    @property
    def needs_clarification(self) -> bool:
        return (not self.relevant) or self.vague


def consult_user_turn(
    *,
    pending: str,
    last_assistant: str,
    keynotes: str,
    phase: str = "",
) -> UserTurnConsult:
    """LLM consult: is this reply on-topic, and if so update conversation keynotes."""
    pending_s = (pending or "").strip()
    prior = (keynotes or "").strip()
    last = (last_assistant or "").strip()
    fallback = UserTurnConsult(
        relevant=True,
        vague=False,
        kind="complement",
        keynotes=prior,
        clarify_message="",
    )
    if not pending_s or not last:
        return fallback
    try:
        from langchain_core.messages import HumanMessage, SystemMessage

        from architect_agent.llm import get_chat_model

        model = get_chat_model()
        response = model.invoke(
            [
                SystemMessage(content=_CONSULT_SYSTEM),
                HumanMessage(
                    content=(
                        f"Phase: {phase or '(n/a)'}\n\n"
                        f"Previous assistant message:\n{last[:4000]}\n\n"
                        f"Latest user message:\n{pending_s}\n\n"
                        f"Current conversation keynotes:\n{prior or '(none)'}\n"
                    )
                ),
            ]
        )
        content = response.content
        if isinstance(content, list):
            content = "".join(
                block.get("text", "") if isinstance(block, dict) else str(block)
                for block in content
            )
        payload = _parse_json_object(str(content))
    except Exception:
        logger.exception("consult_user_turn failed; treating the turn as relevant")
        return fallback
    relevant = bool(payload.get("relevant", True))
    vague = bool(payload.get("vague", False))
    kind = str(payload.get("kind") or "").strip().lower()
    if kind not in _TURN_KINDS:
        kind = "unclear" if (vague or not relevant) else "complement"
    updated = str(payload.get("keynotes") or "").strip()
    clarify = str(payload.get("clarify_message") or "").strip()
    if (not relevant) or vague:
        if not clarify:
            clarify = (
                "Please address the open point in my previous message, or clarify "
                "what you meant, so I can continue this conversation."
            )
        return UserTurnConsult(
            relevant=relevant,
            vague=vague,
            kind=kind if kind in {"unrelated", "unclear"} else ("unclear" if vague else "unrelated"),
            keynotes=prior,
            clarify_message=clarify,
        )
    return UserTurnConsult(
        relevant=True,
        vague=False,
        kind=kind,
        keynotes=updated or prior,
        clarify_message="",
    )


def refresh_discussion_digest(
    prior: str,
    *,
    pending: str = "",
    assistant: str = "",
    phase: str = "",
    track: str = "",
    spec: str = "",
) -> str:
    """Merge the latest turn into durable memory; compact when it grows."""
    merged = _heuristic_digest_merge(
        prior,
        pending=pending,
        assistant=assistant,
        phase=phase,
        track=track,
        spec=spec,
    )
    settings = get_settings()
    if estimate_tokens(merged) <= settings.context_digest_soft_tokens:
        return merged
    logger.info(
        "Compacting discussion_digest (%s tokens > soft limit %s)",
        estimate_tokens(merged),
        settings.context_digest_soft_tokens,
    )
    compacted = _llm_compact_digest(
        merged,
        pending=pending,
        assistant=assistant,
        phase=phase,
        track=track,
        spec=spec,
        target_tokens=settings.context_digest_compact_target_tokens,
    )
    if estimate_tokens(compacted) > settings.context_digest_hard_tokens:
        compacted = fit_text(
            compacted, settings.context_digest_hard_tokens, label="discussion_digest"
        )
    return compacted


def _heuristic_digest_merge(
    prior: str,
    *,
    pending: str,
    assistant: str,
    phase: str,
    track: str,
    spec: str,
) -> str:
    from architect_agent.scope import spec_locks_standalone, wants_distributed, wants_standalone

    sections: list[str] = []
    prior_s = (prior or "").strip()
    if prior_s:
        sections.append(prior_s)

    additions: list[str] = []
    pending_s = (pending or "").strip()
    spec_s = spec or ""
    if wants_standalone(pending_s) or spec_locks_standalone(spec_s):
        additions.append(
            "- Locked topology: local self-contained stand-alone (LLD / single OS process). "
            "Do not propose distributed microservices, Kafka, CDN, or multi-region."
        )
    elif wants_distributed(pending_s):
        additions.append("- Locked topology: distributed (HLD).")
    if track in {"lld", "hld"}:
        additions.append(f"- Current classification: {track.upper()} (phase {phase or 'n/a'}).")

    assistant_s = (assistant or "").strip()
    if assistant_s and estimate_tokens(assistant_s) > 40:
        snippet = re.sub(r"\s+", " ", assistant_s)[:280]
        additions.append(f"- Last architect note: {snippet}")

    prior_l = prior_s.lower()
    new_lines = [line for line in additions if line.lower() not in prior_l]
    if not new_lines:
        return prior_s
    if not prior_s:
        return "## Settled decisions\n" + "\n".join(new_lines)
    return f"{prior_s}\n" + "\n".join(new_lines)


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
                    "Preserve ## Deployment topology and every locked LLD/HLD decision.\n"
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


def _llm_compact_digest(
    digest: str,
    *,
    pending: str,
    assistant: str,
    phase: str,
    track: str,
    spec: str,
    target_tokens: int,
) -> str:
    from architect_agent.llm import get_chat_model
    from langchain_core.messages import HumanMessage, SystemMessage

    model = get_chat_model()
    response = model.invoke(
        [
            SystemMessage(
                content=(
                    "You compress discussion memory for a long architecture interview.\n"
                    "Keep locked topology, settled decisions, resolved issues, and rejected "
                    "proposals. Never drop a locked local/stand-alone vs distributed call.\n"
                    "Use sections: Locked topology, Settled decisions, Issues raised and how "
                    "resolved, Rejected proposals, Open questions.\n"
                    f"Target length: about {target_tokens} tokens or less.\n"
                    'Respond ONLY with JSON: {"discussion_digest": string}.'
                )
            ),
            HumanMessage(
                content=(
                    f"Prior discussion memory:\n{digest}\n\n"
                    f"Phase: {phase or '(n/a)'}  Track: {track or '(n/a)'}\n"
                    f"Latest user message:\n{pending or '(none)'}\n\n"
                    f"Latest assistant note:\n{(assistant or '')[:800] or '(none)'}\n\n"
                    f"Spec excerpt:\n{(spec or '')[:1200]}\n"
                )
            ),
        ]
    )
    content = response.content
    if isinstance(content, list):
        content = "".join(
            block.get("text", "") if isinstance(block, dict) else str(block) for block in content
        )
    payload = _parse_json_object(str(content))
    return str(payload.get("discussion_digest") or digest).strip() or digest


def _parse_json_object(text: str) -> dict[str, Any]:
    try:
        return parse_llm_json_object(text)
    except (ValueError, json.JSONDecodeError):
        return {}
