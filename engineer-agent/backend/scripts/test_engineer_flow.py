#!/usr/bin/env python3
"""Smoke: ingest specs → plan → execute/pause/revise → ship; new spec interrupts work."""
from __future__ import annotations

import inspect
import json
import os
import sys
import tempfile
from pathlib import Path

os.environ["LLM_PROVIDER"] = "stub"
os.environ["BACKGROUND_EXECUTE"] = "false"
os.environ["GIT_EXECUTE_ENABLED"] = "false"
os.environ["PI_CODER_ENABLED"] = "false"

tmp = Path(tempfile.mkdtemp(prefix="engineer-smoke-"))
os.environ["DATA_DIR"] = str(tmp / "sessions")
os.environ["WORKSPACES_DIR"] = str(tmp / "workspaces")

print("importing…", flush=True)
from engineer_agent.config import get_settings
from engineer_agent.llm import get_chat_model
from engineer_agent.plan_parse import parse_handoff, parse_related_entities, sub_agent_id
from engineer_agent import query_intent as engineer_intent
from engineer_agent.query_intent import (
    NEXT_PROMPT_HEADER,
    USER_MESSAGE_FIRST_RULES,
    classify_pi_item_command,
    classify_user_message,
    format_classify_context,
    user_message_first_block,
    workflow_action,
)
import engineer_agent.sessions as sessions_mod
from engineer_agent.sessions import SessionStore, reset_store
from engineer_agent.workspace import (
    answers_path_for,
    private_dir_for,
    private_dir_name,
    run_workspace_tests,
    write_item_work,
)

get_settings.cache_clear()
get_chat_model.cache_clear()
reset_store()

assert "Latest user message is the work" in USER_MESSAGE_FIRST_RULES
assert user_message_first_block("add thumbnail_url")
assert not user_message_first_block("")
assert "Last assistant message:" in format_classify_context("wf", "pause if you need to change it")
assert "BOTH messages" in inspect.getsource(engineer_intent._llm_turn_intent)
assert "Latest user message:" in inspect.getsource(engineer_intent._llm_turn_intent)
assert workflow_action("pause") == "pause"
assert workflow_action("execute") == "execute"
assert workflow_action("revise") == "revise"
assert workflow_action("stop_item") == "stop_item"
assert workflow_action("resume_item") == "resume_item"
assert workflow_action("undo_item") == "undo_item"
assert classify_pi_item_command("stop working on current feature") == "stop_item"
assert classify_pi_item_command("stop fixing this bug") == "stop_item"
assert classify_pi_item_command("stop the plan") is None
assert classify_pi_item_command("stop execution") is None
assert classify_user_message("resume") == ("command", "execute")
assert classify_user_message("resume this feature") == ("command", "resume_item")
assert classify_user_message("undo the changes") == ("command", "undo_item")
assert classify_user_message("pause") == ("command", "pause")

SESSION = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
IDENTITY = "11111111-1111-1111-1111-111111111111"
CATALOG = "22222222-2222-2222-2222-222222222222"


