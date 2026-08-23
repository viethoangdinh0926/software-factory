from __future__ import annotations

import json
import logging
import re
from functools import lru_cache
from typing import Any

import httpx
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from architect_agent.config import get_settings
from architect_agent.scope import spec_locks_standalone, wants_distributed, wants_standalone

logger = logging.getLogger(__name__)


class StubChatModel(BaseChatModel):
    """Deterministic offline model so the architect graph can be exercised without API keys."""

    @property
    def _llm_type(self) -> str:
        return "stub"

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        blob = "\n".join(str(m.content) for m in messages)
        lower = blob.lower()
        turns = (
            blob.count("User answer:")
            + blob.count("Latest user message:")
            + blob.count("USER:")
        )

        if "user turn intent classifier" in lower:
            payload = _stub_turn_intent(blob)
        elif "conversation keynotes consultant" in lower:
            payload = _stub_user_turn_consult(blob)
        elif "design-stage router" in lower:
            payload = {"stage": _stub_rewind_stage(blob)}
        elif "this is q&a before they approve this workflow step" in lower:
            payload = {"assistant_message": _stub_qa(blob)}
        elif "compress a living business specification" in lower or "spec to compress:" in lower:
            payload = {"updated_business_spec": _compact_spec_stub(blob)}
        elif "compress a system-design justification" in lower or "justification to compress:" in lower:
            payload = {"design_justification": _compact_justification_stub(blob)}
        elif "compress discussion memory" in lower or "prior discussion memory:" in lower:
            payload = {"discussion_digest": _stub_discussion_digest(blob)}
        elif "plan web searches" in lower or '"queries"' in lower and "alternatives" in lower:
            payload = {
                "queries": [
                    "warehouse inventory management software alternatives",
                    "open source WMS competitors",
                    "build vs buy inventory tracking SaaS",
                ]
            }
        elif "market evaluator" in lower or "market evaluation report" in lower:
            payload = _stub_market_evaluation(blob)
        elif "phase 0 interview conductor" in lower:
            payload = _stub_phase0_interview_turn(blob)
        elif "phase 0 interview question generator" in lower:
            payload = _stub_phase0_questions(blob)
        elif "phase 0 spec compiler" in lower:
            compiled = _rich_spec(blob)
            payload = {
                "compiled_spec": compiled,
                "assistant_message": (
                    "Here is the compiled project specification.\n\n"
                    f"{compiled}\n\n"
                    "If this specification looks right, confirm, approve, or agree so I can "
                    "classify LLD vs HLD. Otherwise tell me what to add or change."
                ),
            }
        elif "phase 0 spec refiner" in lower:
            payload = {
                **_stub_phase0(blob),
                "assistant_message": (
                    "The spec is updated from your comments. "
                    "If this looks right, confirm, approve, or agree to start the classified "
                    "track, or keep editing."
                ),
            }
        elif "phase 0 classifier" in lower or "classify the design scope" in lower:
            payload = _stub_phase0(blob)
        elif "update the living business specification" in lower or (
            "update the business specification" in lower or "from one interview answer" in lower
        ):
            # Merge without growing an unbounded interview-notes appendix.
            payload = {"updated_business_spec": _fold_answer_stub(blob)}
        elif "lld track node" in lower or "current lld step" in lower:
            payload = _stub_lld(blob)
        elif "hld track node" in lower or "current hld step" in lower:
            payload = _stub_hld(blob)
        elif "architect agent's system design node" in lower or '"design_diagram"' in lower:
            feedback = _latest_feedback(blob)
            payload = _stub_design_proposal(blob, feedback)
        else:
            asked_block = ""
            if "Already asked question titles" in blob:
                asked_block = blob.split("Already asked question titles", 1)[1]
                asked_block = asked_block.split("Uncovered checklist", 1)[0]
            asked_lower = asked_block.lower()

            questions = [
                (
                    "primary actors",
                    "❓ **Primary actors**: Who uses this system day-to-day, and what job are they hiring it to do?\n\n➡️ (Recommended) Name 1–2 concrete roles with a single primary job each.",
                ),
                (
                    "critical invariants",
                    "❓ **Critical invariants**: What must never go wrong (money, safety, compliance, trust)?\n\n➡️ (Recommended) List the top 1–3 invariants in plain language.",
                ),
                (
                    "v1 scope",
                    "❓ **V1 scope**: What 3 capabilities must ship in v1 for the primary job to succeed?\n\n➡️ (Recommended) List three user-visible capabilities, not infrastructure.",
                ),
                (
                    "out of scope",
                    "❓ **Out of scope / non-goals**: What will you explicitly NOT build in v1?\n\n➡️ (Recommended) Name 2–3 tempting features that are deferred on purpose.",
                ),
            ]
            chosen = next((text for key, text in questions if key not in asked_lower), None)
            asked_count = sum(1 for key, _ in questions if key in asked_lower)
            if chosen is None or asked_count >= 3 or turns >= 3:
                payload = {
                    "ready_for_design": True,
                    "updated_business_spec": _rich_spec(blob),
                    "assistant_message": (
                        "The readiness checklist looks covered. You can keep adding detail, "
                        "or approve to move into market evaluation and system design."
                    ),
                    "topic_id": "ready",
                    "rationale": "Checklist covered.",
                }
            else:
                payload = {
                    "ready_for_design": False,
                    "updated_business_spec": _rich_spec(blob),
                    "assistant_message": chosen,
                    "topic_id": "next",
                    "rationale": "Need more crisp decisions before design.",
                }

        return ChatResult(
            generations=[ChatGeneration(message=AIMessage(content=json.dumps(payload)))]
        )


