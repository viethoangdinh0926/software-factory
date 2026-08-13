from __future__ import annotations

import json
import re
from functools import lru_cache
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from architect_agent.config import get_settings


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
        turns = blob.count("User answer:") + blob.count("USER:")

        if "compress a living business specification" in lower or "spec to compress:" in lower:
            payload = {"updated_business_spec": _compact_spec_stub(blob)}
        elif "compress a system-design justification" in lower or "justification to compress:" in lower:
            payload = {"design_justification": _compact_justification_stub(blob)}
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
        elif "update the living business specification" in lower or (
            "update the business specification" in lower or "from one interview answer" in lower
        ):
            # Merge without growing an unbounded interview-notes appendix.
            payload = {"updated_business_spec": _fold_answer_stub(blob)}
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


def _latest_feedback(blob: str) -> str:
    marker = "Latest user feedback to apply now:"
    if marker in blob:
        part = blob.split(marker, 1)[1].strip()
        for stop in ("Return the full", "Respond ONLY"):
            if stop in part:
                part = part.split(stop, 1)[0]
        return part.strip()
    # Fallback: last USER: line from conversation
    users = [line.split(":", 1)[1].strip() for line in blob.splitlines() if line.startswith("USER:")]
    return users[-1] if users else ""


def _fold_answer_stub(blob: str) -> str:
    spec = _extract_spec(blob)
    answer = ""
    if "User answer:" in blob:
        answer = blob.split("User answer:", 1)[1].strip()
        for stop in ("Respond ONLY", "Return JSON", "Spec to compress"):
            if stop in answer:
                answer = answer.split(stop, 1)[0].strip()
    if not answer:
        return _rich_spec(blob if "## Problem" not in spec else spec)
    # Keep a single rolling decision line instead of appending forever.
    line = f"- Latest clarified decision: {answer[:180]}"
    if "## Goals" in spec:
        if "Latest clarified decision:" in spec:
            return re.sub(
                r"- Latest clarified decision:.*",
                line,
                spec,
                count=1,
            )
        return spec.replace("## Goals", f"## Goals\n{line}\n", 1)
    return spec.rstrip() + f"\n\n## Goals\n{line}\n"


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
    marker = "Current business specification markdown:"
    if marker in blob:
        part = blob.split(marker, 1)[1]
        for stop in ("Recent turns", "Recent conversation", "Last assistant", "User answer", "Approved business"):
            if stop in part:
                part = part.split(stop, 1)[0]
        return part.strip() or "# Business Specification\n\n(WIP)\n"
    if "Spec:" in blob:
        return blob.split("Spec:", 1)[1].split("Last assistant", 1)[0].strip()
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
    settings = get_settings()
    provider = settings.llm_provider
    model = settings.llm_model
    temperature = settings.llm_temperature

    if not model:
        raise ValueError("LLM_MODEL is required in .env")

    if provider == "stub":
        return StubChatModel()

    if provider == "openai":
        from langchain_openai import ChatOpenAI

        if settings.openai_api_key:
            return ChatOpenAI(
                model=model,
                api_key=settings.openai_api_key,
                temperature=temperature,
            )

        if settings.aia_gateway_client_id and settings.aia_gateway_client_secret and settings.aia_gateway_base_url:
            from architect_agent.utils.auth import build_http_clients

            http_client, http_async_client = build_http_clients(
                settings.aia_gateway_client_id,
                settings.aia_gateway_client_secret,
            )
            return ChatOpenAI(
                model=model,
                base_url=settings.aia_gateway_base_url,
                temperature=settings.llm_temperature,
                request_timeout=120,
                http_client=http_client,
                http_async_client=http_async_client
            )

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

    if settings.reallm_base_url and settings.reallm_api_key:
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(
            model=model,
            base_url=settings.reallm_base_url,
            api_key=settings.reallm_api_key,
            temperature=temperature,
        )

    raise RuntimeError("Failed to initialize LLM client")
