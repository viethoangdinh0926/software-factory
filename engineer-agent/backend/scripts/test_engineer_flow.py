#!/usr/bin/env python3
"""Smoke: ingest specs → plan → execute/pause/revise → ship; new spec interrupts work."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ["LLM_PROVIDER"] = "stub"
os.environ["BACKGROUND_EXECUTE"] = "false"
os.environ["GIT_EXECUTE_ENABLED"] = "false"

tmp = Path(tempfile.mkdtemp(prefix="engineer-smoke-"))
os.environ["DATA_DIR"] = str(tmp / "sessions")
os.environ["WORKSPACES_DIR"] = str(tmp / "workspaces")

print("importing…", flush=True)
from engineer_agent.config import get_settings
from engineer_agent.llm import get_chat_model
from engineer_agent.plan_parse import parse_handoff, parse_related_entities, sub_agent_id
from engineer_agent.query_intent import NEXT_PROMPT_HEADER
from engineer_agent.sessions import SessionStore, reset_store
from engineer_agent.workspace import private_dir_for, private_dir_name

get_settings.cache_clear()
get_chat_model.cache_clear()
reset_store()

SESSION = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
IDENTITY = "11111111-1111-1111-1111-111111111111"
CATALOG = "22222222-2222-2222-2222-222222222222"


def _plan(*, mid: str, name: str, relations: str, version: int = 1, features: str | None = None) -> str:
    feat = features or "- v1: own the primary resource."
    return (
        f"# Plan spec\n\n"
        f"- action: `plan`\n"
        f"- design_session_id: `{SESSION}`\n"
        f"- design_version: `{version}`\n"
        f"- microservice_id: `{mid}`\n"
        f"- microservice_name: `{name}`\n\n"
        f"## Entity relationships\n\n{relations}\n\n"
        f"## Features / functionality\n\n{feat}\n\n"
        f"## Tech stack\n\n- Language: Python 3.12\n"
    )


identity_relations = (
    "### User (kind: user)\n"
    "- We initiate: no. Callers initiate toward this service.\n"
    "- Relationship: register, login, refresh.\n\n"
    f"### VideoCatalogService (kind: core_microservice, id: {CATALOG})\n"
    "- We initiate: yes.\n"
    "- Relationship: Identity fetches catalog ownership records when authorizing publish.\n"
    "- Data this service needs from them: video id and owner.\n\n"
    "### Postgres (kind: infra)\n"
    "- We initiate: yes.\n"
    "- Relationship: system of record for credentials.\n"
)

catalog_relations = (
    "### User (kind: user)\n"
    "- We initiate: no.\n"
    "- Relationship: callers register and read videos.\n\n"
    f"### IdentityService (kind: core_microservice, id: {IDENTITY})\n"
    "- We initiate: no. Identity initiates toward this service.\n"
    "- Relationship: Identity reads ownership of catalog records.\n\n"
    "### Postgres (kind: infra)\n"
    "- We initiate: yes.\n"
    "- Relationship: system of record for videos.\n"
)

print("parse relations…", flush=True)
assert private_dir_name("IdentityService") == "IdentityService"
assert private_dir_name("Video Catalog") == "Video-Catalog"
assert private_dir_name("IdentityService", microservice_id="aaaa", taken={"IdentityService"}) == "IdentityService-aaaa"
ents = parse_related_entities(identity_relations)
assert any(e.name == "VideoCatalogService" and e.we_initiate for e in ents), ents
assert sub_agent_id(SESSION, IDENTITY) == f"{SESSION}:{IDENTITY}"
parsed = parse_handoff(_plan(mid=IDENTITY, name="IdentityService", relations=identity_relations))
assert parsed.design_session_id == SESSION
assert parsed.microservice_id == IDENTITY

print("ingest identity…", flush=True)
store = SessionStore()
s = store.ingest(_plan(mid=IDENTITY, name="IdentityService", relations=identity_relations))
ident = s.find(IDENTITY)
assert ident is not None
assert ident["sub_agent_id"] == f"{SESSION}:{IDENTITY}"
assert ident["status"] == "awaiting_plan"
assert "Offered API" in (ident.get("offered_api") or "")
plan = ident.get("execution_plan") or {}
items = plan.get("items") or []
assert items, plan
assert any(it.get("peer_services") for it in items), items
assert NEXT_PROMPT_HEADER not in (ident.get("messages") or [{}])[-1].get("content", "")
consults = ident.get("peer_consults") or []
assert any(c.get("peer_name") == "VideoCatalogService" and c.get("we_initiate") for c in consults)
assert any(c.get("status") == "pending_peer" for c in consults)

print("ingest catalog…", flush=True)
s = store.ingest(_plan(mid=CATALOG, name="VideoCatalogService", relations=catalog_relations))
ident = s.find(IDENTITY)
cat = s.find(CATALOG)
assert cat is not None and ident is not None
assert cat["status"] == "awaiting_plan"
assert "Offered API" in (cat.get("offered_api") or "")
hit = next(c for c in ident.get("peer_consults") or [] if c.get("we_initiate"))
assert hit["status"] == "consulted", hit
assert "Offered API" in (hit.get("offered_api") or "")
assert hit["peer_sub_agent_id"] == f"{SESSION}:{CATALOG}"

print("identity asks catalog for more data…", flush=True)
s = store.chat(
    SESSION,
    "I need more data: include thumbnail_url from VideoCatalogService.",
    service_id=IDENTITY,
)
cat = s.find(CATALOG)
ident = s.find(IDENTITY)
assert cat is not None and ident is not None
assert "thumbnail_url" in (cat.get("offered_api") or "")
assert any(
    r.get("from_sub_agent_id") == ident["sub_agent_id"]
    for r in (cat.get("incoming_api_requests") or [])
)
hit = next(c for c in ident.get("peer_consults") or [] if c.get("we_initiate"))
assert "thumbnail_url" in (hit.get("offered_api") or "")
assert NEXT_PROMPT_HEADER not in (ident.get("messages") or [{}])[-1].get("content", "")

print("approve identity plan…", flush=True)
s = store.chat(SESSION, "Approve", service_id=IDENTITY)
ident = s.find(IDENTITY)
assert ident is not None
assert ident["status"] == "executing"
private = private_dir_for(SESSION, "IdentityService", microservice_id=IDENTITY)
assert private.is_dir(), private
assert private.name == "IdentityService"
assert (private / "README.md").is_file()

print("tick first item…", flush=True)
s = store.tick_execution(SESSION, service_id=IDENTITY)
ident = s.find(IDENTITY)
assert ident is not None
assert ident["status"] == "executing"
done = [it for it in (ident.get("execution_plan") or {}).get("items") or [] if it.get("status") == "done"]
assert len(done) == 1, ident.get("execution_plan")

print("plan locked during execute…", flush=True)
try:
    store.chat(SESSION, "change the plan: drop the scaffold item", service_id=IDENTITY)
    raise AssertionError("expected PermissionError while executing")
except PermissionError:
    pass

print("pause, revise, execute…", flush=True)
s = store.pause(SESSION, service_id=IDENTITY)
ident = s.find(IDENTITY)
assert ident is not None
assert ident["status"] == "paused"
s = store.chat(SESSION, "add a bug for login lockout at highest priority", service_id=IDENTITY)
ident = s.find(IDENTITY)
assert ident is not None
titles = [it.get("title") for it in (ident.get("execution_plan") or {}).get("items") or []]
assert any("lockout" in str(t).lower() or "bug" in str(t).lower() for t in titles), titles
s = store.execute(SESSION, service_id=IDENTITY)
ident = s.find(IDENTITY)
assert ident is not None
assert ident["status"] == "executing"

print("new spec interrupts execution…", flush=True)
s = store.ingest(
    _plan(
        mid=IDENTITY,
        name="IdentityService",
        relations=identity_relations,
        version=2,
        features="- v2: own the primary resource.\n- v2: session revocation.",
    )
)
ident = s.find(IDENTITY)
assert ident is not None
assert ident["status"] == "awaiting_plan"
assert str(ident.get("previous_plan_spec") or "").strip()
assert (ident.get("previous_execution_plan") or {}).get("items")
assert ident.get("interrupted_from") == "executing"
assert "new spec" in ((ident.get("execution_plan") or {}).get("transition") or "").lower() or (
    ident.get("execution_plan") or {}
).get("transition")

print("approve v2 and tick until shipped…", flush=True)
s = store.approve(SESSION, service_id=IDENTITY)
ident = s.find(IDENTITY)
assert ident is not None
assert ident["status"] == "executing"
for _ in range(12):
    ident = s.find(IDENTITY)
    if ident and ident.get("status") == "shipped":
        break
    s = store.tick_execution(SESSION, service_id=IDENTITY)
ident = s.find(IDENTITY)
assert ident is not None
assert ident["status"] == "shipped", ident.get("status")
assert ident.get("git_ship_status") == "local_only"
assert any((private / "items").glob("*/impl.py"))
peer_item = next(
    it
    for it in (ident.get("execution_plan") or {}).get("items") or []
    if it.get("peer_services")
)
assert peer_item.get("status") == "done"
assert "VideoCatalogService" in (peer_item.get("contracts") or {}), peer_item

print("suspend identity…", flush=True)
s = store.ingest(
    f"# Suspend development\n\n"
    f"- action: `suspend`\n"
    f"- design_session_id: `{SESSION}`\n"
    f"- design_version: `3`\n"
    f"- microservice_id: `{IDENTITY}`\n"
    f"- reason: `removed from HLD`\n"
)
ident = s.find(IDENTITY)
assert ident is not None
assert ident["status"] == "suspended"
cat = s.find(CATALOG)
assert cat is not None
assert cat["status"] != "suspended"

print("git access stored for the fleet…", flush=True)
SAMPLE_KEY = (
    "-----BEGIN OPENSSH PRIVATE KEY-----\n"
    "b3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQAAAAAAAAABAAAAMwAAAAtzc2gtZW\n"
    "-----END OPENSSH PRIVATE KEY-----\n"
)
s = store.ingest_git(
    design_session_id=SESSION,
    git_repo_url="git@github.com:org/video-platform.git",
    ssh_private_key=SAMPLE_KEY,
)
pub = s.to_public()
assert pub["git_repo_url"] == "git@github.com:org/video-platform.git"
assert pub["git_key_configured"] is True
assert "ssh_private_key" not in pub
data = store.git_data_for(SESSION)
assert data is not None
assert data["repo_url"].startswith("git@")
assert "BEGIN OPENSSH PRIVATE KEY" in data["ssh_private_key"]
ident = s.find(IDENTITY)
assert ident is not None

print("OK", flush=True)
sys.exit(0)
