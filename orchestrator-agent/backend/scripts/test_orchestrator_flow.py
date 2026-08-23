#!/usr/bin/env python3
"""Smoke: ingest HLD package → plan one service → v2 rename/drop → LLD track change."""
from __future__ import annotations

import inspect
import os
import sys
import tempfile
from pathlib import Path

os.environ["LLM_PROVIDER"] = "stub"
os.environ["ENGINEER_AGENT_URL"] = ""
os.environ["GIT_VERIFY_ENABLED"] = "false"

tmp = Path(tempfile.mkdtemp(prefix="orchestrator-smoke-"))
os.environ["DATA_DIR"] = str(tmp / "sessions")

print("importing…", flush=True)
from orchestrator_agent.config import get_settings
from orchestrator_agent.graph import reset_graph
from orchestrator_agent.graph.nodes.common import pick_assistant_message, service_focus_user_block
from orchestrator_agent.llm import get_chat_model
from orchestrator_agent.package_parse import extract_http_endpoints, format_agreed_endpoints, service_contract_section
from orchestrator_agent import query_intent as orch_intent
from orchestrator_agent.query_intent import (
    NEXT_PROMPT_HEADER,
    USER_MESSAGE_FIRST_RULES,
    format_classify_context,
    resolve_wait_action,
    workflow_action,
    is_advance_request,
    is_full_phase_request,
    is_revision_request,
    is_step_approval_message,
    user_message_first_block,
    wants_endpoint_list,
)
from orchestrator_agent.sessions import SessionStore, reset_store

get_settings.cache_clear()
get_chat_model.cache_clear()
reset_graph()
reset_store()

assert "Latest user message is the work" in USER_MESSAGE_FIRST_RULES
assert user_message_first_block("please drop Kafka") 
assert not user_message_first_block("")

print("reject package without design session id…", flush=True)
try:
    SessionStore().ingest("# System Design Package\n\nNo session id here.\n")
    raise AssertionError("expected missing design session ID")
except ValueError as exc:
    assert "design session ID" in str(exc).lower() or "session" in str(exc).lower()

SESSION = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


def _package(*, version: int, track: str, body: str) -> str:
    return (
        f"<!-- architect-agent handoff id=h1 session={SESSION} version={version} at=2026-08-16T00:00:00+00:00 -->\n\n"
        f"# System Design Package\n\n"
        f"Design session: `{SESSION}`\n"
        f"Design version: `{version}`\n"
        f"Track: `{track}` step `6`\n\n"
        f"{body}\n"
    )


hld_v1 = _package(
    version=1,
    track="hld",
    body=(
        "## Business Specification\n\n"
        "Distributed video platform with microservices.\n\n"
        "## Core Microservices\n\n"
        "### IdentityService\n"
        "Owns User, Session, Credential. Operations: register, login, refresh.\n\n"
        "### VideoCatalogService\n"
        "Owns Video, VisibilityPolicy. Operations: register, read metadata, update visibility.\n\n"
        "## Communication Schemes\n\n"
        "### User ↔ system\n"
        "HTTPS request/response via API Gateway (REST/JSON at the edge).\n\n"
        "### Core microservice ↔ core microservice\n"
        "Sync gRPC for authz; Kafka pub/sub for VideoPublished.\n\n"
        "### Core microservice ↔ infrastructure\n"
        "Postgres request/response SQL; CDN stream for playback.\n\n"
        "## Design Diagram\n\n"
        "```mermaid\nflowchart LR\n  IdentityService --> VideoCatalogService\n```\n"
    ),
)