def _stub_help_answering_questions(text: str) -> bool:
    """Stub stand-in for the classifier LLM: user wants candidate replies to our questions."""
    compact = re.sub(r"\s+", " ", (text or "").strip().lower())
    if not compact:
        return False
    asking_for_help = any(
        token in compact
        for token in (
            "help me",
            "help us",
            "what would you",
            "what should i",
            "what could i",
            "can you draft",
            "could you draft",
            "draft",
            "propose",
            "suggest",
            "candidate",
            "option",
            "possible",
            "potential",
            "recommend",
            "example",
            "sample",
        )
    )
    about_replying = any(
        token in compact
        for token in (
            "answer",
            "reply",
            "replies",
            "respond",
            "pick",
            "choose",
            "question",
            "option",
            "default",
        )
    )
    return asking_for_help and about_replying


def _stub_rewind_stage(blob: str) -> str:
    user = ""
    for marker in ("User message:", "Latest user message:"):
        if marker in blob:
            user = blob.split(marker, 1)[1].strip()
            break
    if not user:
        user = blob
    compact = re.sub(r"\s+", " ", user).strip().lower()
    current = blob.lower()
    if re.search(r"\b(skip|jump)\s+(ahead|to)\b|\bgo\s+straight\s+to\b", compact):
        return "ahead"
    if wants_standalone(compact) or any(
        token in compact
        for token in (
            "gdpr",
            "residenc",
            "new requirement",
            "spec requirement",
            "eu-only",
            "eu only",
            "invariant",
            "out of scope",
            "new actor",
        )
    ):
        return "phase0"
    if "fmea" in compact or "spof" in compact:
        return "hld5" if "hld" in current else "lld3"
    if "domain object" in compact or "entities" in compact:
        return "hld2"
    return "current"


def _stub_turn_intent(blob: str) -> dict[str, str]:
    """Deterministic command vs information classification for the stub model."""
    user = ""
    for marker in ("User message:", "Latest user message:"):
        if marker in blob:
            user = blob.split(marker, 1)[1].strip()
            break
    if not user:
        user = blob
    compact = re.sub(r"\s+", " ", user).strip().lower().rstrip(".!")
    if _stub_help_answering_questions(user):
        return {"category": "information", "action": "answer"}
    if re.search(r"\bwhy should i approve\b", compact) or (
        "?" in user and re.search(r"\bapprove\b", compact) and "rate" not in compact
    ):
        return {"category": "information", "action": "answer"}
    if (
        re.search(r"\b(next step|move on|wrap up|go ahead|proceed)\b", compact)
        and "?" not in user
        and "add " not in compact
        and "change " not in compact
    ):
        return {"category": "command", "action": "approve"}
    if any(
        hint in compact
        for hint in (
            "add ",
            "change ",
            "switch ",
            "worried",
            "missing",
            "rate limit",
            "we should",
            "we need",
            "stand-alone",
            "standalone",
            "self-contained",
        )
    ):
        return {"category": "command", "action": "revise"}
    if re.search(
        r"\b(approve|lgtm|looks good|next step|move on|wrap up|go ahead|proceed|"
        r"let'?s continue|happy with this|ship it)\b",
        compact,
    ) and "?" not in user:
        return {"category": "command", "action": "approve"}
    if "?" in user or compact.startswith(
        ("why", "what", "which", "how", "show", "list", "explain")
    ):
        return {"category": "information", "action": "answer"}
    return {"category": "command", "action": "revise"}


def _stub_suggested_answers(blob: str) -> str:
    questions: list[str] = []
    if "Open questions to propose answers for:" in blob:
        block = blob.split("Open questions to propose answers for:", 1)[1]
        for stop in ("User question:", "Latest user message:", "Current artifacts", "Business spec:"):
            if stop in block:
                block = block.split(stop, 1)[0]
        for line in block.splitlines():
            text = line.strip().lstrip("-").strip()
            if text:
                questions.append(text)
    if not questions and "Last assistant message (questions you asked):" in blob:
        block = blob.split("Last assistant message (questions you asked):", 1)[1]
        for stop in ("Open questions", "User question:", "Latest user message:", "Current artifacts"):
            if stop in block:
                block = block.split(stop, 1)[0]
        for line in block.splitlines():
            stripped = line.strip()
            if "?" in stripped or stripped.startswith("❓"):
                questions.append(stripped.lstrip("❓-• ").strip())
    if not questions:
        questions = ["the open question I asked"]
    parts = [
        "Here are potential answers you can use or edit. "
        "I am not treating this as your decision yet.\n"
    ]
    for question in questions[:6]:
        parts.append(f"### {question}")
        parts.append("- (Recommended) A concrete default based on the current spec.")
        parts.append("- A more conservative alternative.")
        parts.append("- A more ambitious alternative.\n")
    return "\n".join(parts)