def _plan(
    *,
    mid: str,
    name: str,
    relations: str,
    version: int = 1,
    features: str | None = None,
    bugs: str | None = None,
    session: str = SESSION,
) -> str:
    feat = features or "- v1: own the primary resource."
    bug_block = f"## Bugs\n\n{bugs}\n\n" if bugs else ""
    return (
        f"# Plan spec\n\n"
        f"- action: `plan`\n"
        f"- design_session_id: `{session}`\n"
        f"- design_version: `{version}`\n"
        f"- microservice_id: `{mid}`\n"
        f"- microservice_name: `{name}`\n\n"
        f"## Entity relationships\n\n{relations}\n\n"
        f"## Features / functionality\n\n{feat}\n\n"
        f"{bug_block}"
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
assert parsed.bug_spec == ""
parsed_bugs = parse_handoff(
    _plan(
        mid=IDENTITY,
        name="IdentityService",
        relations=identity_relations,
        bugs="- Login lockout after repeated failed authentications.",
    )
)
assert "lockout" in parsed_bugs.bug_spec.lower()

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
ident_digest = (ident.get("discussion_digest") or "").lower()
cat_digest = (cat.get("discussion_digest") or "").lower()
assert "thumbnail_url" in ident_digest, ident.get("discussion_digest")
assert "thumbnail_url" in cat_digest, cat.get("discussion_digest")

print("identity off-topic chat asks to clarify…", flush=True)
s = store.chat(SESSION, "asdf what is the weather in paris", service_id=IDENTITY)
ident = s.find(IDENTITY)
assert ident is not None
off = (ident.get("messages") or [{}])[-1].get("content", "")
assert "previous message" in off.lower() or "open point" in off.lower() or "clarif" in off.lower(), off[:400]
assert "weather" not in (ident.get("discussion_digest") or "").lower()

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
assert any("tests passed" in str(it.get("notes") or "").lower() for it in done), done
assert any((private / "items").glob("*/test_impl.py"))
ok_tests, test_detail = run_workspace_tests(private)
assert ok_tests, test_detail
assert (private / "IMPLEMENTATION_STATUS.md").is_file()
assert "Implementation status" in str(ident.get("implementation_status") or "")
last_asst = ""
for msg in reversed(ident.get("messages") or []):
    if isinstance(msg, dict) and msg.get("role") == "assistant":
        last_asst = str(msg.get("content") or "")
        break
assert "implementation status" in last_asst.lower(), last_asst[:400]

print("Pi questions go to the user; answers go back…", flush=True)
pending_item = next(
    it
    for it in (ident.get("execution_plan") or {}).get("items") or []
    if it.get("status") == "pending"
)
s = store.surface_pi_questions(
    SESSION,
    service_id=IDENTITY,
    item=pending_item,
    questions=["Which auth header should clients send?"],
)
ident = s.find(IDENTITY)
assert ident is not None
assert ident["status"] == "blocked"
assert (ident.get("block_issue") or {}).get("kind") == "pi_questions"
q_note = str((ident.get("messages") or [{}])[-1].get("content") or "")
assert "auth header" in q_note.lower(), q_note[:400]
s = store.chat(SESSION, "Send Authorization: Bearer <token>.", service_id=IDENTITY)
ident = s.find(IDENTITY)
assert ident is not None
assert ident["status"] == "executing"
passed = json.loads(answers_path_for(private, str(pending_item.get("id"))).read_text())
assert "Bearer" in str(passed.get("answers") or ""), passed

print("stop / resume current item, then undo Pi work…", flush=True)
s = store.chat(SESSION, "stop working on current feature", service_id=IDENTITY)
ident = s.find(IDENTITY)
assert ident is not None
assert ident.get("pi_hold")
stop_note = str((ident.get("messages") or [{}])[-1].get("content") or "")
assert "successfully" in stop_note.lower(), stop_note[:400]
s = store.tick_execution(SESSION, service_id=IDENTITY)
ident = s.find(IDENTITY)
assert ident is not None
assert ident.get("pi_hold")
done_after_hold = [
    it for it in (ident.get("execution_plan") or {}).get("items") or [] if it.get("status") == "done"
]
assert len(done_after_hold) == 1, "hold must not start the next item"
s = store.chat(SESSION, "resume this item", service_id=IDENTITY)
ident = s.find(IDENTITY)
assert ident is not None
assert not ident.get("pi_hold")
resume_note = str((ident.get("messages") or [{}])[-1].get("content") or "")
assert "successfully" in resume_note.lower(), resume_note[:400]
s = store.chat(SESSION, "undo the changes", service_id=IDENTITY)
ident = s.find(IDENTITY)
assert ident is not None
undone = [
    it
    for it in (ident.get("execution_plan") or {}).get("items") or []
    if it.get("status") == "stopped"
]
assert undone, ident.get("execution_plan")
undone_dir = private / "items" / str(undone[0].get("id") or "")
assert undone_dir.is_dir(), undone_dir
assert not (undone_dir / "impl.py").is_file()
assert (undone_dir / "SPEC.md").is_file()
undo_note = str((ident.get("messages") or [{}])[-1].get("content") or "")
assert "successfully" in undo_note.lower(), undo_note[:400]
s = store.chat(SESSION, "resume this item", service_id=IDENTITY)
ident = s.find(IDENTITY)
assert ident is not None
assert not ident.get("pi_hold")

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
        bugs="- Login lockout must persist across instances.",
    )
)
ident = s.find(IDENTITY)
assert ident is not None
assert ident["status"] == "awaiting_plan"
assert str(ident.get("previous_plan_spec") or "").strip()
assert "lockout" in str(ident.get("bug_spec") or "").lower()
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