print("service-scoped package excerpt…", flush=True)
ident_only = service_contract_section(hld_v1, ["IdentityService"])
assert "Owns User" in ident_only
assert "VideoCatalogService" not in ident_only
assert "Business Specification" not in ident_only
focus = service_focus_user_block(
    {"package_markdown": hld_v1, "services": []},
    {
        "names": ["IdentityService"],
        "role_key": "identity",
        "architect_api_contract": "Owns User, Session, Credential.",
        "microservice_id": "id-1",
        "messages": [],
    },
)
assert "Focus microservice: IdentityService" in focus
assert "Package excerpt" not in focus
assert "Architect communication schemes (context only — do not lock protocols):" in focus
assert pick_assistant_message({"assistant_message": ""}, fallback="Hi") == "Hi"
assert "POST /login" in pick_assistant_message(
    {"assistant_message": "  ", "api_design": "POST /login"},
    fallback="missing",
    artifact_keys=("api_design",),
)
assert "Last assistant message:" in format_classify_context("wf", "pick REST")
assert "BOTH messages" in inspect.getsource(orch_intent._llm_turn_intent)
assert "Latest user message:" in inspect.getsource(orch_intent._llm_turn_intent)
assert workflow_action("revise") == "revise"
assert resolve_wait_action("answer", "What features are in v1?") == "answer"
assert wants_endpoint_list("Show me all URL enpoints we aggreed on")
assert not is_revision_request("Show me all URL endpoints we agreed on")
assert is_revision_request("Add a health check endpoint")
assert is_revision_request("Why is there no rate limiting?")
assert is_full_phase_request("Switch the stack to Java.")
assert is_full_phase_request("Redo the entity relationships from scratch")
assert not is_full_phase_request("Add a health check endpoint")
assert not is_full_phase_request("add a bug for login lockout")
assert not is_revision_request("Approve")
assert is_step_approval_message("Approve")
assert is_step_approval_message("Looks good.")
assert is_step_approval_message("next step")
assert is_step_approval_message("Let's move on")
assert is_advance_request("wrap up this step")
assert not is_advance_request("What's the next step?")
assert not is_step_approval_message("Why should I approve REST?")
assert not is_step_approval_message("Looks good, add a health check")
assert not is_step_approval_message("next step, add a health check")
eps = extract_http_endpoints(
    "## Endpoint: POST /v1/auth/register\nGET /v1/users/{id}\n`PATCH /v1/users/me`"
)
assert ("POST", "/v1/auth/register") in eps
assert ("GET", "/v1/users/{id}") in eps
assert "POST /v1/auth/register" in format_agreed_endpoints("IdentityService", eps)

print("HLD v1 ingest…", flush=True)
store = SessionStore()
s = store.ingest(hld_v1)
pub = s.to_public()
print("  ", s.topology, s.phase, s.wait_kind, len(s.services), pub["can_approve"], flush=True)
assert s.design_session_id == SESSION
assert s.topology == "distributed", s.topology
assert s.architect_track == "hld"
live = [x for x in pub["services"] if x.get("status") != "suspended"]
assert len(live) == 2, live
assert all(x.get("status") == "awaiting_relations" for x in live), [x.get("status") for x in live]
assert all(x.get("can_approve") for x in live)
assert all((x.get("entity_relationships") or x.get("api_design") or "").strip() for x in live)
assert all("We initiate" in (x.get("entity_relationships") or x.get("api_design") or "") for x in live)
assert all(NEXT_PROMPT_HEADER not in (x.get("messages") or [{}])[-1].get("content", "") for x in live)
assert not pub["can_approve"]
assert pub["wait_kind"] == "distributed", pub["wait_kind"]
first_ids = {
    (x.get("names") or [""])[-1]: x["microservice_id"]
    for x in live
}
assert "IdentityService" in first_ids and "VideoCatalogService" in first_ids
catalog_id = first_ids["VideoCatalogService"]
identity_id = first_ids["IdentityService"]

print("  identity relations qa…", flush=True)
s = store.chat(SESSION, "Who initiates toward VideoCatalogService?", service_id=identity_id)
ident = next(x for x in s.to_public()["services"] if x["microservice_id"] == identity_id)
cat = next(x for x in s.to_public()["services"] if x["microservice_id"] == catalog_id)
assert ident["status"] == "awaiting_relations", ident["status"]
assert ident["can_approve"]
assert ident["messages"][-1]["content"].strip()
assert "Updates to this proposal" not in ident["messages"][-1]["content"]
assert NEXT_PROMPT_HEADER not in ident["messages"][-1]["content"]
ident_digest = (ident.get("discussion_digest") or "").lower()
assert "videocatalog" in ident_digest or "initiate" in ident_digest, ident.get("discussion_digest")
assert "who initiates toward videocatalogservice" not in (cat.get("discussion_digest") or "").lower()

print("  identity off-topic chat asks to clarify…", flush=True)
s = store.chat(SESSION, "asdf what is the weather in paris", service_id=identity_id)
ident = next(x for x in s.to_public()["services"] if x["microservice_id"] == identity_id)
cat = next(x for x in s.to_public()["services"] if x["microservice_id"] == catalog_id)
off = ident["messages"][-1]["content"]
assert ident["status"] == "awaiting_relations", ident["status"]
assert "previous message" in off.lower() or "open point" in off.lower() or "clarif" in off.lower(), off[:400]
assert "weather" not in (ident.get("discussion_digest") or "").lower()
assert "weather" not in (cat.get("discussion_digest") or "").lower()

s = store.chat(SESSION, "next step", service_id=identity_id)
ident = next(x for x in s.to_public()["services"] if x["microservice_id"] == identity_id)
cat = next(x for x in s.to_public()["services"] if x["microservice_id"] == catalog_id)
print("  after identity relations", ident["status"], cat["status"], flush=True)
assert ident["approve_kind"] == "approve_features", ident["approve_kind"]
assert cat["status"] == "awaiting_relations", "other service must stay on its own tile"