def _stub_qa(blob: str) -> str:
    lower = blob.lower()
    ask = _labeled_user_text(blob).lower() or lower
    if _stub_help_answering_questions(ask):
        return _stub_suggested_answers(blob)
    if "endpoint" in ask or " url" in ask or "urls" in ask:
        found = re.findall(
            r"\b((?:GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+/[A-Za-z0-9_{}\-./]*)",
            blob,
            re.I,
        )
        uniq: list[str] = []
        seen: set[str] = set()
        for item in found:
            key = item.upper()
            if key in seen:
                continue
            seen.add(key)
            uniq.append(item)
        if uniq:
            return "URL endpoints currently recorded in this design:\n" + "\n".join(
                f"- `{item}`" for item in uniq[:24]
            )
        return (
            "No URL endpoints are recorded yet. Core microservices and communication "
            "schemes are agreed first; HTTP/gRPC path specs are completed later with "
            "the orchestrator."
        )
    if "own" in ask or "domain object" in ask or "identityservice" in ask:
        return (
            "IdentityService owns User, Session, and Credential in the current core "
            "microservices artifact. Other headed services own their own domain objects."
        )
    if "gdpr" in ask or "residenc" in ask:
        return (
            "You raised GDPR / data-residency. I would keep EU-only processing as a "
            "v1 invariant and call it out in the spec before we classify the track."
        )
    if "rate limit" in ask:
        return (
            "Rate limiting belongs on the edge/gateway for write-heavy APIs. "
            "I can add it to this step's artifact if you want it locked in."
        )
    if "worried" in ask or "concern" in ask or "compliance" in ask:
        return (
            "Compliance and similar worries belong in the market write-up and the spec "
            "invariants. Here is how the current artifacts speak to that, and what I "
            "would change if we revise."
        )
    if "grade" in ask:
        return "The market evaluation grade for this design version is **B**."
    if "hld" in lower and "lld" in lower:
        return (
            "This is HLD because the spec describes a distributed, multi-service system "
            "rather than a single OS process."
        )
    return (
        "Here is what is currently on this step, based on the artifacts. "
        "Ask if you want a specific section quoted."
    )


def _living_spec_excerpt(blob: str) -> str:
    """Living spec only — exclude discussion memory and the latest user turn."""
    part = blob
    for marker in ("Current living specification:", "Living specification:"):
        if marker in blob:
            part = blob.split(marker, 1)[1]
            break
    else:
        return ""
    for stop in (
        "DISCUSSION MEMORY",
        "Recent phase0",
        "Recent Phase 0",
        "Recent hld",
        "Recent lld",
        "User's requested changes:",
        "Latest user message:",
        "User's final response:",
        "Recent ",
    ):
        if stop in part:
            part = part.split(stop, 1)[0]
    return part.strip()


def _stub_phase0(blob: str) -> dict[str, Any]:
    # Classify from the latest user turn + living spec only — system digests mention both tracks.
    feedback = _latest_feedback(blob)
    spec_text = _living_spec_excerpt(blob)
    lower = (spec_text or feedback or blob).lower()
    if wants_standalone(feedback) or spec_locks_standalone(spec_text):
        track = "lld"
    elif wants_distributed(feedback):
        track = "hld"
    elif any(
        k in lower
        for k in ("microservice", "distributed", "multi-region", "kafka", "cdn", "multi-tenant")
    ):
        track = "hld"
    elif any(
        k in lower
        for k in ("single process", "single os process", "library", "in-process", "(lld)")
    ) or re.search(r"\bcli\b", lower):
        track = "lld"
    elif "warehouse" in lower or "inventory" in lower or "saas" in lower:
        track = "hld"
    else:
        track = "lld"
    addressed = " I addressed your comment in the spec." if feedback else ""
    return {
        "design_track": track,
        "ready_to_advance": True,
        "system_type": "video sharing platform" if "youtube" in lower else "system",
        "search_query": "video sharing platform requirements" if "youtube" in lower else f"{track} architecture requirements",
        "updated_business_spec": _rich_spec(blob),
        "tradeoff_ledger": "- Scope classification pending user confirm.\n",
        "assistant_message": (
            f"Scope looks like **{track.upper()}**.{addressed} "
            "If this looks right, confirm, approve, or agree to start that track, "
            "or tell me if this should be the other track."
        ),
    }


def _stub_user_turn_consult(blob: str) -> dict[str, Any]:
    pending = ""
    if "Latest user message:" in blob:
        pending = blob.rsplit("Latest user message:", 1)[-1].strip()
        for stop in ("Current conversation keynotes:", "Previous assistant"):
            if stop in pending:
                pending = pending.split(stop, 1)[0].strip()
    last = ""
    if "Previous assistant message:" in blob:
        last = blob.split("Previous assistant message:", 1)[1]
        if "Latest user message:" in last:
            last = last.split("Latest user message:", 1)[0]
        last = last.strip()
    prior = ""
    if "Current conversation keynotes:" in blob:
        prior = blob.split("Current conversation keynotes:", 1)[1].strip()
        if prior == "(none)":
            prior = ""
    compact = re.sub(r"\s+", " ", pending).strip().lower().rstrip(".!")
    last_l = last.lower()
    asking_confirm = (
        "confirm, approve, or agree" in last_l or "if this looks right" in last_l
    )
    if re.search(r"\b(weather|asdf|qwerty|lorem ipsum)\b", compact):
        return {
            "relevant": False,
            "vague": False,
            "kind": "unrelated",
            "keynotes": prior,
            "clarify_message": (
                "That does not address the open point in my previous message. "
                "Please answer that concern, or clarify what you meant, so I can continue."
            ),
        }
    vague_only = compact in {
        "maybe",
        "idk",
        "i don't know",
        "i dont know",
        "dunno",
        "whatever",
        "not sure",
        "hmm",
        "huh",
    }
    if vague_only or (compact in {"ok", "okay", "k"} and not asking_confirm):
        return {
            "relevant": True,
            "vague": True,
            "kind": "unclear",
            "keynotes": prior,
            "clarify_message": (
                "I am not sure how that answers the open point in my previous message. "
                "Please address that concern or spell out what you want changed."
            ),
        }
    kind = "complement"
    if compact in {
        "looks good",
        "lgtm",
        "approve",
        "approved",
        "next step",
        "move on",
        "wrap up",
    } or (compact in {"ok", "okay"} and asking_confirm):
        kind = "approve"
    elif "?" in pending or compact.startswith(("why", "what", "which", "how", "who")):
        kind = "answer"
    elif "worried" in compact or "concern" in compact:
        kind = "concern"
    lines = [prior] if prior else ["## Settled decisions"]
    if pending:
        item = f"- Settled from user: {pending[:400]}"
        if item.lower() not in prior.lower():
            lines.append(item)
    return {
        "relevant": True,
        "vague": False,
        "kind": kind,
        "keynotes": "\n".join(line for line in lines if line).strip(),
        "clarify_message": "",
    }


