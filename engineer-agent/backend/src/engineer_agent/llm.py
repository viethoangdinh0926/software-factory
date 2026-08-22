from __future__ import annotations

import json
import logging
import os
import re
from functools import lru_cache
from typing import Any

import httpx
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from engineer_agent.config import get_settings
from engineer_agent.json_util import parse_llm_json_object

logger = logging.getLogger(__name__)


class StubChatModel(BaseChatModel):
    """Deterministic offline model so the engineer fleet can run without API keys."""

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
        if "answering a question about" in lower:
            payload = {"assistant_message": _stub_qa(blob)}
        elif "engineer execution planner" in lower:
            payload = _stub_execution_plan(blob)
        elif "engineer plan revision advisor" in lower:
            payload = _stub_plan_revise(blob)
        elif "engineer api offer advisor" in lower:
            payload = _stub_offered_api(blob)
        elif "engineer api change advisor" in lower:
            payload = _stub_api_change(blob)
        elif "engineer implementer" in lower:
            payload = _stub_implement(blob)
        else:
            payload = {
                "assistant_message": "Stub engineer is ready. Approve to continue.",
                "ready": True,
            }
        return ChatResult(
            generations=[ChatGeneration(message=AIMessage(content=json.dumps(payload)))]
        )


def _focus_name(blob: str) -> str:
    focus = re.search(r"Focus microservice:\s*([A-Za-z0-9]+)", blob)
    if focus:
        return focus.group(1)
    found = re.findall(r"\b([A-Z][A-Za-z0-9]+Service)\b", blob)
    return found[0] if found else "Service"


def _slug(name: str) -> str:
    slug = re.sub(r"Service$", "", name)
    slug = re.sub(r"(?<!^)(?=[A-Z])", "-", slug).lower()
    return slug or "resource"


def _stub_qa(blob: str) -> str:
    lower = blob.lower()
    ask = lower
    if "user question:" in lower:
        ask = lower.split("user question:", 1)[-1]
    if "offered api" in ask or "protocol" in ask or "endpoint" in ask:
        return (
            "This sub-engineer owns the offered API and protocol for its microservice. "
            "Peers that initiate toward us must call this surface; we do not inherit a "
            "protocol lock from the orchestrator."
        )
    if "consult" in ask or "peer" in ask or "initiate" in ask:
        return (
            "When this service initiates toward another core microservice, I consult that "
            "peer sub-engineer's offered API and write the client against it."
        )
    return "Answered from the current sub-engineer artifacts. Ask about a specific detail."


def _stub_offered_api(blob: str) -> dict[str, Any]:
    name = _focus_name(blob)
    slug = _slug(name)
    spec = (
        f"## Offered API for {name}\n\n"
        "Protocol: REST/JSON request-response at the service edge (chosen by this "
        "sub-engineer, not the orchestrator).\n\n"
        f"### `POST /v1/{slug}`\n"
        "Create the primary resource this service owns. Returns the created record.\n\n"
        f"### `GET /v1/{slug}/{{id}}`\n"
        "Read the resource by id. Returns 404 when missing. Payload includes id, owner, "
        "status, and updated_at.\n\n"
        f"### `PATCH /v1/{slug}/{{id}}`\n"
        "Partial update of mutable fields. Enforces ownership; version on write.\n"
    )
    return {
        "offered_api": spec,
        "assistant_message": (
            f"Drafted the offered API for **{name}**. Peer sub-engineers that initiate "
            "toward this service should call this surface. Chat to change protocol or "
            "fields, or approve the offered API."
        ),
    }


def _stub_api_change(blob: str) -> dict[str, Any]:
    name = _focus_name(blob)
    existing = ""
    if "Current offered API:" in blob:
        existing = blob.split("Current offered API:", 1)[-1]
        existing = existing.split("Change request:", 1)[0].strip()
    request = blob.split("Change request:", 1)[-1].strip() if "Change request:" in blob else blob
    extra = "extra_field"
    snake = re.findall(r"\b([a-z]+_[a-z0-9_]+)\b", request.lower())
    if snake:
        extra = snake[0]
    else:
        named = re.search(
            r"(?:include|field|add)\s+`?([a-z][a-z0-9_]{2,})`?",
            request.lower(),
        )
        if named and named.group(1) not in {"more", "less", "data", "field", "from"}:
            extra = named.group(1)
    less = "less" in request.lower() or "remove" in request.lower() or "drop" in request.lower()
    if less:
        body = existing or f"## Offered API for {name}\n"
        lines = [ln for ln in body.splitlines() if extra not in ln.lower()]
        spec = "\n".join(lines).strip() + "\n"
        note = f"Removed **{extra}** from the offered API after a peer asked for less data."
    else:
        addon = (
            f"\n### Additional field `{extra}`\n"
            f"Included on read payloads because a peer sub-engineer needs this data.\n"
        )
        spec = (existing or f"## Offered API for {name}\n") + addon
        note = f"Added **{extra}** to the offered API after a peer asked for more data."
    return {"offered_api": spec, "assistant_message": note}


def _initiator_peers(blob: str) -> list[str]:
    names: list[str] = []
    for match in re.finditer(
        r"###\s+([A-Za-z0-9]+Service)\b[^\n]*\n(?:(?!###).)*?We initiate:\s*yes",
        blob,
        re.I | re.S,
    ):
        name = match.group(1)
        if name not in names:
            names.append(name)
    return names