print("  identity features qa…", flush=True)
s = store.chat(SESSION, "What features are in v1?", service_id=identity_id)
ident = next(x for x in s.to_public()["services"] if x["microservice_id"] == identity_id)
assert ident["status"] == "awaiting_features", ident["status"]
assert ident["can_approve"]
assert ident["messages"][-1]["content"].strip()
assert "Updates to this proposal" not in ident["messages"][-1]["content"]
assert NEXT_PROMPT_HEADER not in ident["messages"][-1]["content"]

s = store.chat(SESSION, "Approve", service_id=identity_id)
ident = next(x for x in s.to_public()["services"] if x["microservice_id"] == identity_id)
cat = next(x for x in s.to_public()["services"] if x["microservice_id"] == catalog_id)
print("  after identity features", ident["status"], cat["status"], flush=True)
assert ident["approve_kind"] == "approve_plan"
assert cat["status"] == "awaiting_relations", "other service must stay on its own tile"
s = store.approve(SESSION, service_id=identity_id)
print("  after identity plan", [x.get("status") for x in s.services], flush=True)
ident = next(x for x in s.services if x["microservice_id"] == identity_id)
cat = next(x for x in s.services if x["microservice_id"] == catalog_id)
assert ident.get("status") == "sent"
assert ident.get("spec_version") == 1
assert ident.get("update_kind") == "full"
assert cat.get("status") == "awaiting_relations"
assert "## Entity relationships" in (ident.get("plan_spec") or "")
assert "Locked protocol" not in (ident.get("plan_spec") or "")
assert "## Features / functionality" in (ident.get("plan_spec") or "")
assert "## Bugs" in (ident.get("plan_spec") or "")
assert "spec_version: `1`" in (ident.get("plan_spec") or "")
assert (ident.get("feature_spec") or "").strip()
assert s.engineer_handoffs[-1]["action"] == "plan"
assert s.engineer_handoffs[-1]["microservice_id"] == identity_id

print("  incremental spec after first ship…", flush=True)
s = store.chat(SESSION, "Add a health check endpoint.", service_id=identity_id)
ident = next(x for x in s.to_public()["services"] if x["microservice_id"] == identity_id)
assert ident["status"] == "awaiting_spec_update", ident["status"]
assert ident["can_approve"]
assert "health check" in (ident.get("feature_spec") or "").lower()
ident_digest = (ident.get("discussion_digest") or "").lower()
assert "health check" in ident_digest, ident.get("discussion_digest")
cat = next(x for x in s.to_public()["services"] if x["microservice_id"] == catalog_id)
assert "health check" not in (cat.get("discussion_digest") or "").lower()
s = store.approve(SESSION, service_id=identity_id)
ident = next(x for x in s.services if x["microservice_id"] == identity_id)
assert ident.get("status") == "sent"
assert ident.get("spec_version") == 2
assert ident.get("update_kind") == "incremental"
assert "health check" in (ident.get("plan_spec") or "").lower()
assert "spec_version: `2`" in (ident.get("plan_spec") or "")
assert s.engineer_handoffs[-1]["action"] == "plan"

print("  incremental bug update…", flush=True)
s = store.chat(SESSION, "add a bug for login lockout", service_id=identity_id)
ident = next(x for x in s.to_public()["services"] if x["microservice_id"] == identity_id)
assert ident["status"] == "awaiting_spec_update", ident["status"]
assert "lockout" in (ident.get("bug_spec") or "").lower()
s = store.approve(SESSION, service_id=identity_id)
ident = next(x for x in s.services if x["microservice_id"] == identity_id)
assert ident.get("status") == "sent"
assert ident.get("spec_version") == 3
assert "lockout" in (ident.get("plan_spec") or "").lower()

print("  refuse full-phase without new package…", flush=True)
stack_before = ident.get("tech_stack") or ""
s = store.chat(SESSION, "Switch the stack to Java.", service_id=identity_id)
ident = next(x for x in s.to_public()["services"] if x["microservice_id"] == identity_id)
assert ident["status"] == "sent", ident["status"]
assert ident["status"] != "awaiting_relations"
assert ident["status"] != "awaiting_stack"
assert (ident.get("tech_stack") or "") == stack_before
assert "design package" in ident["messages"][-1]["content"].lower()
assert ident.get("spec_version") == 3

hld_v2 = _package(
    version=2,
    track="hld",
    body=(
        "## Business Specification\n\n"
        "Distributed video platform with microservices.\n\n"
        "## Core Microservices\n\n"
        "### CatalogService\nOwns Video. Operations: read catalog metadata.\n\n"
        "### PlaybackService\nOwns PlaybackSession. Operations: authorize playback.\n\n"
        "## Communication Schemes\n\n"
        "HTTPS request/response via API Gateway (REST/JSON). Kafka pub/sub for playback events.\n\n"
        "## Design Diagram\n\n"
        "```mermaid\nflowchart LR\n  CatalogService --> PlaybackService\n```\n"
    ),
)

