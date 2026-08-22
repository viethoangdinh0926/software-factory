#!/usr/bin/env python3
"""Smoke: Phase 0 → HLD steps → design approve → market → handoff → resume step 4.
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
    classify_user_message,
    is_advance_request,
    is_informational_query,
    is_revision_request,
    is_step_approval_message,
    promote_chat_to_approve,
    with_next_prompt,
    without_user_echo,
)
from architect_agent.json_util import parse_llm_json_object
from architect_agent.graph.nodes.common import assistant_message_is_thin, ensure_step_briefing
from architect_agent.sessions import SessionStore, _legacy_map

get_settings.cache_clear()
get_chat_model.cache_clear()
reset_graph()

assert classify_user_message("Approve") == ("command", "approve")
assert classify_user_message("I'm happy with this, let's continue") == ("command", "approve")
assert classify_user_message("Why is this HLD rather than LLD?") == ("information", "answer")
assert classify_user_message("Show me all URL endpoints we agreed on") == ("information", "answer")
assert classify_user_message("Add a health check endpoint") == ("command", "revise")
assert is_informational_query("Why is this HLD rather than LLD?")
assert is_informational_query("Show me all URL endpoints we agreed on")
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
assert NEXT_PROMPT_HEADER in with_next_prompt("Here is the proposal.")
assert with_next_prompt(with_next_prompt("Here is the proposal.")).count(NEXT_PROMPT_HEADER) == 1
assert "Session marked done." == with_next_prompt("Session marked done.")
assert assistant_message_is_thin("LLD step 1 update.")
assert assistant_message_is_thin("HLD step 2 update.")
assert assistant_message_is_thin("Design updated.")
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
assert NEXT_PROMPT_HEADER in s.messages[-1]["content"]
track0 = s.design_track
s = store.chat(s.session_id, "Why is this HLD rather than LLD?")
print("  phase0 qa", s.phase, s.design_track, s.to_public()["can_approve"], flush=True)
assert s.phase == "phase0", s.phase
assert s.design_track == track0
assert s.to_public()["can_approve"]
assert s.messages[-1]["role"] == "assistant"
assert s.messages[-1]["content"].strip()
assert NEXT_PROMPT_HEADER in s.messages[-1]["content"]
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
        s = store.chat(s.session_id, "next step")
        print(f"  after next-step chat@{step}", s.phase, s.design_step, flush=True)
        assert s.phase == "hld" and s.design_step == 2
        assert NEXT_PROMPT_HEADER in s.messages[-1]["content"]
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
        assert "Updates to this proposal" in last, last[:400]
        assert "None" in last, last[:400]
        assert NEXT_PROMPT_HEADER in last, last[-400:]
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
        assert NEXT_PROMPT_HEADER in s.messages[-1]["content"]
        s = store.chat(s.session_id, "I'm worried the grade ignored compliance.")
        print("  market concern", s.phase, flush=True)
        assert s.phase == "market_research", s.phase
        market_last = s.messages[-1]["content"]
        assert "compliance" in market_last.lower() or "worried" in market_last.lower(), market_last[:400]
        assert s.to_public()["can_approve"]

version_before = s.design_version
s = store.approve(s.session_id)
print("  after market continue", s.phase, s.design_step, s.design_version, flush=True)
assert s.phase == "hld" and s.design_step == 4, (s.phase, s.design_step)
assert s.design_version == version_before + 1
assert s.design_diagram.strip()
assert s.communication_schemes.strip()
assert s.tradeoff_ledger.strip()
pkg = store.final_design_markdown(s.session_id)
assert "## Core Microservices" in pkg
assert "## Communication Schemes" in pkg
assert "## API Contracts" not in pkg

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
assert s2.phase == "lld" and s2.design_step == 3
assert s2.design_version >= 1

print("phase0 interview addresses concerns…", flush=True)
get_settings.cache_clear()
get_chat_model.cache_clear()
reset_graph()
store3 = SessionStore()
s3 = store3.start("I want an app like youtube")
assert s3.phase == "phase0"
s3 = store3.chat(
    s3.session_id,
    "I'm worried about GDPR and we should only allow EU users at first. Why no data residency?",
)
last3 = s3.messages[-1]["content"]
assert s3.phase == "phase0"
assert "GDPR" in last3 or "residenc" in last3.lower() or "EU" in last3, last3[:400]
assert "Updates to this proposal" in last3, last3[:400]
# Must not be only the next canned question.
assert len(last3) > 120, last3

print("hld concern is addressed…", flush=True)
s = store.chat(s.session_id, "We should add rate limiting at the gateway.")
# After market continue, s is at hld step 4
assert s.phase == "hld", s.phase
hld_last = s.messages[-1]["content"]
assert "rate limit" in hld_last.lower(), hld_last[:400]
assert "We should add rate limiting at the gateway." not in hld_last, hld_last[:400]
assert "Updates to this proposal" in hld_last, hld_last[:400]

print("legacy map…", flush=True)
mapped = _legacy_map({"phase": "spec_interview"})
assert mapped["phase"] == "phase0"
mapped = _legacy_map({"phase": "system_design"})
assert mapped["phase"] == "hld" and mapped["design_step"] == 4

print("OK", flush=True)
