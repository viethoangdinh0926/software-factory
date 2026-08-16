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

from orchestrator_agent.config import get_settings

logger = logging.getLogger(__name__)


class StubChatModel(BaseChatModel):
    """Deterministic offline model so the orchestrator graph can run without API keys."""

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
        if "orchestrator topology classifier" in lower:
            payload = _stub_topology(blob)
        elif "orchestrator service matcher" in lower:
            payload = _stub_match(blob)
        elif "orchestrator service extractor" in lower:
            payload = _stub_extract(blob)
        elif "orchestrator api type advisor" in lower:
            payload = _stub_api_type(blob)
        elif "orchestrator api design proposer" in lower:
            payload = _stub_api_design(blob)
        elif "orchestrator tech stack advisor" in lower:
            payload = _stub_tech_stack(blob)
        else:
            payload = {
                "assistant_message": "Stub orchestrator is ready. Approve to continue.",
                "ready": True,
            }
        return ChatResult(
            generations=[ChatGeneration(message=AIMessage(content=json.dumps(payload)))]
        )


def _track_from_blob(blob: str) -> str:
    match = re.search(r"Track:\s*`?(lld|hld)`?", blob, re.I)
    if match:
        return match.group(1).lower()
    lower = blob.lower()
    if "architect_track" in lower and "lld" in lower and "hld" not in lower.split("architect_track", 1)[-1][:80]:
        return "lld"
    if re.search(r"\blld\b", lower) and "microservice" not in lower:
        if "hld" not in lower:
            return "lld"
    if "microservice" in lower or "distributed" in lower or "hld" in lower:
        return "hld"
    return "hld"


def _stub_topology(blob: str) -> dict[str, Any]:
    focus = blob
    if "Architect track:" in blob:
        focus = blob.split("Architect track:", 1)[1]
    track = _track_from_blob(focus)
    lower = focus.lower()
    if "single process" in lower or "library" in lower or track == "lld":
        if "microservice" not in lower and "distributed" not in lower:
            return {
                "topology": "standalone",
                "certain": True,
                "rationale": "LLD / single-process signals a standalone application.",
                "assistant_message": (
                    "This looks like a **stand-alone application**. I will research a tech stack "
                    "next. Approve if that topology is right, or chat to correct it."
                ),
            }
    return {
        "topology": "distributed",
        "certain": True,
        "rationale": "HLD / microservice signals a distributed system.",
        "assistant_message": (
            "This looks like a **distributed system**. I will extract core microservices "
            "and plan each one with you. Approve if that topology is right, or chat to correct it."
        ),
    }


def _service_names_from_blob(blob: str) -> list[tuple[str, str]]:
    names = re.findall(r"\b([A-Z][A-Za-z0-9]+Service)\b", blob)
    ordered: list[tuple[str, str]] = []
    seen: set[str] = set()
    for name in names:
        if name in seen:
            continue
        seen.add(name)
        role = re.sub(r"Service$", "", name)
        role_key = re.sub(r"(?<!^)(?=[A-Z])", "-", role).lower()
        ordered.append((name, role_key or name.lower()))
    if ordered:
        return ordered
    lower = blob.lower()
    if "youtube" in lower or "video" in lower:
        return [
            ("IdentityService", "identity"),
            ("VideoCatalogService", "video-catalog"),
        ]
    return [("CoreDomainService", "core-domain")]


def _stub_extract(blob: str) -> dict[str, Any]:
    services = []
    for name, role_key in _service_names_from_blob(blob):
        services.append(
            {
                "name": name,
                "role_key": role_key,
                "contract_summary": f"HTTP API for {name} as described in the architect package.",
            }
        )
    return {
        "services": services,
        "assistant_message": (
            "Core microservices: "
            + ", ".join(s["name"] for s in services)
            + ". Planning them one at a time."
        ),
    }


def _stub_match(blob: str) -> dict[str, Any]:
    """Match new extracted services to previous UUIDs by role_key, not name."""
    prev_block = blob
    if "PREVIOUS_SERVICES_JSON:" in blob:
        prev_block = blob.split("PREVIOUS_SERVICES_JSON:", 1)[1]
        prev_block = prev_block.split("NEW_SERVICES_JSON:", 1)[0]
    new_block = blob
    if "NEW_SERVICES_JSON:" in blob:
        new_block = blob.split("NEW_SERVICES_JSON:", 1)[1]

    def _load_list(text: str) -> list[dict[str, Any]]:
        start = text.find("[")
        if start < 0:
            return []
        try:
            data, _ = json.JSONDecoder().raw_decode(text[start:])
        except json.JSONDecodeError:
            return []
        return data if isinstance(data, list) else []

    previous = _load_list(prev_block)
    new_services = _load_list(new_block)
    matches: list[dict[str, Any]] = []
    used_prev: set[str] = set()
    for svc in new_services:
        role = str(svc.get("role_key") or "").lower()
        name = str(svc.get("name") or "")
        matched_id = ""
        for prev in previous:
            pid = str(prev.get("microservice_id") or "")
            if not pid or pid in used_prev:
                continue
            prev_role = str(prev.get("role_key") or "").lower()
            prev_names = [str(n).lower() for n in (prev.get("names") or [])]
            if role and prev_role and (role == prev_role or role in prev_role or prev_role in role):
                matched_id = pid
                break
            if name.lower() in prev_names:
                matched_id = pid
                break
            stem = name.lower().replace("service", "")
            if stem and any(stem in n or n.replace("service", "") in stem for n in prev_names):
                matched_id = pid
                break
        if matched_id:
            used_prev.add(matched_id)
        matches.append(
            {
                "name": name,
                "role_key": role,
                "microservice_id": matched_id,
                "contract_summary": svc.get("contract_summary") or "",
            }
        )
    removed = [
        str(prev.get("microservice_id"))
        for prev in previous
        if str(prev.get("microservice_id") or "") not in used_prev
        and prev.get("status") != "suspended"
    ]
    return {"matches": matches, "removed_microservice_ids": removed}