print("item tests must pass before the next item…", flush=True)
fail_root = tmp / "fail-ws"
fail_root.mkdir()
write_item_work(
    fail_root,
    item={"id": "item-x", "title": "X", "kind": "feature", "priority": 1},
    microservice_name="Svc",
)
ok_tests, test_detail = run_workspace_tests(fail_root)
assert ok_tests, test_detail
failing = next(fail_root.glob("items/*/test_impl.py"))
failing.write_text(
    "import unittest\n\n"
    "class T(unittest.TestCase):\n"
    "    def test_x(self) -> None:\n"
    "        self.fail('intentional')\n",
    encoding="utf-8",
)
ok_tests, test_detail = run_workspace_tests(fail_root)
assert not ok_tests
assert "failed" in test_detail.lower() or "intentional" in test_detail.lower()

print("peer contract blocker waits for approve to continue…", flush=True)
PEER_SID = "bbbbbbbb-bbbb-cccc-dddd-eeeeeeeeeeee"
s = store.ingest(
    _plan(
        mid=IDENTITY,
        name="IdentityService",
        relations=identity_relations,
        session=PEER_SID,
    )
)
ident = s.find(IDENTITY)
assert ident is not None
assert ident["status"] == "awaiting_plan"
s = store.approve(PEER_SID, service_id=IDENTITY)
ident = s.find(IDENTITY)
assert ident is not None
assert ident["status"] == "executing"
for _ in range(6):
    ident = s.find(IDENTITY)
    if ident and ident.get("status") == "blocked":
        break
    s = store.tick_execution(PEER_SID, service_id=IDENTITY)
ident = s.find(IDENTITY)
assert ident is not None
assert ident["status"] == "blocked", ident.get("status")
issue = ident.get("block_issue") or {}
assert issue.get("kind") == "peer_contract", issue
pub = next(row for row in s.to_public()["sub_agents"] if row["microservice_id"] == IDENTITY)
assert pub["can_approve"] is True
assert pub["approve_label"] == "Approve to continue"
assert pub["block_issue"]["kind"] == "peer_contract"
assert "click" not in ((ident.get("messages") or [{}])[-1].get("content") or "").lower()
try:
    store.execute(PEER_SID, service_id=IDENTITY)
    raise AssertionError("execute must not resume a blocker")
except PermissionError:
    pass
s = store.chat(PEER_SID, "execute", service_id=IDENTITY)
ident = s.find(IDENTITY)
assert ident is not None
assert ident["status"] == "blocked"
s = store.ingest(
    _plan(
        mid=CATALOG,
        name="VideoCatalogService",
        relations=catalog_relations,
        session=PEER_SID,
    )
)
ident = s.find(IDENTITY)
assert ident is not None
assert ident["status"] == "blocked"
s = store.approve(PEER_SID, service_id=IDENTITY)
ident = s.find(IDENTITY)
assert ident is not None
assert ident["status"] == "executing"
assert not ident.get("block_issue")
s = store.tick_execution(PEER_SID, service_id=IDENTITY)
ident = s.find(IDENTITY)
assert ident is not None
assert ident["status"] == "shipped", ident.get("status")
peer_item = next(
    it
    for it in (ident.get("execution_plan") or {}).get("items") or []
    if it.get("peer_services")
)
assert peer_item.get("status") == "done"
assert "VideoCatalogService" in (peer_item.get("contracts") or {}), peer_item

print("blocked chat instructions then approve…", flush=True)
INSTR_SID = "eeeeeeee-eeee-ffff-0000-111111111111"
s = store.ingest(
    _plan(
        mid=IDENTITY,
        name="IdentityService",
        relations=identity_relations,
        session=INSTR_SID,
    )
)
s = store.approve(INSTR_SID, service_id=IDENTITY)
for _ in range(6):
    ident = s.find(IDENTITY)
    if ident and ident.get("status") == "blocked":
        break
    s = store.tick_execution(INSTR_SID, service_id=IDENTITY)