def _stub_execution_plan(blob: str) -> dict[str, Any]:
    name = _focus_name(blob)
    peers = _initiator_peers(blob)
    prior = "previous execution plan" in blob.lower() or "previous plan spec" in blob.lower()
    items: list[dict[str, Any]] = [
        {
            "id": "item-1",
            "kind": "feature",
            "title": f"Scaffold {name} and tests",
            "priority": 1,
            "depends_on": [],
            "peer_services": [],
            "notes": "Create the service skeleton and unit-test harness in the private workspace.",
        },
        {
            "id": "item-2",
            "kind": "feature",
            "title": f"Implement primary {name} resource",
            "priority": 2,
            "depends_on": ["item-1"],
            "peer_services": [],
            "notes": "Own the offered API handlers for the primary resource.",
        },
    ]
    if peers:
        peer = peers[0]
        items.append(
            {
                "id": "item-3",
                "kind": "feature",
                "title": f"Integrate with {peer}",
                "priority": 3,
                "depends_on": ["item-2"],
                "peer_services": [peer],
                "notes": (
                    f"Settle the communication contract with {peer} (input/output) "
                    "before writing the client."
                ),
            }
        )
    if prior:
        transition = (
            "Carry forward completed items whose titles still exist; restart changed "
            "or new work. Re-consult peers for items that depend on other services."
        )
        summary = (
            f"Updated development plan for {name} after a new spec. Compare with the "
            "previous plan; keep finished work that still applies."
        )
    else:
        transition = (
            "No prior plan. Pull the repo, create the private folder at the git root, "
            "then execute items in priority order. Ship only when every item is done."
        )
        summary = (
            f"Development plan for {name}: highest-priority features first, with "
            "peer-dependent work after those dependencies are in place."
        )
    return {
        "summary": summary,
        "transition": transition,
        "items": items,
        "assistant_message": (
            f"Drafted an execution plan for **{name}**. Review the priority order and "
            "dependencies, chat to change the plan, or approve it to start coding."
        ),
    }


def _current_plan_blob(blob: str) -> dict[str, Any]:
    marker = "Current execution plan:"
    if marker not in blob:
        return {}
    chunk = blob.split(marker, 1)[-1]
    chunk = chunk.split("Latest user message:", 1)[0].strip()
    try:
        data = json.loads(chunk)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def _stub_plan_revise(blob: str) -> dict[str, Any]:
    name = _focus_name(blob)
    current = _current_plan_blob(blob)
    items = list(current.get("items") or [])
    request = blob.split("Latest user message:", 1)[-1] if "Latest user message:" in blob else blob
    lower = request.lower()
    if not items:
        return _stub_execution_plan(blob)
    if "bug" in lower:
        for item in items:
            try:
                item["priority"] = int(item.get("priority") or 1) + 1
            except (TypeError, ValueError):
                item["priority"] = 2
        items.insert(
            0,
            {
                "id": "item-bug",
                "kind": "bug",
                "title": "Fix login lockout",
                "priority": 1,
                "depends_on": [],
                "peer_services": [],
                "status": "pending",
                "notes": "Inserted from a plan-revision request.",
            },
        )
        note = "Inserted a highest-priority bug and shifted later items."
    else:
        summary_extra = re.sub(r"\s+", " ", request.strip())[:180]
        current["summary"] = (
            f"{current.get('summary') or f'Execution plan for {name}'} "
            f"Revised: {summary_extra}"
        ).strip()
        note = "Updated the execution plan from your comments."
    current["items"] = items
    current["assistant_message"] = note
    current.setdefault(
        "transition",
        "Resume from current workspace progress: keep completed items that still match, "
        "then start the next pending item.",
    )
    current.setdefault("summary", f"Revised execution plan for {name}.")
    return current


def _stub_implement(blob: str) -> dict[str, Any]:
    name = _focus_name(blob)
    notes = (
        f"## Implementation notes for {name}\n\n"
        "- Own the offered API in this service's handlers; do not copy peer internals.\n"
        "- For every relationship with **we initiate: yes** toward another core "
        "microservice, call that peer's offered API as consulted.\n"
        "- If a needed field is missing, ask the peer sub-engineer to extend their API "
        "instead of reaching into their datastore.\n"
    )
    return {
        "implementation_notes": notes,
        "assistant_message": (
            f"Ready to implement **{name}** against its offered API and consulted peer "
            "surfaces. Approve to mark this sub-engineer ready."
        ),
    }


def invoke_json(system: str, user: str) -> dict[str, Any]:
    model = get_chat_model()
    response = model.invoke([SystemMessage(content=system), HumanMessage(content=user)])
    content = response.content
    if isinstance(content, list):
        content = "".join(
            block.get("text", "") if isinstance(block, dict) else str(block) for block in content
        )
    return parse_llm_json_object(str(content or ""))


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
                if original_ssl_verify is not None:
                    os.environ["PYTHONHTTPSVERIFY"] = original_ssl_verify
                elif "PYTHONHTTPSVERIFY" in os.environ:
                    del os.environ["PYTHONHTTPSVERIFY"]
        if settings.aia_gateway_client_id and settings.aia_gateway_client_secret and settings.aia_gateway_base_url:
            original_ssl_verify = os.environ.get("PYTHONHTTPSVERIFY")
            if not settings.ssl_verify:
                os.environ["PYTHONHTTPSVERIFY"] = "0"
            try:
                from engineer_agent.utils.auth import build_http_clients

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
                if original_ssl_verify is not None:
                    os.environ["PYTHONHTTPSVERIFY"] = original_ssl_verify
                elif "PYTHONHTTPSVERIFY" in os.environ:
                    del os.environ["PYTHONHTTPSVERIFY"]
        if settings.reallm_base_url and settings.reallm_api_key:
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
                if original_ssl_verify is not None:
                    os.environ["PYTHONHTTPSVERIFY"] = original_ssl_verify
                elif "PYTHONHTTPSVERIFY" in os.environ:
                    del os.environ["PYTHONHTTPSVERIFY"]
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