def _stub_discussion_digest(blob: str) -> str:
    prior = ""
    if "Prior discussion memory:" in blob:
        prior = blob.split("Prior discussion memory:", 1)[1]
        for marker in ("Phase:", "Latest user message:"):
            if marker in prior:
                prior = prior.split(marker, 1)[0]
                break
    prior = prior.strip()
    feedback = _latest_feedback(blob)
    lines = [prior] if prior else ["## Settled decisions"]
    if wants_standalone(feedback):
        lock = (
            "- Locked topology: local self-contained stand-alone (LLD / single OS process)."
        )
        if lock.lower() not in prior.lower():
            lines.append(lock)
    if feedback:
        item = f"- Settled from user: {feedback[:400]}"
        if item.lower() not in prior.lower():
            lines.append(item)
    return "\n".join(line for line in lines if line).strip()


def _stub_phase0_questions(blob: str) -> dict[str, Any]:
    del blob
    questions = [
        {"id": "q1", "text": "Who are the primary daily users, and what job are they hiring this for?", "category": "users"},
        {"id": "q2", "text": "What must never go wrong in v1 (safety, money, compliance, trust)?", "category": "constraints"},
        {"id": "q3", "text": "Which 3 capabilities must ship in v1?", "category": "business"},
        {"id": "q4", "text": "What will you explicitly not build in v1?", "category": "business"},
        {"id": "q5", "text": "How will you know v1 succeeded?", "category": "success_metrics"},
    ]
    return {
        "questions": questions,
        "assistant_message": (
            "I will gather just enough to classify LLD vs HLD. "
            f"{questions[0]['text']}"
        ),
    }


def _stub_phase0_interview_turn(blob: str) -> dict[str, Any]:
    feedback = _latest_feedback(blob)
    match = re.search(r"Current question index:\s*(\d+)", blob)
    current = int(match.group(1)) if match else 0
    if _stub_help_answering_questions(feedback):
        return {
            "updated_business_spec": _living_spec_excerpt(blob) or _extract_spec(blob),
            "questions": _stub_phase0_questions(blob)["questions"],
            "current_question_index": current,
            "interview_complete": False,
            "assistant_message": _stub_suggested_answers(blob),
        }
    idx = min(current + 1, 5)
    living = _living_spec_excerpt(blob) or _extract_spec(blob)
    return {
        "updated_business_spec": living,
        "questions": _stub_phase0_questions(blob)["questions"],
        "current_question_index": idx,
        "interview_complete": idx >= 5,
        "assistant_message": (
            "I folded that into the living spec (including any GDPR, residency, or "
            "security concerns). "
            + (
                "I have enough to compile the specification."
                if idx >= 5
                else "Next, only if still needed: what 3 capabilities must ship in v1?"
            )
        ),
    }


def _stub_keep_existing(blob: str) -> bool:
    return "keep existing artifacts. work already completed" in (blob or "").lower()


def _stub_keep_payload(track: str, step: int) -> dict[str, Any]:
    last = 6 if track == "hld" else 3
    return {
        "updated_business_spec": "",
        "tradeoff_ledger": "",
        "scale_estimates": "",
        "core_microservices": "",
        "api_contracts": "",
        "communication_schemes": "",
        "fmea_notes": "",
        "design_diagram": "",
        "design_diagram_lines": [],
        "design_justification": "",
        "ready_to_advance": True,
        "design_ready_to_approve": step >= last,
        "assistant_message": (
            f"Kept the existing {track.upper()} step {step} artifact and applied only "
            "patches required by the carry-forward change, if that step is affected. "
            "If this looks right, confirm, approve, or agree to continue."
        ),
    }


def _stub_lld(blob: str) -> dict[str, Any]:
    step = 1
    if "current lld step: 2" in blob.lower():
        step = 2
    elif "current lld step: 3" in blob.lower():
        step = 3
    if _stub_keep_existing(blob):
        return _stub_keep_payload("lld", step)
    feedback = _latest_feedback(blob)
    design = _stub_design_proposal(blob, feedback)
    ledger = (
        "- Prefer clear domain objects over anemic DTOs.\n"
        "- Favor composition for cross-cutting policies.\n"
    )
    return {
        "updated_business_spec": _rich_spec(blob),
        "tradeoff_ledger": ledger,
        "design_diagram": design["design_diagram"] if step >= 2 else "",
        "design_diagram_lines": design["design_diagram"].splitlines() if step >= 2 else [],
        "design_justification": design["design_justification"] if step >= 2 else "",
        "ready_to_advance": True,
        "design_ready_to_approve": step >= 3,
        "assistant_message": (
            ("I applied your comments to this step. " if feedback else "")
            + f"LLD step {step} draft ready. "
            + (
                "If this looks right, confirm, approve, or agree so we can run market evaluation and hand off."
                if step >= 3
                else "If this looks right, confirm, approve, or agree to advance, or tell me what to change."
            )
        ),
    }