def _stub_api_type(blob: str) -> dict[str, Any]:
    lower = blob.lower()
    architect_type = "REST"
    if "grpc" in lower:
        architect_type = "gRPC"
    elif "graphql" in lower:
        architect_type = "GraphQL"
    recommendation = "keep"
    proposed = architect_type
    if "change to grpc" in lower or "use grpc" in lower:
        recommendation = "change"
        proposed = "gRPC"
    return {
        "architect_api_type": architect_type,
        "recommended_api_type": proposed,
        "recommendation": recommendation,
        "rationale": (
            f"Similar services typically expose {proposed}. "
            f"Architect package reads as {architect_type}."
        ),
        "assistant_message": (
            f"Architect contract looks like **{architect_type}**. Similar services usually use "
            f"**{proposed}**. Recommendation: **{recommendation}**. Chat to change, or approve "
            "to lock the API type."
        ),
        "used_live_search": False,
    }


def _stub_api_design(blob: str) -> dict[str, Any]:
    name = "Service"
    match = re.search(r"Service name:\s*([A-Za-z0-9]+)", blob)
    if match:
        name = match.group(1)
    else:
        found = re.findall(r"\b([A-Z][A-Za-z0-9]+Service)\b", blob)
        if found:
            name = found[-1]
    slug = re.sub(r"Service$", "", name)
    slug = re.sub(r"(?<!^)(?=[A-Z])", "-", slug).lower() or "resource"
    api_type = "REST"
    if "grpc" in blob.lower() and "recommended_api_type" in blob.lower():
        api_type = "gRPC"
    if re.search(r"locked api type:\s*`?grpc", blob, re.I):
        api_type = "gRPC"
    design = (
        f"## {name} API design ({api_type})\n\n"
        f"### `POST /v1/{slug}`\n"
        "Create the primary resource. Validates caller identity via Identity (or local auth), "
        "writes the system of record, and emits a domain event other services may consume.\n\n"
        f"### `GET /v1/{slug}/{{id}}`\n"
        "Read the resource by id. Returns 404 when missing. May fan out to a catalog or metadata "
        "peer when the record is a pointer.\n\n"
        f"### `PATCH /v1/{slug}/{{id}}`\n"
        "Partial update of mutable fields. Enforces ownership; does not silently clobber "
        "concurrent writes (etag / version).\n\n"
        f"### `DELETE /v1/{slug}/{{id}}`\n"
        "Soft-delete or tombstone. Downstream consumers (search, CDN invalidation) are notified.\n"
    )
    return {
        "api_design": design,
        "assistant_message": (
            f"Proposed {api_type} design for **{name}** with business logic per endpoint, "
            "including peer-service calls. Chat to refine, or approve the API design."
        ),
    }


def _stub_tech_stack(blob: str) -> dict[str, Any]:
    lower = blob.lower()
    if "java" in lower or "spring" in lower:
        stack = (
            "## Tech stack\n"
            "- Language: Java 21\n"
            "- API: Spring Boot 3 + OpenAPI\n"
            "- Data: PostgreSQL, Redis cache\n"
            "- Build: Gradle\n"
            "- Tests: JUnit 5 + Testcontainers\n"
        )
    else:
        stack = (
            "## Tech stack\n"
            "- Language: Python 3.12\n"
            "- API: FastAPI + Pydantic v2 + OpenAPI\n"
            "- Data: PostgreSQL, Redis cache, S3-compatible object store when media is needed\n"
            "- Async: Kafka or NATS for domain events\n"
            "- Tests: pytest + httpx\n"
        )
    return {
        "tech_stack": stack,
        "search_notes": "Used canned popular-stack knowledge (live search skipped in stub).",
        "assistant_message": (
            "Proposed a concrete tech stack for this unit. Chat to swap languages or stores, "
            "or approve the plan so I can send a plan spec to the engineer."
        ),
        "used_live_search": False,
    }


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
            from orchestrator_agent.utils.auth import build_http_clients

            http_client, http_async_client = build_http_clients(
                settings.aia_gateway_client_id,
                settings.aia_gateway_client_secret,
                verify=settings.ssl_verify,
            )
            return ChatOpenAI(
                model=model,
                base_url=settings.aia_gateway_base_url,
                temperature=temperature,
                request_timeout=120,
                http_client=http_client,
                http_async_client=http_async_client,
            )
        if settings.reallm_base_url and settings.reallm_api_key:
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
