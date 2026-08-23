from __future__ import annotations

import json
import logging
import re
from functools import lru_cache
from typing import Any

import httpx
import os
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
        if "user turn intent classifier" in lower:
            payload = _stub_turn_intent(blob)
        elif "answering a question about" in lower:
            payload = {
                "assistant_message": _stub_qa(blob),
                "api_design": "",
                "tech_stack": "",
            }
        elif "orchestrator topology classifier" in lower:
            payload = _stub_topology(blob)
        elif "orchestrator service matcher" in lower:
            payload = _stub_match(blob)
        elif "orchestrator service extractor" in lower:
            payload = _stub_extract(blob)
        elif "orchestrator feature advisor" in lower:
            payload = _stub_features(blob)
        elif "orchestrator relationship advisor" in lower:
            payload = _stub_relations(blob)
        elif "orchestrator communication advisor" in lower:
            payload = _stub_relations(blob)
        elif "orchestrator api type advisor" in lower:
            payload = _stub_relations(blob)
        elif "orchestrator api design proposer" in lower:
            payload = _stub_relations(blob)
        elif "orchestrator spec update advisor" in lower:
            payload = _stub_spec_update(blob)
        elif "orchestrator tech stack advisor" in lower:
            payload = _stub_tech_stack(blob)
        else:
            payload = {
                "assistant_message": "Stub orchestrator is ready. If this looks right, confirm, approve, or agree so we can continue.",
                "ready": True,
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


def _stub_turn_intent(blob: str) -> dict[str, str]:
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
    if re.search(r"\b(pause|stop execution|hold on)\b", compact) and "approve" not in compact:
        return {"category": "command", "action": "pause"}
    if re.search(r"\b(execute|run the plan|start coding)\b", compact):
        return {"category": "command", "action": "execute"}
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


def _stub_qa(blob: str) -> str:
    lower = blob.lower()
    ask = lower
    if "user question:" in lower:
        ask = lower.split("user question:", 1)[-1]
    elif "latest user message:" in lower:
        ask = lower.split("latest user message:", 1)[-1]
    if _stub_help_answering_questions(ask):
        return (
            "Here are potential answers you can use or edit. "
            "I am not treating this as your decision yet.\n\n"
            "### The open question I asked\n"
            "- (Recommended) A concrete default based on the current artifacts.\n"
            "- A more conservative alternative.\n"
            "- A more ambitious alternative.\n"
        )
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
            return "Agreed URL endpoints:\n" + "\n".join(f"- `{item}`" for item in uniq[:24])
    if "feature" in ask or "capabilit" in ask:
        return (
            "The current v1 feature list on this step is unchanged. I can walk through "
            "each capability, who it is for, and what is out of v1 if you want a specific line."
        )
    if "relationship" in ask or "entity" in ask or "who initiates" in ask:
        return (
            "The current entity relationship map on this tile is unchanged. I can walk "
            "through each related user, peer service, or infra component and who initiates "
            "that link. Protocols and APIs are not locked here."
        )
    if "rest" in ask or "grpc" in ask or "graphql" in ask or "protocol" in ask or "communication" in ask:
        return (
            "This tile does not lock REST vs gRPC vs topics. Related entities and who "
            "initiates each link are in the relationship map. Engineer sub-agents choose "
            "protocols when they consult each other."
        )
    if "stack" in ask or "python" in ask or "java" in ask:
        return "The current tech stack on this step is unchanged. I can quote language, framework, and datastore if you want a specific line."
    if "standalone" in ask or "distributed" in ask or "topology" in ask:
        return "The proposed topology is based on the architect track and package. I have not changed the classification."
    return "Answered from the current artifacts on this step. Ask about a specific detail if you want more."


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
                    "This looks like a **stand-alone application**. I will discuss features "
                    "next, then the tech stack. Approve if that topology is right, or chat to correct it."
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


def _stub_features(blob: str) -> dict[str, Any]:
    name = "application"
    focus = re.search(r"Focus microservice:\s*([A-Za-z0-9]+)", blob)
    if focus:
        name = focus.group(1)
    else:
        found = re.findall(r"\b([A-Z][A-Za-z0-9]+Service)\b", blob)
        if found:
            name = found[0]
    spec = (
        f"## v1 capabilities for {name}\n\n"
        "- Authenticate callers: issue and validate credentials; reject expired or revoked "
        "tokens with a clear error and do not leak whether the account exists.\n"
        "- Create the primary resource: validate input, persist the system of record, and "
        "return the created identity so callers can follow up.\n"
        "- Read the primary resource by id: return 404 when missing; include ownership and "
        "status fields the UI or peer services need.\n"
        "- Update mutable fields: enforce ownership; do not silently clobber concurrent writes "
        "(etag or version check).\n"
        "- Soft-delete or deactivate: keep an audit trail and notify collaborators that the "
        "record is gone.\n\n"
        "## Out of v1\n\n"
        "- Multi-region replication, cross-tenant analytics, and unrelated product surfaces.\n\n"
        "## Collaborators\n\n"
        "- Name peer services only when this unit must invoke them; do not design their internals.\n\n"
        "## Assumptions\n\n"
        "- The architect package is a sketch. v1 covers that sketch plus the usual lifecycle "
        "around the primary resource.\n"
    )
    return {
        "feature_spec": spec,
        "ready_for_features": True,
        "assistant_message": (
            f"Here is a thorough v1 feature list for **{name}**. The architect package was only "
            "a sketch — walk through each capability with me, then Approve features when it is "
            "complete."
        ),
    }


def _stub_extract_marked(blob: str, marker: str) -> str:
    lower = blob.lower()
    key = marker.lower()
    if key not in lower:
        return ""
    start = lower.index(key) + len(key)
    chunk = blob[start:].lstrip("\n")
    for stop in (
        "\nPrior bugs",
        "\nCurrent bugs",
        "\nWeb search",
        "\nLast shipped",
        "\nNext spec",
        "\nLatest user message",
        "\nPeer core",
    ):
        idx = chunk.find(stop)
        if idx >= 0:
            chunk = chunk[:idx]
    body = chunk.strip()
    if body.lower() in {"(none)", "none"}:
        return ""
    return body


def _stub_spec_update(blob: str) -> dict[str, Any]:
    name = "Service"
    focus = re.search(r"Focus microservice:\s*([A-Za-z0-9]+)", blob)
    if focus:
        name = focus.group(1)
    pending = blob.split("Latest user message:", 1)[-1] if "Latest user message:" in blob else blob
    lower = pending.lower()
    features = _stub_extract_marked(blob, "Prior agreed features for this service:")
    if not features:
        features = _stub_extract_marked(blob, "Agreed features / functionality:")
    if not features:
        features = str(_stub_features(blob).get("feature_spec") or "")
    bugs = _stub_extract_marked(blob, "Prior bugs for this service:")
    if not bugs:
        bugs = _stub_extract_marked(blob, "Current bugs:")
    changes: list[str] = []
    if "health check" in lower:
        if "health check" not in features.lower():
            features = features.rstrip() + (
                "\n- Health check: expose a liveness probe so callers and infra can "
                "tell whether this service is ready to take traffic.\n"
            )
        changes.append("Added a health check capability.")
    if "bug" in lower or "lockout" in lower:
        if "lockout" not in bugs.lower():
            bugs = (
                (bugs.rstrip() + "\n\n" if bugs else "")
                + "## Login lockout\n\n"
                "- Existing accounts can be brute-forced; add lockout after repeated "
                "failed authentications and a clear unlock path.\n"
            )
        else:
            bugs = bugs.rstrip() + (
                "\n- Update: lockout must persist across instances and reset only via "
                "the documented unlock path.\n"
            )
        changes.append("Recorded an authentication lockout bug.")
    if not changes:
        extra = re.sub(r"\s+", " ", pending.strip())[:160]
        if extra and extra.lower() not in {"(none)", "none"} and extra.lower() not in features.lower():
            features = features.rstrip() + f"\n- Spec update: {extra}\n"
            changes.append("Applied the requested spec update.")
    changelog = "\n".join(f"- {line}" for line in changes) or "- No spec changes."
    return {
        "feature_spec": features,
        "bug_spec": bugs,
        "spec_changelog": changelog,
        "assistant_message": (
            f"Drafted an incremental spec update for **{name}**. Features and bugs from "
            "the last shipped version stay unless this increment changes them. Confirm, "
            "approve, or agree to send a new spec version to the engineer."
        ),
    }


def _stub_relations(blob: str) -> dict[str, Any]:
    name = "Service"
    focus = re.search(r"Focus microservice:\s*([A-Za-z0-9]+)", blob)
    if focus:
        name = focus.group(1)
    else:
        found = re.findall(r"\b([A-Z][A-Za-z0-9]+Service)\b", blob)
        if found:
            name = found[-1]
    peers = [n for n in re.findall(r"\b([A-Z][A-Za-z0-9]+Service)\b", blob) if n != name]
    peer = peers[0] if peers else "PeerDomainService"
    spec = (
        f"## Entity relationships for {name}\n\n"
        "### User (kind: user)\n"
        "- We initiate: no. Callers and API gateways initiate toward this service.\n"
        "- Relationship: users send commands and queries this service owns (register, "
        "read, update). This service returns owned-resource state; it does not prescribe "
        "HTTP vs gRPC here.\n\n"
        f"### {peer} (kind: core_microservice)\n"
        "- We initiate: yes when this service must fetch collaborator state or notify it "
        "of a domain event it produced.\n"
        "- Relationship: this service depends on the collaborator for authorization checks "
        "or owned-object lookups. The peer's offered API is owned by that service's "
        "engineer sub-agent, not this plan.\n"
        "- Data this service needs from them: identity/ownership of the related record.\n"
        "- Data this service provides to them: events when this service's record changes.\n\n"
        "### Postgres (kind: infra)\n"
        "- We initiate: yes.\n"
        "- Relationship: system of record for objects this service owns; reads and writes "
        "stay inside this bounded context.\n\n"
        "### Message broker (kind: infra)\n"
        "- We initiate: yes for events this service produces.\n"
        "- Relationship: notify collaborators of state changes without locking a topic "
        "catalog in this plan.\n"
    )
    return {
        "entity_relationships": spec,
        "api_design": spec,
        "assistant_message": (
            f"Mapped related entities for **{name}**: users, **{peer}**, datastore, and "
            "broker, including who initiates each link. Chat to refine, or approve the "
            "relationship map. Protocols and APIs stay with engineer sub-agents."
        ),
    }


def _stub_comms(blob: str) -> dict[str, Any]:
    return _stub_relations(blob)


def _stub_tech_stack(blob: str) -> dict[str, Any]:
    pending = blob
    if "Latest user message:" in blob:
        pending = blob.split("Latest user message:", 1)[-1]
    elif "User feedback:" in blob:
        pending = blob.split("User feedback:", 1)[-1]
    lower = pending.lower()
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
            # Apply SSL verification settings
            original_ssl_verify = os.environ.get("PYTHONHTTPSVERIFY")
            if not settings.ssl_verify:
                os.environ["PYTHONHTTPSVERIFY"] = "0"

            try:
                http_client = httpx.Client(verify=settings.ssl_verify)
                http_async_client = httpx.AsyncClient(verify=settings.ssl_verify)
                return ChatOpenAI(
                    model=model,
                    api_key=settings.openai_api_key,
                    temperature=temperature,
                    http_client=http_client,
                    http_async_client=http_async_client,
                )
            finally:
                # Restore original SSL verification setting
                if original_ssl_verify is not None:
                    os.environ["PYTHONHTTPSVERIFY"] = original_ssl_verify
                elif "PYTHONHTTPSVERIFY" in os.environ:
                    del os.environ["PYTHONHTTPSVERIFY"]
        if settings.aia_gateway_client_id and settings.aia_gateway_client_secret and settings.aia_gateway_base_url:
            # Apply SSL verification settings
            original_ssl_verify = os.environ.get("PYTHONHTTPSVERIFY")
            if not settings.ssl_verify:
                os.environ["PYTHONHTTPSVERIFY"] = "0"

            try:
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
            finally:
                # Restore original SSL verification setting
                if original_ssl_verify is not None:
                    os.environ["PYTHONHTTPSVERIFY"] = original_ssl_verify
                elif "PYTHONHTTPSVERIFY" in os.environ:
                    del os.environ["PYTHONHTTPSVERIFY"]
        if settings.reallm_base_url and settings.reallm_api_key:
            # Apply SSL verification settings
            original_ssl_verify = os.environ.get("PYTHONHTTPSVERIFY")
            if not settings.ssl_verify:
                os.environ["PYTHONHTTPSVERIFY"] = "0"

            try:
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
            finally:
                # Restore original SSL verification setting
                if original_ssl_verify is not None:
                    os.environ["PYTHONHTTPSVERIFY"] = original_ssl_verify
                elif "PYTHONHTTPSVERIFY" in os.environ:
                    del os.environ["PYTHONHTTPSVERIFY"]
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