def _stub_hld_architecture_diagram(feedback: str = "") -> str:
    """Concrete multi-tier diagram so Step 4 concreteness checks pass under stub LLM."""
    lower = feedback.lower()
    lines = [
        "flowchart LR",
        "  Client[Web/Mobile Client] --> LB[Load Balancer]",
        "  LB --> GW[API Gateway]",
        "  GW --> Auth[Auth IdentityService]",
        "  GW --> Catalog[VideoCatalogService]",
        "  GW --> Upload[UploadService]",
        "  GW --> Playback[PlaybackService]",
        "  GW --> Search[SearchService]",
        "  GW --> Recs[RecommendationsService]",
        "  GW --> Engage[EngagementService]",
        "  Upload --> Kafka[Kafka Broker]",
        "  Kafka --> Transcode[Transcoding Workers]",
        "  Transcode --> S3[Object Storage S3]",
        "  Auth --> PG[(Postgres)]",
        "  Catalog --> PG",
        "  Playback --> CDN[CDN]",
        "  CDN --> S3",
        "  Search --> ES[(Elasticsearch)]",
        "  Recs --> Redis[(Redis Cache)]",
        "  Engage --> Kafka",
    ]
    if "monolith" in lower:
        lines = [
            "flowchart LR",
            "  Client[Web/Mobile Client] --> LB[Load Balancer]",
            "  LB --> GW[API Gateway]",
            "  GW --> Auth[Auth IdentityService]",
            "  GW --> App[Modular Monolith Domains]",
            "  App --> PG[(Postgres)]",
            "  App --> Redis[(Redis Cache)]",
            "  App --> Kafka[Kafka Broker]",
            "  Kafka --> Transcode[Transcoding Workers]",
            "  Transcode --> S3[Object Storage S3]",
            "  App --> Search[SearchService]",
            "  Search --> ES[(Elasticsearch)]",
            "  App --> CDN[CDN]",
            "  CDN --> S3",
        ]
    return "\n".join(lines)


