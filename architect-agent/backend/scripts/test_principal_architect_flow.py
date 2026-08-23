#!/usr/bin/env python3
"""Smoke: Phase 0 → HLD steps → design approve → market → handoff → new round at Phase 0.
Also covers a short LLD path."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ["LLM_PROVIDER"] = "stub"

# Isolate session + checkpoint storage for this smoke run.
tmp = Path(tempfile.mkdtemp(prefix="architect-smoke-"))
os.environ["DATA_DIR"] = str(tmp / "sessions")

print("importing…", flush=True)
from architect_agent.config import get_settings
from architect_agent.llm import get_chat_model
from architect_agent.graph import reset_graph
from architect_agent.query_intent import (
    NEXT_PROMPT_HEADER,
    UPDATES_HEADER,
    classify_user_message,
    is_advance_request,
    is_informational_query,
    is_revision_request,
    is_step_approval_message,
    promote_chat_to_approve,
    with_next_prompt,
    with_resolution_close,
    without_user_echo,
)
from architect_agent.json_util import parse_llm_json_object
from architect_agent.graph.nodes.common import assistant_message_is_thin, ensure_step_briefing
from architect_agent.design_diagram import (
    catalog_covers_diagram,
    diagram_is_due,
    diagram_node_count,
    ensure_component_catalog,
    ensure_design_diagram,
    extract_spec_section,
    upsert_spec_section,
    with_component_walkthrough,
)
from architect_agent.graph.state import _keep_nonempty_str
from architect_agent.design_progress import classify_rewind_stage, design_position, package_fingerprint
from architect_agent.scope import recommend_design_track, resolve_design_track, wants_standalone
from architect_agent.sessions import SessionStore, _legacy_map

get_settings.cache_clear()
get_chat_model.cache_clear()
reset_graph()

assert classify_user_message("Approve") == ("command", "approve")
assert classify_user_message("I'm happy with this, let's continue") == ("command", "approve")
assert classify_user_message("Why is this HLD rather than LLD?") == ("information", "answer")
assert classify_user_message("Show me all URL endpoints we agreed on") == ("information", "answer")
assert classify_user_message("Add a health check endpoint") == ("command", "revise")
# Example phrasings of "help me answer your questions" — not a keyword list.
assert classify_user_message("Give me some potential answers") == ("information", "answer")
assert classify_user_message("What would you pick for those?") == ("information", "answer")
assert classify_user_message("Can you draft replies I could use?") == ("information", "answer")
assert is_informational_query("Why is this HLD rather than LLD?")
assert is_informational_query("Show me all URL endpoints we agreed on")
assert is_informational_query("Draft some replies I can use")
assert not is_revision_request("What would you pick for those?")
assert is_revision_request("Please switch to a modular monolith")
assert is_revision_request("Add a health check endpoint")
assert is_revision_request("Why is there no rate limiting?")
assert not is_revision_request("Why is this HLD rather than LLD?")
assert not is_revision_request("Approve")
assert is_step_approval_message("Approve")
assert is_step_approval_message("Looks good")
assert is_step_approval_message("lgtm")
assert is_step_approval_message("next step")
assert is_step_approval_message("Let's move on")
assert is_step_approval_message("wrap up this step")
assert is_advance_request("next step")
assert is_advance_request("we need to move on")
assert not is_advance_request("What's the next step?")
assert not is_step_approval_message("Why should I approve REST?")
assert not is_step_approval_message("Looks good, add a health check")
assert not is_step_approval_message("next step, add a health check")
assert promote_chat_to_approve("chat", "Approve", can_approve=False) == "approve"
echoed = without_user_echo(
    "I applied your latest comments (We should add rate limiting).\n\nHLD step 1 is ready.",
    "We should add rate limiting",
)
assert "We should add rate limiting" not in echoed
assert "HLD step 1 is ready." in echoed
assert "I heard you:" not in without_user_echo("I heard you: GDPR please.\nNext question?", "GDPR please")
assert NEXT_PROMPT_HEADER not in with_next_prompt("Here is the proposal.")
assert "click" not in without_user_echo(
    "Scope looks like **HLD**. Click **Approve** to start that track."
).lower()
assert "confirm, approve, or agree" in without_user_echo(
    "Scope looks like **HLD**. Click **Approve** to start that track."
).lower()
assert with_next_prompt(
    "Here is the proposal.\n\n**What you can do next**\n- Click **Approve**."
) == "Here is the proposal."
assert UPDATES_HEADER not in with_next_prompt(
    "Here is the proposal.\n\n**Updates to this proposal**\n- None — unchanged."
)
assert UPDATES_HEADER not in with_resolution_close("Here is the proposal.", changed=True)
assert "Session marked done." == with_next_prompt("Session marked done.")
assert assistant_message_is_thin("LLD step 1 update.")
assert assistant_message_is_thin("HLD step 2 update.")
assert assistant_message_is_thin("Design updated.")
assert design_position("phase0", "hld", 0) == 0
assert design_position("hld", "hld", 2) < design_position("hld", "hld", 4)
assert wants_standalone("Make this a local self-contained stand-alone application")
assert not wants_standalone("Please switch to a modular monolith")
assert not wants_standalone("I want an app like youtube")
assert (
    resolve_design_track(
        "hld",
        pending="Route this to a local self-contained stand-alone application.",
        spec="Distributed multi-tenant inventory tracker with microservices.",
        prior="hld",
    )
    == "lld"
)
assert (
    resolve_design_track(
        "hld",
        pending="We also need GDPR: EU-only users in v1.",
        spec="Distributed multi-tenant inventory tracker with microservices.",
        prior="hld",
    )
    == "hld"
)
assert recommend_design_track(spec="A local CLI for quote math in one OS process.") == "lld"
assert (
    recommend_design_track(
        spec="Multi-tenant warehouse inventory SaaS with microservices."
    )
    == "hld"
)
assert recommend_design_track(spec="A vague product idea with no topology yet.") in {
    "lld",
    "hld",
}
assert classify_rewind_stage(
    "Make this a local self-contained stand-alone application, not a distributed system.",
    "phase=hld track=hld step=3",
    "hld",
) == "phase0"
assert classify_rewind_stage(
    "We also need GDPR: EU-only users in v1.",
    "phase=hld track=hld step=2",
    "hld",
) == "phase0"
assert classify_rewind_stage("Skip to FMEA", "phase=hld track=hld step=1", "hld") == "ahead"
assert package_fingerprint(
    "# System Design Package\nDesign version: `1`\nGenerated: a\n\n## Spec\nhello\n"
) == package_fingerprint(
    "# System Design Package\nDesign version: `2`\nGenerated: b\n\n## Spec\nhello\n"
)
assert not assistant_message_is_thin(
    "HLD step 1 locked a 50k DAU / 120k read-QPS plan because catalog reads dominate "
    "writes; a single-region monolith was rejected for tenant isolation. "
    "Postgres stays the system of record; Redis is a read-through cache only."
)
long_rule = (
    "The application must use a dependency management system (e.g., package manager) "
    "that supports cross-platform compilation (e.g., Go modules, npm, or standard library vendors)."
)
brief = ensure_step_briefing(
    "LLD step 1 update.",
    track="lld",
    step=1,
    title="Information gathering",
    artifacts={
        "business_spec": (
            "## Invariants\n- No silent stock loss\n- Quotes are deterministic\n"
            f"**Dependency Management:** {long_rule}\n"
        )
    },
    primary_field="business_spec",
)
assert "No silent stock loss" in brief
assert long_rule in brief
assert "**Dependency Management:**" in brief
assert "standar\n" not in brief
assert "LLD step 1 update." not in brief

latex_json = r'{"assistant_message": "DAU $\text{assumed}$ is $\approx$ 5k"}'
latex_msg = parse_llm_json_object(latex_json)["assistant_message"]
assert "\t" not in latex_msg, repr(latex_msg)
assert r"\text" in latex_msg
assert r"\approx" in latex_msg

print("HLD path…", flush=True)
store = SessionStore()
s = store.start(
    "# Warehouse Inventory SaaS\n\n"
    "Distributed multi-tenant inventory tracker with microservices.\n\n"
    "## Actors\n- Warehouse clerk\n\n## In scope (v1)\n- receive/adjust/report\n\n"
    "## Out of scope\n- accounting\n\n## Critical invariants\n- no silent stock loss\n\n"
    "## Success criteria\n- clerks can adjust counts\n\n## Assumptions & risks\n- multi-region later\n"
)
print("  start", s.phase, s.design_track, s.design_step, s.to_public()["can_approve"], flush=True)
pub = s.to_public()
assert s.phase == "phase0", s.phase
assert pub["can_approve"], pub
assert NEXT_PROMPT_HEADER not in s.messages[-1]["content"]
track0 = s.design_track
s = store.chat(s.session_id, "Why is this HLD rather than LLD?")
print("  phase0 qa", s.phase, s.design_track, s.to_public()["can_approve"], flush=True)
assert s.phase == "phase0", s.phase
assert s.design_track == track0
assert s.to_public()["can_approve"]
assert s.messages[-1]["role"] == "assistant"
assert s.messages[-1]["content"].strip()
assert NEXT_PROMPT_HEADER not in s.messages[-1]["content"]
s = store.chat(s.session_id, "Looks good")
print("  after phase0", s.phase, s.design_track, s.design_step, flush=True)
assert s.design_track == "hld", s.design_track
assert s.phase == "hld" and s.design_step == 1, (s.phase, s.design_step)
hld1 = s.messages[-1]["content"]
assert "HLD step 1 update." not in hld1, hld1[:200]
assert not assistant_message_is_thin(hld1.split("**What you can do next**")[0]), hld1[:400]

for step in range(1, 7):
    assert s.phase == "hld" and s.design_step == step, (s.phase, s.design_step)
    assert s.to_public()["can_approve"], s.to_public()
    last_hld = s.messages[-1]["content"]
    assert f"HLD step {step} update." not in last_hld, last_hld[:240]
    assert "locked in" in last_hld.lower() or not assistant_message_is_thin(
        last_hld.split("**What you can do next**")[0]
    ), last_hld[:400]
    if step == 1:
        s = store.chat(s.session_id, "Skip to FMEA")
        print("  skip ahead blocked", s.phase, s.design_step, flush=True)
        assert s.phase == "hld" and s.design_step == 1
        skip_last = s.messages[-1]["content"]
        assert "jump" in skip_last.lower() or "one confirmed step" in skip_last.lower(), skip_last[:400]
        s = store.chat(s.session_id, "next step")
        print(f"  after next-step chat@{step}", s.phase, s.design_step, flush=True)
        assert s.phase == "hld" and s.design_step == 2
        assert NEXT_PROMPT_HEADER not in s.messages[-1]["content"]
        continue
    if step == 2:
        scale_before = s.scale_estimates
        assert scale_before.strip(), "HLD step 1 capacity plan should already exist"
        s = store.chat(
            s.session_id,
            "We also need GDPR: EU-only users in v1 as a spec requirement.",
        )
        print("  rewind to phase0", s.phase, s.design_step, s.design_track, s.ready_to_advance, flush=True)
        assert s.phase == "phase0", s.phase
        assert s.design_track == "hld", s.design_track
        assert s.to_public()["can_approve"]
        assert s.scale_estimates.strip() == scale_before.strip()
        rewind_last = s.messages[-1]["content"]
        assert "Phase 0" in rewind_last, rewind_last[:400]
        assert "stay" in rewind_last.lower() or "later" in rewind_last.lower() or "patch" in rewind_last.lower(), rewind_last[:400]
        s = store.approve(s.session_id)
        print("  after rewind approve", s.phase, s.design_step, flush=True)
        assert s.phase == "hld" and s.design_step == 1, (s.phase, s.design_step)
        assert s.scale_estimates.strip() == scale_before.strip()
        # Re-walk to the step the outer loop expects next.
        s = store.approve(s.session_id)
        print("  rewalk to step 2", s.phase, s.design_step, flush=True)
        assert s.phase == "hld" and s.design_step == 2
        assert s.scale_estimates.strip() == scale_before.strip()
        s = store.approve(s.session_id)
        print(f"  after approve@{step}", s.phase, s.design_step, flush=True)
        continue
    if step == 3:
        assert s.to_public()["design_step_title"] == "Core microservices"
        apis = s.api_contracts
        s = store.chat(s.session_id, "Which domain objects does IdentityService own?")
        print("  hld3 qa", s.phase, s.design_step, flush=True)
        assert s.phase == "hld" and s.design_step == 3
        assert s.api_contracts == apis
        assert s.to_public()["can_approve"]
        last = s.messages[-1]["content"]
        assert "IdentityService" in last or "User" in last or "own" in last.lower(), last[:240]
        assert "Updates to this proposal" not in last, last[:400]
        assert NEXT_PROMPT_HEADER not in last, last[-400:]
    s = store.approve(s.session_id)
    print(f"  after approve@{step}", s.phase, s.design_step, flush=True)
    if step < 6:
        assert s.phase == "hld" and s.design_step == step + 1
    else:
        assert s.phase == "market_research", s.phase
        assert s.market_evaluation_done
        assert s.market_evaluation_report.strip()
        s = store.chat(s.session_id, "What was the market evaluation grade?")
        print("  market qa", s.phase, s.to_public()["can_approve"], flush=True)
        assert s.phase == "market_research", s.phase
        assert s.to_public()["can_approve"]
        assert s.messages[-1]["role"] == "assistant"
        assert s.messages[-1]["content"].strip()
        assert NEXT_PROMPT_HEADER not in s.messages[-1]["content"]
        s = store.chat(s.session_id, "I'm worried the grade ignored compliance.")
        print("  market concern", s.phase, flush=True)
        assert s.phase == "market_research", s.phase
        market_last = s.messages[-1]["content"]
        assert "compliance" in market_last.lower() or "worried" in market_last.lower(), market_last[:400]
        assert s.to_public()["can_approve"]

version_before = s.design_version
s = store.approve(s.session_id)
print("  after market continue", s.phase, s.design_step, s.design_version, flush=True)
assert s.phase == "phase0" and s.design_step == 0, (s.phase, s.design_step)
assert s.design_version == version_before + 1
round_last = s.messages[-1]["content"]
assert "Phase 0" in round_last, round_last[:400]
assert s.design_diagram.strip()
assert s.communication_schemes.strip()
assert s.tradeoff_ledger.strip()
pkg = store.final_design_markdown(s.session_id)
assert "## Core Microservices" in pkg
assert "## Communication Schemes" in pkg
assert "## API Contracts" not in pkg

print("second round with no changes skips handoff…", flush=True)
v1 = s.design_version
scale_kept = s.scale_estimates
apis_kept = s.api_contracts
s = store.approve(s.session_id)
print("  second-round hld1", s.phase, s.design_step, flush=True)
assert s.phase == "hld" and s.design_step == 1
assert s.scale_estimates.strip() == scale_kept.strip()
assert s.api_contracts.strip() == apis_kept.strip()
for _ in range(6):
    s = store.approve(s.session_id)
    print(f"  second-round", s.phase, s.design_step, flush=True)
assert s.phase == "market_research", s.phase
s = store.approve(s.session_id)
print("  second-round after market", s.phase, s.design_version, flush=True)
assert s.phase == "phase0" and s.design_step == 0
assert s.design_version == v1, s.design_version
skip_last = s.messages[-1]["content"]
assert "no design updates" in skip_last.lower(), skip_last[:400]

print("LLD path…", flush=True)
get_settings.cache_clear()
get_chat_model.cache_clear()
reset_graph()
store2 = SessionStore()
s2 = store2.start(
    "# In-process pricing library (LLD)\n\n"
    "Single OS process library for quote calculation. No network services.\n"
)
assert s2.phase == "phase0"
s2 = store2.approve(s2.session_id)
print("  after phase0", s2.phase, s2.design_track, s2.design_step, flush=True)
assert s2.design_track == "lld", s2.design_track
for step in range(1, 4):
    assert s2.design_step == step
    last_lld = s2.messages[-1]["content"]
    assert f"LLD step {step} update." not in last_lld, last_lld[:240]
    assert "locked in" in last_lld.lower() or "step" in last_lld.lower(), last_lld[:240]
    if step == 1:
        s2 = store2.chat(s2.session_id, "Approve")
        advanced = s2.messages[-1]["content"]
        assert "I applied your latest comments" not in advanced, advanced[:400]
        assert s2.design_step == 2, (s2.phase, s2.design_step)
    else:
        s2 = store2.approve(s2.session_id)
    print(f"  after approve@{step}", s2.phase, s2.design_step, flush=True)
assert s2.phase == "market_research"
s2 = store2.approve(s2.session_id)
assert s2.phase == "phase0" and s2.design_step == 0, (s2.phase, s2.design_step)
assert s2.design_version >= 1
assert "Phase 0" in s2.messages[-1]["content"]

print("phase0 suggests answers to its own questions…", flush=True)
get_settings.cache_clear()
get_chat_model.cache_clear()
classify_user_message.cache_clear()
reset_graph()
store4 = SessionStore()
s4 = store4.start("I want an app like youtube")
assert s4.phase == "phase0"
asked4 = s4.messages[-1]["content"]
assert "?" in asked4 or "❓" in asked4, asked4[:400]
assert "## Problem" in s4.business_spec, s4.business_spec[:400]
assert "youtube" in s4.business_spec.lower(), s4.business_spec[:400]
spec_before = s4.business_spec
s4 = store4.chat(s4.session_id, "Draft some replies I can use.")
last4 = s4.messages[-1]["content"]
assert s4.phase == "phase0", s4.phase
assert s4.business_spec == spec_before
assert "Recommended" in last4, last4[:500]
assert "Draft some replies I can use." not in last4
assert "folded that into" not in last4.lower()
assert NEXT_PROMPT_HEADER not in last4

print("phase0 interview addresses concerns…", flush=True)
get_settings.cache_clear()
get_chat_model.cache_clear()
reset_graph()
store3 = SessionStore()
s3 = store3.start("I want an app like youtube")
assert s3.phase == "phase0"
print("phase0 off-topic reply asks to clarify…", flush=True)
s3 = store3.chat(s3.session_id, "asdf what is the weather in paris")
off = s3.messages[-1]["content"]
assert s3.phase == "phase0", s3.phase
assert "need more information to proceed" not in off.lower(), off[:400]
assert (
    "previous message" in off.lower()
    or "open point" in off.lower()
    or "clarif" in off.lower()
), off[:400]
assert "weather" not in (s3.discussion_digest or "").lower()
s3 = store3.chat(
    s3.session_id,
    "I'm worried about GDPR and we should only allow EU users at first. Why no data residency?",
)
last3 = s3.messages[-1]["content"]
assert s3.phase == "phase0"
assert "GDPR" in last3 or "residenc" in last3.lower() or "EU" in last3, last3[:400]
assert "Updates to this proposal" not in last3, last3[:400]
# Must not be only the next canned question.
assert len(last3) > 120, last3
digest3 = (s3.discussion_digest or "").lower()
assert "gdpr" in digest3 or "residenc" in digest3 or "eu" in digest3, s3.discussion_digest[:400]
spec_gdpr = s3.business_spec.lower()
assert "gdpr" in spec_gdpr or "residenc" in spec_gdpr or "eu" in spec_gdpr, s3.business_spec[:600]
assert "## critical invariants" in spec_gdpr or "## problem" in spec_gdpr, s3.business_spec[:400]

print("phase0 confirmation locks and asks a specific next question…", flush=True)
from architect_agent.interview_progress import (
    ensure_specific_question,
    is_vague_question,
    specific_followup_message,
)

stall = (
    "I need more information to proceed. Please provide more details about your system."
)
assert is_vague_question(stall)
assert assistant_message_is_thin(stall)
locked, _ready = specific_followup_message(
    "Local REST collection manager.",
    "I confirm on ignoring sync and security.",
    [],
)
assert "Locked" in locked, locked
assert "sync" in locked.lower()
assert "❓" in locked or "?" in locked
assert "more details about your system" not in locked.lower()
replaced = ensure_specific_question(
    stall,
    spec="Local REST collection manager.",
    pending="I confirm on ignoring sync and security.",
    asked_titles=[],
)
assert "Locked" in replaced
assert not is_vague_question(replaced)

s3 = store3.chat(s3.session_id, "I confirm on ignoring sync and security.")
last_lock = s3.messages[-1]["content"]
assert "need more information to proceed" not in last_lock.lower(), last_lock[:400]
assert "more details about your system" not in last_lock.lower(), last_lock[:400]
assert "Locked" in last_lock or "sync" in last_lock.lower(), last_lock[:500]
assert "?" in last_lock or "❓" in last_lock, last_lock[:400]
spec_lock = s3.business_spec.lower()
assert "gdpr" in spec_lock or "residenc" in spec_lock or "eu" in spec_lock, s3.business_spec[:600]
assert "sync" in spec_lock or "synchronization" in spec_lock, s3.business_spec[:600]

print("phase0 later turn with no checklist still replies to the user…", flush=True)
from architect_agent.graph.nodes.phase0 import phase0_classify_node
from architect_agent.query_intent import USER_MESSAGE_FIRST_RULES

assert "Latest user message is the work" in USER_MESSAGE_FIRST_RULES
assert "Do not start a new interview" in USER_MESSAGE_FIRST_RULES

later = phase0_classify_node(
    {
        "session_id": "later-turn",
        "business_spec": "Local REST collection manager.",
        "messages": [
            {
                "role": "assistant",
                "content": "Should we add sync between devices?",
                "node": "phase0",
            }
        ],
        "pending_user_feedback": "I confirm on ignoring sync and security.",
        "interview_questions": [],
        "current_question_index": 0,
        "interview_answers": {},
        "interview_complete": False,
        "spec_compiled": False,
        "design_track": "unset",
        "design_step": 0,
        "discussion_digest": "Architect raised sync and security.",
        "tradeoff_ledger": "",
        "rewind_notice": "",
    }
)
later_msg = str(later.get("pending_assistant_message") or "")
assert "need more information to proceed" not in later_msg.lower(), later_msg[:400]
assert "more details about your system" not in later_msg.lower(), later_msg[:400]
assert "I will gather just enough" not in later_msg, later_msg[:400]
assert "Locked" in later_msg or "sync" in later_msg.lower(), later_msg[:500]
later_spec = str(later.get("business_spec") or "").lower()
assert "sync" in later_spec or "synchronization" in later_spec, later_spec[:600]

print("phase0 interview compiles spec then classifies…", flush=True)
questions = [
    {"id": "q1", "text": "Who are the primary daily users?", "category": "users"},
    {"id": "q2", "text": "What must never go wrong in v1?", "category": "constraints"},
    {"id": "q3", "text": "Which 3 capabilities must ship in v1?", "category": "business"},
    {"id": "q4", "text": "What will you explicitly not build in v1?", "category": "business"},
    {"id": "q5", "text": "How will you know v1 succeeded?", "category": "success_metrics"},
]
compiled = phase0_classify_node(
    {
        "session_id": "compile-turn",
        "business_spec": "Warehouse inventory tracker notes from the interview.",
        "messages": [
            {
                "role": "assistant",
                "content": questions[-1]["text"],
                "node": "phase0",
            }
        ],
        "pending_user_feedback": "Success is clerks finishing cycle counts without silent stock loss.",
        "interview_questions": questions,
        "current_question_index": 4,
        "interview_answers": {
            "q1": "Warehouse clerks",
            "q2": "No silent stock loss",
            "q3": "Receive, adjust, report",
            "q4": "Accounting",
        },
        "interview_complete": False,
        "spec_compiled": False,
        "design_track": "unset",
        "design_step": 0,
        "discussion_digest": "",
        "tradeoff_ledger": "",
        "rewind_notice": "",
    }
)
compiled_msg = str(compiled.get("pending_assistant_message") or "")
assert compiled.get("interview_complete") is True, compiled
assert compiled.get("spec_compiled") is True, compiled
assert compiled.get("design_track") == "unset", compiled.get("design_track")
assert compiled.get("ready_to_advance") is True, compiled
assert "enough to compile" not in compiled_msg.lower(), compiled_msg[:400]
assert "## Problem" in compiled_msg or "## Actors" in compiled_msg, compiled_msg[:500]
assert "confirm, approve, or agree" in compiled_msg.lower(), compiled_msg[:400]
assert "UNSET" not in compiled_msg

classified = phase0_classify_node(
    {
        "session_id": "compile-turn",
        "business_spec": str(compiled.get("business_spec") or ""),
        "messages": list(compiled.get("messages") or []),
        "pending_user_feedback": "Looks good",
        "interview_questions": questions,
        "current_question_index": 5,
        "interview_answers": compiled.get("interview_answers") or {},
        "interview_complete": True,
        "spec_compiled": True,
        "design_track": "unset",
        "design_step": 0,
        "discussion_digest": str(compiled.get("discussion_digest") or ""),
        "tradeoff_ledger": "",
        "rewind_notice": "",
    }
)
classified_msg = str(classified.get("pending_assistant_message") or "")
assert classified.get("design_track") in {"lld", "hld"}, classified.get("design_track")
assert classified.get("ready_to_advance") is True, classified
assert "UNSET" not in classified_msg, classified_msg[:400]
assert "confirm, approve, or agree" in classified_msg.lower(), classified_msg[:400]
assert classified.get("design_track").upper() in classified_msg, classified_msg[:400]

print("stand-alone routing leaves HLD for LLD…", flush=True)
get_settings.cache_clear()
get_chat_model.cache_clear()
reset_graph()
store5 = SessionStore()
s5 = store5.start(
    "# Warehouse Inventory SaaS\n\n"
    "Distributed multi-tenant inventory tracker with microservices.\n\n"
    "## Actors\n- Warehouse clerk\n\n## In scope (v1)\n- receive/adjust/report\n\n"
    "## Out of scope\n- accounting\n\n## Critical invariants\n- no silent stock loss\n\n"
    "## Success criteria\n- clerks can adjust counts\n\n## Assumptions & risks\n- multi-region later\n"
)
assert s5.design_track == "hld", s5.design_track
s5 = store5.approve(s5.session_id)
assert s5.phase == "hld" and s5.design_step == 1, (s5.phase, s5.design_step)
s5 = store5.chat(
    s5.session_id,
    "Make this a local self-contained stand-alone application, not a distributed system.",
)
print("  after stand-alone chat", s5.phase, s5.design_track, s5.design_step, flush=True)
assert s5.phase == "phase0", s5.phase
assert s5.design_track == "lld", s5.design_track
assert s5.to_public()["can_approve"]
digest5 = (s5.discussion_digest or "").lower()
assert "stand-alone" in digest5 or "standalone" in digest5 or "lld" in digest5, s5.discussion_digest[:500]
assert "deployment topology" in s5.business_spec.lower() or "stand-alone" in s5.business_spec.lower()
last5 = s5.messages[-1]["content"]
assert "lld" in last5.lower() or "stand-alone" in last5.lower() or "standalone" in last5.lower(), last5[:400]
s5 = store5.approve(s5.session_id)
print("  after stand-alone approve", s5.phase, s5.design_track, s5.design_step, flush=True)
assert s5.phase == "lld" and s5.design_step == 1, (s5.phase, s5.design_track, s5.design_step)

print("new-round spec update stays on phase0…", flush=True)
s = store.chat(s.session_id, "We should add rate limiting at the gateway.")
assert s.phase == "phase0", s.phase
round_edit = s.messages[-1]["content"]
assert "We should add rate limiting at the gateway." not in round_edit, round_edit[:400]
assert "rate limit" in round_edit.lower() or "updated" in round_edit.lower(), round_edit[:400]
assert "Updates to this proposal" not in round_edit, round_edit[:400]

print("diagram keep + fallback…", flush=True)
assert _keep_nonempty_str("flowchart LR\n  A[A] --> B[B]", "") == "flowchart LR\n  A[A] --> B[B]"
assert _keep_nonempty_str("old", "flowchart LR\n  A[A]") == "flowchart LR\n  A[A]"
assert diagram_is_due("lld", "lld", 2)
assert diagram_is_due("market_research", "lld", 3)
assert not diagram_is_due("phase0", "unset", 0)
sketch = ensure_design_diagram(
    "## Persistence\nSQLite collections and request history. Tokens stored locally.",
    "",
    track="lld",
    allow_llm=False,
)
assert diagram_node_count(sketch) >= 6, sketch
assert "Sqlite" in sketch and "Http" in sketch
assert "Creds" in sketch
catalog = ensure_component_catalog(sketch, "SQLite collections. Tokens stored locally.", allow_llm=False)
assert "## Diagram components" in catalog
assert "### Desktop UI" in catalog
assert "### Application Shell" in catalog
assert catalog_covers_diagram(catalog, sketch)
spec_with = upsert_spec_section("# Spec\n\n## Goals\n- ship", "Diagram components", catalog)
assert extract_spec_section(spec_with, "Diagram components")
assert spec_with.count("## Diagram components") == 1
walked = with_component_walkthrough("LLD step 2 is ready.", catalog)
assert "Desktop UI" in walked and "Diagram components" in walked
assert with_component_walkthrough(walked, catalog) == walked

print("legacy map…", flush=True)
mapped = _legacy_map({"phase": "spec_interview"})
assert mapped["phase"] == "phase0"
mapped = _legacy_map({"phase": "system_design"})
assert mapped["phase"] == "hld" and mapped["design_step"] == 4

print("OK", flush=True)
