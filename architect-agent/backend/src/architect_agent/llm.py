from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from architect_agent.config import get_settings

from utils.auth import build_http_clients


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

        if "update the business specification" in lower or "from one interview answer" in lower:
            payload = {
                "updated_business_spec": _extract_spec(blob)
                + "\n\n## Interview notes\n- Captured latest user answer.\n"
            }
        elif "system design node" in lower or "design_diagram" in lower:
            payload = {
                "design_diagram": (
                    "flowchart LR\n"
                    "  UI[Web UI] --> API[API Gateway]\n"
                    "  API --> Core[Core Domain Service]\n"
                    "  Core --> DB[(Database)]\n"
                    "  Core --> Notify[Notification Worker]"
                ),
                "design_justification": (
                    "### Web UI\nOperator-facing surface for day-to-day work.\n\n"
                    "### API Gateway\nAuthn/authz and routing edge.\n\n"
                    "### Core Domain Service\nOwns primary business invariants.\n\n"
                    "### Database\nSystem of record for domain entities.\n\n"
                    "### Notification Worker\nAsync outbound notifications; failure is best-effort."
                ),
                "assistant_message": (
                    "Proposed a modular distributed slice. Chat to refine boundaries, "
                    "or finalize when this matches your intent."
                ),
                "style": "distributed",
            }
        else:
            ready = turns >= 2 or "actors:" in lower and "out of scope" in lower
            if ready:
                payload = {
                    "ready_for_design": True,
                    "updated_business_spec": _rich_spec(blob),
                    "assistant_message": (
                        "The readiness checklist looks covered. You can keep adding detail, "
                        "or approve to move into system design."
                    ),
                    "rationale": "Actors, scope, invariants, and non-goals are present.",
                }
            else:
                questions = [
                    "❓ **Primary actors**: Who uses this system day-to-day, and what job are they hiring it to do?\n\n➡️ (Recommended) Name 1–2 concrete roles with a single primary job each.",
                    "❓ **Critical invariants**: What must never go wrong (money, safety, compliance, trust)?\n\n➡️ (Recommended) List the top 1–3 invariants in plain language.",
                    "❓ **V1 scope vs non-goals**: What is explicitly in v1, and what is out?\n\n➡️ (Recommended) 3 in-scope capabilities and 2 explicit non-goals.",
                ]
                payload = {
                    "ready_for_design": False,
                    "updated_business_spec": _rich_spec(blob),
                    "assistant_message": questions[min(turns, len(questions) - 1)],
                    "rationale": "Need more crisp decisions before design.",
                }

        return ChatResult(
            generations=[ChatGeneration(message=AIMessage(content=json.dumps(payload)))]
        )


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
            http_client, http_async_client = build_http_clients(settings.aia_gateway_client_id, settings.aia_gateway_client_secret)
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