def _stub_hld(blob: str) -> dict[str, Any]:
    step = 1
    for n in range(1, 7):
        if f"current hld step: {n}" in blob.lower():
            step = n
            break
    if _stub_keep_existing(blob):
        return _stub_keep_payload("hld", step)
    feedback = _latest_feedback(blob)
    design = _stub_design_proposal(blob, feedback)
    diagram = _stub_hld_architecture_diagram(feedback) if step >= 4 else ""
    core_services = (
        "### IdentityService\n"
        "Owns User, Session, Credential. Bounded context: authentication and profile identity.\n"
        "- Operations: register, login, refresh session, read/update profile, revoke credentials.\n"
        "- Collaborators: called by the API gateway and peer services for authz checks.\n"
        "### ChannelService\n"
        "Owns Channel, ChannelMembership.\n"
        "- Operations: create channel, read metadata, update branding, manage memberships.\n"
        "- Collaborators: invoked by VideoCatalogService when attaching a video to a channel.\n"
        "### VideoCatalogService\n"
        "Owns Video, VideoMetadata, VisibilityPolicy.\n"
        "- Operations: register a video, read catalog metadata, update title/visibility, list by channel.\n"
        "- Collaborators: consumes upload-complete events; called by PlaybackService for authz metadata.\n"
        "### UploadService\n"
        "Owns UploadSession, UploadPart.\n"
        "- Operations: initiate multipart upload, complete parts, abort upload.\n"
        "- Collaborators: notifies VideoCatalogService when an original is stored.\n"
        "### PlaybackService\n"
        "Owns PlaybackSession (ephemeral), ManifestGrant.\n"
        "- Operations: authorize playback, issue time-limited CDN grants, record start-of-play.\n"
        "- Collaborators: reads VideoCatalogService metadata; media bytes live on CDN/object storage.\n"
        if step >= 3
        else ""
    )
    return {
        "updated_business_spec": _rich_spec(blob),
        "tradeoff_ledger": (
            "- CAP: prefer consistency on ownership/mutations; AP on view counters.\n"
            "- Pattern: API gateway + bounded-context services + async Kafka events.\n"
            "- Storage: Postgres for metadata (CP), object storage for media, Redis for hot reads.\n"
            + (
                "- Gateway rate limiting on writes to protect origin and auth.\n"
                if "rate limit" in feedback.lower()
                else ""
            )
        ),
        "scale_estimates": (
            "### Capacity plan\n"
            "- DAU: 50,000,000\n"
            "- Peak concurrent viewers: 5,000,000\n"
            "- Peak read QPS (metadata/playback auth): 120,000\n"
            "- Peak write/upload QPS: 8,000\n"
            "- Storage year-1 originals+renditions: 180 PB growth trajectory\n"
            "- Egress / bandwidth: multi-Tbps via CDN; origin pull << edge hit ratio 95%\n"
            "- Latency NFR: p99 playback start < 2s; catalog read p99 < 200ms\n"
            "- Availability SLA: 99.95% streaming control plane\n"
            if step >= 1
            else ""
        ),
        "core_microservices": core_services,
        "api_contracts": core_services,
        "communication_schemes": (
            "### User ↔ system\n"
            "- Browser/mobile clients use HTTPS request/response through the API Gateway (REST/JSON at the edge).\n"
            "- Video playback is stream-based: CDN byte-range and HLS/DASH manifests, not origin request/response for every segment.\n"
            "- Interactive notifications may use WebSocket streams from the gateway.\n"
            "### Core microservice ↔ core microservice\n"
            "- Sync request/response: IdentityService authz checks and VideoCatalogService metadata reads "
            "(gRPC preferred inside the mesh; REST acceptable).\n"
            "- Async pub/sub: Kafka topics VideoRegistered, UploadCompleted, TranscodeComplete, VisibilityChanged.\n"
            "### Core microservice ↔ infrastructure\n"
            "- Postgres: request/response SQL from services that own that data.\n"
            "- Redis: request/response cache/session lookups.\n"
            "- Object storage: signed URL PUT/GET (request/response).\n"
            "- CDN: pull-through cache plus cache-invalidation events.\n"
            "- Message broker: pub/sub for domain events; no shared DB writes across services.\n"
            if step >= 4
            else ""
        ),
        "fmea_notes": (
            "### FMEA\n"
            "| Failure mode | Impact | Mitigation |\n"
            "|---|---|---|\n"
            "| SPOF: primary Postgres metadata | Catalog/auth outage | Sync replicas + automated failover; read replicas for GET |\n"
            "| Bottleneck: transcoder backlog | Upload-to-playable latency spike | Priority queues + worker autoscaling + degraded lower-res publish |\n"
            "| Race: concurrent title edits | Lost updates | Conditional writes / etags on VideoCatalogService |\n"
            "| Split-brain on dual-writer cache | Stale authz | Single-writer keys; Redis with sticky ownership |\n"
            "| CDN origin overload | Playback errors | Multi-CDN + high cache TTLs + origin shields |\n"
            if step >= 5
            else ""
        ),
        "design_diagram": diagram,
        "design_diagram_lines": diagram.splitlines() if diagram else [],
        "design_justification": design["design_justification"] if step >= 4 else "",
        "ready_to_advance": True,
        "design_ready_to_approve": step >= 6,
        "assistant_message": (
            (
                "Gateway rate limiting is now part of this step: writes are capped at the "
                "edge so origin and auth are not flooded. A per-route token bucket on the "
                "API gateway was chosen over service-local limiters to keep one policy. "
                if "rate limit" in feedback.lower()
                else ("I applied your comments to this step. " if feedback else "")
            )
            + f"HLD step {step} draft ready. "
            + (
                "If this looks right, confirm, approve, or agree so we can run market evaluation and hand off."
                if step >= 6
                else "If this looks right, confirm, approve, or agree to advance, or tell me what to change."
            )
        ),
    }


def _stub_market_evaluation(blob: str) -> dict[str, Any]:
    return {
        "grade": "B",
        "grade_rationale": (
            "Clear problem framing with room to differentiate on workflow fit; "
            "several credible off-the-shelf alternatives exist."
        ),
        "summary": (
            "Solid niche idea. Prefer buying if your needs match a category leader; "
            "build when your workflow, data model, or compliance rules are the product."
        ),
        "report_markdown": (
            "# Market Evaluation Report\n\n"
            "## Idea grade\n\n"
            "**B** — Clear problem with credible alternatives; differentiation depends on "
            "workflow depth and constraints.\n\n"
            "## Executive summary\n\n"
            "Your approved specification describes a practical operational system. "
            "Popular SaaS and open-source options can cover generic create/read/update and "
            "reporting needs. Building your own is justified when invariants, offline needs, "
            "or domain rules are a durable advantage.\n\n"
            "## Popular alternatives found\n\n"
            "- [Open-source alternative](https://example.com/open-source-alternative) — "
            "extensible core workflows; more integration work.\n"
            "- [SaaS category leader](https://example.com/saas-leader) — fast time-to-value; "
            "weaker deep customization.\n"
            "- [Build-vs-buy notes](https://example.com/build-vs-buy) — buy undifferentiated "
            "capability; build strategic workflow.\n\n"
            "## Comparison vs your spec\n\n"
            "Alternatives typically win on speed and ecosystem. Your spec wins if critical "
            "invariants, actor jobs, or offline/lot-tracking rules are stricter than commodity tools.\n\n"
            "## When you should use an existing alternative\n\n"
            "- Needs map cleanly to a common category product\n"
            "- Time-to-value and ops cost dominate\n"
            "- Differentiation is not in the core workflow engine\n\n"
            "## When you should build your own\n\n"
            "- Spec invariants are product-defining\n"
            "- Existing tools force painful workarounds for primary actors\n"
            "- Data model / compliance constraints are unique and durable\n\n"
            "## Risks if you build\n\n"
            "- Longer path to a trustworthy v1\n"
            "- Ongoing ownership of undifferentiated features\n"
            "- Competition from cheaper packaged tools\n\n"
            "## Recommended next step\n\n"
            "If building, proceed to system design with explicit non-goals that commodity tools "
            "already cover. If buying, shortlist 2 alternatives and prove fit against your "
            "invariants checklist.\n\n"
            "## Sources\n\n"
            "- https://example.com/open-source-alternative\n"
            "- https://example.com/saas-leader\n"
            "- https://example.com/build-vs-buy\n"
        ),
    }