print("HLD v2 update (rename catalog, drop identity, add playback)…", flush=True)
s = store.ingest(hld_v2)
live = [x for x in s.services if x.get("status") != "suspended"]
suspended = [x for x in s.services if x.get("status") == "suspended"]
print("  live", [(x.get("names"), x.get("status")) for x in live], flush=True)
print("  suspended", [x.get("microservice_id") for x in suspended], flush=True)
assert any(identity_id == x.get("microservice_id") for x in suspended), "identity should be suspended"
assert any(catalog_id == x.get("microservice_id") for x in live), "renamed catalog must keep UUID"
names = {(x.get("names") or [""])[-1] for x in live}
assert "CatalogService" in names
assert "PlaybackService" in names
assert all(x.get("status") == "awaiting_relations" for x in live), [x.get("status") for x in live]
assert any(h["action"] == "suspend" and h["microservice_id"] == identity_id for h in s.engineer_handoffs)

lld = _package(
    version=3,
    track="lld",
    body=(
        "## Business Specification\n\n"
        "Single process library for quote calculation. No network services.\n\n"
        "## Design Diagram\n\n"
        "```mermaid\nclassDiagram\n  class PricingLib\n```\n"
    ),
)

print("LLD v3 track change…", flush=True)
s = store.ingest(lld)
print("  ", s.topology, s.architect_track, s.wait_kind, s.app_status, flush=True)
assert s.architect_track == "lld"
assert s.topology == "standalone", s.topology
assert s.to_public()["approve_kind"] == "approve_features", s.to_public()["approve_kind"]
assert (s.feature_spec or "").strip()
assert any(h["action"] == "suspend" for h in s.engineer_handoffs)
s = store.approve(SESSION)
print("  after standalone features", s.wait_kind, s.app_status, flush=True)
assert s.to_public()["approve_kind"] == "approve_plan", s.to_public()["approve_kind"]
s = store.approve(SESSION)
print("  after standalone plan", s.app_status, s.plan_spec[:40], flush=True)
assert s.app_status == "sent"
assert s.plan_spec.strip()
assert "## Features / functionality" in s.plan_spec
assert s.engineer_handoffs[-1]["action"] == "plan"
assert s.engineer_handoffs[-1]["microservice_id"] in (None, "")
assert s.to_public()["discussion_locked"]
try:
    store.chat(SESSION, "Switch the stack to Java.")
    raise AssertionError("standalone chat should lock after engineer handoff")
except PermissionError:
    pass
assert store.get(SESSION).tech_stack == s.tech_stack

print("git access save + failed send + resend…", flush=True)
from orchestrator_agent.git_access import GitAccessError, validate_repo_url
from orchestrator_agent import sessions as sessions_mod

SAMPLE_KEY = (
    "-----BEGIN OPENSSH PRIVATE KEY-----\n"
    "b3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQAAAAAAAAABAAAAMwAAAAtzc2gtZW\n"
    "-----END OPENSSH PRIVATE KEY-----\n"
)
try:
    validate_repo_url("https://github.com/org/repo.git")
    raise AssertionError("https git url should be rejected")
except GitAccessError:
    pass
s = store.save_git(
    SESSION,
    git_repo_url="git@github.com:org/video-platform.git",
    ssh_private_key=SAMPLE_KEY,
)
pub = s.to_public()
assert pub["git_repo_url"] == "git@github.com:org/video-platform.git"
assert pub["git_key_configured"] is True
assert pub["can_send_git"] is True
assert "ssh_private_key" not in pub
assert "git_ssh_private_key" not in pub
s = store.send_git(SESSION)
assert s.git_send_status == "failed", s.git_send_status
assert s.git_send_error
assert s.to_public()["can_send_git"] is True

def _fake_send(**kwargs):
    from orchestrator_agent.a2a.engineer import EngineerHandoff

    return EngineerHandoff(
        status="sent",
        handoff_id="git-1",
        path="",
        target_url="http://127.0.0.1:8091",
        detail="Engineer stored git access for this design session.",
        at="2026-08-22T00:00:00+00:00",
        action="git",
        design_session_id=kwargs["design_session_id"],
        design_version=0,
        microservice_id=None,
    )

sessions_mod.send_git_access = _fake_send  # type: ignore[method-assign]
s = store.send_git(SESSION)
assert s.git_send_status == "sent", s.git_send_error
assert s.git_send_error == ""
assert any(h["action"] == "git" and h["status"] == "sent" for h in s.engineer_handoffs)

print("OK", flush=True)
sys.exit(0)