ident = s.find(IDENTITY)
assert ident is not None
assert ident["status"] == "blocked", ident.get("status")
watcher = store.watch(INSTR_SID)
s = store.chat(
    INSTR_SID,
    "Use this contract with VideoCatalogService: GET /videos/{id} returns owner_id.",
    service_id=IDENTITY,
)
ident = s.find(IDENTITY)
assert ident is not None
assert ident["status"] == "blocked"
issue = ident.get("block_issue") or {}
assert "GET /videos" in str(issue.get("instructions") or ""), issue
live = watcher.get(timeout=2)
assert live is not None
assert live["design_session_id"] == INSTR_SID
blocked_pub = next(row for row in live["sub_agents"] if row["microservice_id"] == IDENTITY)
assert "GET /videos" in str((blocked_pub.get("block_issue") or {}).get("instructions") or "")
store.unwatch(INSTR_SID, watcher)
s = store.chat(INSTR_SID, "Why are you blocked?", service_id=IDENTITY)
ident = s.find(IDENTITY)
assert ident is not None
assert ident["status"] == "blocked"
assert "GET /videos" in str((ident.get("block_issue") or {}).get("instructions") or "")
s = store.approve(INSTR_SID, service_id=IDENTITY)
ident = s.find(IDENTITY)
assert ident is not None
assert ident["status"] == "executing"
s = store.tick_execution(INSTR_SID, service_id=IDENTITY)
ident = s.find(IDENTITY)
assert ident is not None
assert ident["status"] == "shipped", ident.get("status")
peer_item = next(
    it
    for it in (ident.get("execution_plan") or {}).get("items") or []
    if it.get("peer_services")
)
assert peer_item.get("status") == "done"
assert "GET /videos" in str((peer_item.get("contracts") or {}).get("VideoCatalogService") or ""), peer_item

print("git pull blocker waits for approve to continue…", flush=True)
GIT_SID = "cccccccc-cccc-dddd-eeee-ffffffffffff"
GIT_MID = "33333333-3333-3333-3333-333333333333"
orig_prep = sessions_mod.prepare_workspace

def _failing_prep(**kwargs):
    private, _err = orig_prep(**kwargs)
    return private, "Authentication failed against the git remote."

sessions_mod.prepare_workspace = _failing_prep
try:
    s = store.ingest(
        _plan(
            mid=GIT_MID,
            name="BillingService",
            relations=(
                "### User (kind: user)\n"
                "- We initiate: no. Callers initiate toward this service.\n"
                "- Relationship: invoices.\n"
            ),
            session=GIT_SID,
        )
    )
    s = store.approve(GIT_SID, service_id=GIT_MID)
    billing = s.find(GIT_MID)
    assert billing is not None
    assert billing["status"] == "blocked", billing.get("status")
    issue = billing.get("block_issue") or {}
    assert issue.get("kind") == "git_pull", issue
    assert "git" in str(issue.get("detail") or "").lower()
    pub = next(row for row in s.to_public()["sub_agents"] if row["microservice_id"] == GIT_MID)
    assert pub["approve_label"] == "Approve to continue"
    s = store.approve(GIT_SID, service_id=GIT_MID)
    billing = s.find(GIT_MID)
    assert billing is not None
    assert billing["status"] == "blocked"
finally:
    sessions_mod.prepare_workspace = orig_prep
s = store.approve(GIT_SID, service_id=GIT_MID)
billing = s.find(GIT_MID)
assert billing is not None
assert billing["status"] == "executing", billing.get("status")
assert not billing.get("block_issue")

print("failed item tests block until approve to continue…", flush=True)
TEST_SID = "dddddddd-dddd-eeee-ffff-000000000000"
TEST_MID = "44444444-4444-4444-4444-444444444444"
orig_run = sessions_mod.run_workspace_tests

def _failing_run(_private):
    return False, "Tests failed; I will not start the next plan item.\nbogus suite"

sessions_mod.run_workspace_tests = _failing_run
try:
    s = store.ingest(
        _plan(
            mid=TEST_MID,
            name="NotifyService",
            relations=(
                "### User (kind: user)\n"
                "- We initiate: no.\n"
                "- Relationship: notifications.\n"
            ),
            session=TEST_SID,
        )
    )
    s = store.approve(TEST_SID, service_id=TEST_MID)
    s = store.tick_execution(TEST_SID, service_id=TEST_MID)
    notify = s.find(TEST_MID)
    assert notify is not None
    assert notify["status"] == "blocked", notify.get("status")
    issue = notify.get("block_issue") or {}
    assert issue.get("kind") == "tests_failed", issue
    blocked_items = [
        it
        for it in (notify.get("execution_plan") or {}).get("items") or []
        if it.get("status") == "blocked"
    ]
    assert blocked_items, notify.get("execution_plan")
finally:
    sessions_mod.run_workspace_tests = orig_run
s = store.approve(TEST_SID, service_id=TEST_MID)
s = store.tick_execution(TEST_SID, service_id=TEST_MID)
notify = s.find(TEST_MID)
assert notify is not None
assert notify["status"] == "executing"
done = [it for it in (notify.get("execution_plan") or {}).get("items") or [] if it.get("status") == "done"]
assert done, notify.get("execution_plan")

print("OK", flush=True)
sys.exit(0)