def _labeled_user_text(blob: str) -> str:
    """Last labeled user turn in a system+user stub blob (ignore the same words in rules)."""
    for marker in (
        "Latest user message:",
        "Latest user feedback to apply now:",
        "User's requested changes:",
        "User's final response:",
        "User answer:",
        "User question:",
        "User message:",
    ):
        if marker in blob:
            part = blob.rsplit(marker, 1)[1].strip()
            for stop in ("Return the full", "Respond ONLY", "Return JSON", "Recent ", "Spec to compress"):
                if stop in part:
                    part = part.split(stop, 1)[0]
            text = part.strip()
            if text and not text.startswith("(none"):
                return text
    users = [line.split(":", 1)[1].strip() for line in blob.splitlines() if line.startswith("USER:")]
    return users[-1] if users else ""


def _latest_feedback(blob: str) -> str:
    return _labeled_user_text(blob)


def _fold_answer_stub(blob: str) -> str:
    from architect_agent.interview_progress import (
        append_spec_bullet,
        guess_spec_section,
        living_spec_scaffold,
        record_dropped_constraints,
    )
    from architect_agent.scope import ensure_standalone_spec

    spec = living_spec_scaffold(_extract_spec(blob))
    answer = _labeled_user_text(blob)
    if not answer:
        return spec
    updated = append_spec_bullet(spec, guess_spec_section(answer), answer[:400])
    updated = ensure_standalone_spec(updated, answer)
    return record_dropped_constraints(updated, answer)


def _compact_spec_stub(blob: str) -> str:
    spec = blob
    if "Spec to compress:" in blob:
        spec = blob.split("Spec to compress:", 1)[1].strip()
    # Drop repeated interview-notes style appendices and keep structured core.
    cleaned = re.sub(r"\n## Interview notes[\s\S]*?(?=\n## |\Z)", "\n", spec)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    if estimate := len(cleaned):
        # Deterministic shrink: keep head sections if absurdly long.
        if estimate > 12000:
            cleaned = cleaned[:8000].rstrip() + "\n"
    return cleaned or _rich_spec(blob)


def _compact_justification_stub(blob: str) -> str:
    text = blob
    if "Justification to compress:" in blob:
        text = blob.split("Justification to compress:", 1)[1].strip()
    sections = re.findall(r"(### [^\n]+\n[^\n]+)", text)
    if sections:
        return "\n\n".join(sections[:12])
    return text[:4000]


def _stub_design_proposal(blob: str, feedback: str) -> dict[str, Any]:
    lower_fb = feedback.lower()
    first_draft = "(none" in blob.lower() and "produce first draft" in blob.lower()

    nodes = [
        ("UI", "Web UI", "Operator-facing surface for day-to-day work."),
        ("API", "API Gateway", "Authn/authz and routing edge."),
        ("Core", "Core Domain Service", "Owns primary business invariants."),
        ("DB", "Database", "System of record for domain entities."),
        ("Notify", "Notification Worker", "Async outbound notifications; failure is best-effort."),
    ]
    edges = [("UI", "API"), ("API", "Core"), ("Core", "DB"), ("Core", "Notify")]
    changes: list[str] = []

    if "monolith" in lower_fb:
        nodes = [
            ("App", "Modular Monolith", "Single deployable with internal domain modules."),
            ("DB", "Database", "System of record for domain entities."),
        ]
        edges = [("App", "DB")]
        changes.append("Switched to a modular monolith")
    if "cache" in lower_fb or "redis" in lower_fb:
        nodes.append(("Cache", "Cache (Redis)", "Low-latency read cache in front of hot paths."))
        edges.append(("Core", "Cache") if any(n[0] == "Core" for n in nodes) else ("App", "Cache"))
        changes.append("Added a cache component")
    if "search" in lower_fb:
        nodes.append(("Search", "Search Service", "Indexed search over domain records."))
        edges.append(("API", "Search") if any(n[0] == "API" for n in nodes) else ("App", "Search"))
        changes.append("Added a search service")
    if "remove notification" in lower_fb or "drop notify" in lower_fb or "no notification" in lower_fb:
        nodes = [n for n in nodes if n[0] != "Notify"]
        edges = [e for e in edges if "Notify" not in e]
        changes.append("Removed notification worker")
    if "auth" in lower_fb and "service" in lower_fb:
        nodes.append(("Auth", "Auth Service", "Identity, sessions, and token issuance."))
        edges.append(("API", "Auth") if any(n[0] == "API" for n in nodes) else ("App", "Auth"))
        changes.append("Added a dedicated auth service")

    # Always reflect non-empty freeform feedback in the draft label for visibility.
    if feedback and not feedback.startswith("(none") and not changes:
        nodes.append(("Note", "Design Note", f"Incorporated feedback: {feedback[:120]}"))
        anchor = nodes[0][0]
        edges.append((anchor, "Note"))
        changes.append(f"Incorporated feedback: {feedback[:80]}")

    lines = ["flowchart LR"]
    for key, label, _ in nodes:
        lines.append(f"  {key}[{label}]")
    for a, b in edges:
        lines.append(f"  {a} --> {b}")

    justification = "\n\n".join(f"### {label}\n{desc}" for _, label, desc in nodes)
    if first_draft and not changes:
        assistant = (
            "Here is the first draft of the system design. Chat to refine components, "
            "boundaries, or style — the diagram will update with each message."
        )
        change_text = "Initial draft"
    else:
        change_text = "; ".join(changes) if changes else "Refined wording and kept structure"
        assistant = (
            f"Updated the design based on your feedback. {change_text}. "
            "Keep chatting to refine further, or finalize when ready."
        )

    return {
        "design_diagram": "\n".join(lines),
        "design_justification": justification,
        "assistant_message": assistant,
        "style": "monolithic" if "monolith" in lower_fb else "distributed",
        "changes_made": change_text,
    }


def _extract_spec(blob: str) -> str:
    for marker in (
        "Current living specification:",
        "Current business specification markdown:",
        "Living specification:",
    ):
        if marker in blob:
            part = blob.split(marker, 1)[1]
            for stop in (
                "Latest user message:",
                "Interview checklist:",
                "Interview questions and answers:",
                "Recent turns",
                "Recent conversation",
                "Recent phase0",
                "Recent Phase 0",
                "Last assistant",
                "User answer",
                "Approved business",
                "DISCUSSION MEMORY",
            ):
                if stop in part:
                    part = part.split(stop, 1)[0]
            return part.strip() or "# Business Specification\n\n(WIP)\n"
    if "Spec:" in blob:
        part = blob.split("Spec:", 1)[1]
        for stop in (
            "Latest user message:",
            "Last assistant",
            "User answer",
            "Approved business",
        ):
            if stop in part:
                part = part.split(stop, 1)[0]
        return part.strip() or "# Business Specification\n\n(WIP)\n"
    return "# Business Specification\n\n(WIP)\n"


def _rich_spec(blob: str) -> str:
    base = _extract_spec(blob)
    if "## Problem" in base:
        return base
    return (
        "# Business Specification\n\n"
        "## Problem\n"
        "Build the described software system with clear domain boundaries.\n\n"
        "## Actors\n"
        "- Primary operator role (to be refined via interview)\n\n"
        "## Goals\n"
        "- Deliver a trustworthy v1 that protects critical invariants\n\n"
        "## In scope (v1)\n"
        "- Core create/read/update flows\n"
        "- Basic reporting\n\n"
        "## Out of scope\n"
        "- Adjacent enterprise integrations not required for v1\n\n"
        "## Critical invariants\n"
        "- Authoritative data must not be silently corrupted\n\n"
        "## Success criteria\n"
        "- Operators can complete primary job without workarounds\n\n"
        "## Assumptions & risks\n"
        "- Single-tenant deployment unless otherwise decided\n"
    )


@lru_cache
def get_chat_model() -> BaseChatModel:
    logger.info("get_chat_model() called")
    settings = get_settings()
    provider = settings.llm_provider
    model = settings.llm_model
    temperature = settings.llm_temperature

    logger.info("LLM config: provider=%s, model=%s, temperature=%s", provider, model, temperature)
    logger.info("SSL_VERIFY setting: %s", settings.ssl_verify)

    if not model:
        logger.error("LLM_MODEL is required in .env")
        raise ValueError("LLM_MODEL is required in .env")

    if provider == "stub":
        logger.info("Using StubChatModel")
        return StubChatModel()

    if provider == "openai":
        logger.info("Using OpenAI provider")
        from langchain_openai import ChatOpenAI

        if settings.openai_api_key:
            logger.info("Using OpenAI API key")
            http_client = httpx.Client(verify=settings.ssl_verify)
            http_async_client = httpx.AsyncClient(verify=settings.ssl_verify)
            return ChatOpenAI(
                model=model,
                api_key=settings.openai_api_key,
                temperature=temperature,
                http_client=http_client,
                http_async_client=http_async_client,
            )

        if settings.aia_gateway_client_id and settings.aia_gateway_client_secret and settings.aia_gateway_base_url:
            logger.info("Using AIA Gateway")
            from architect_agent.utils.auth import build_http_clients

            http_client, http_async_client = build_http_clients(
                settings.aia_gateway_client_id,
                settings.aia_gateway_client_secret,
                verify=settings.ssl_verify,
            )
            return ChatOpenAI(
                model=model,
                base_url=settings.aia_gateway_base_url,
                temperature=settings.llm_temperature,
                request_timeout=120,
                http_client=http_client,
                http_async_client=http_async_client
            )

        if settings.reallm_base_url and settings.reallm_api_key:
            logger.info("Using RealLLM proxy: %s", settings.reallm_base_url)
            http_client = httpx.Client(verify=settings.ssl_verify)
            http_async_client = httpx.AsyncClient(verify=settings.ssl_verify)
            return ChatOpenAI(
                model=model,
                base_url=settings.reallm_base_url,
                api_key=settings.reallm_api_key,
                temperature=temperature,
                http_client=http_client,
                http_async_client=http_async_client,
            )
        
        logger.error("No valid OpenAI configuration found")
        raise ValueError("No valid OpenAI configuration found in .env")

    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic

        if settings.anthropic_api_key:
            return ChatAnthropic(
                model=model,
                api_key=settings.anthropic_api_key,
                temperature=temperature,
            )
        

    if provider == "ollama":
        from langchain_ollama import ChatOllama

        if settings.ollama_base_url:
            return ChatOllama(
                model=model,
                base_url=settings.ollama_base_url,
                temperature=temperature,
            )

    raise RuntimeError("Failed to initialize LLM client")
