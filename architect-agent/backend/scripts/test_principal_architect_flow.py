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
    is_advance_request,
    is_informational_query,
    is_revision_request,
    is_step_approval_message,
)
from architect_agent.json_util import parse_llm_json_object
from architect_agent.sessions import SessionStore, _legacy_map

get_settings.cache_clear()
get_chat_model.cache_clear()
reset_graph()

assert is_informational_query("Why is this HLD rather than LLD?")
assert is_informational_query("Show me all URL endpoints we agreed on")
assert is_revision_request("Please switch to a modular monolith")
assert is_revision_request("Add a health check endpoint")
assert is_revision_request("Why is there no rate limiting?")
assert not is_revision_request("Why is this HLD rather than LLD?")
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
track0 = s.design_track
s = store.chat(s.session_id, "Why is this HLD rather than LLD?")
print("  phase0 qa", s.phase, s.design_track, s.to_public()["can_approve"], flush=True)
assert s.phase == "phase0", s.phase
assert s.design_track == track0
assert s.to_public()["can_approve"]
assert s.messages[-1]["role"] == "assistant"
assert s.messages[-1]["content"].strip()
s = store.chat(s.session_id, "Looks good")
print("  after phase0", s.phase, s.design_track, s.design_step, flush=True)
assert s.design_track == "hld", s.design_track
assert s.phase == "hld" and s.design_step == 1, (s.phase, s.design_step)

for step in range(1, 7):
    assert s.phase == "hld" and s.design_step == step, (s.phase, s.design_step)
    assert s.to_public()["can_approve"], s.to_public()
    if step == 1:
        s = store.chat(s.session_id, "next step")
        print(f"  after next-step chat@{step}", s.phase, s.design_step, flush=True)
        assert s.phase == "hld" and s.design_step == 2
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
    s2 = store2.approve(s2.session_id)
    print(f"  after approve@{step}", s2.phase, s2.design_step, flush=True)
assert s2.phase == "market_research"
s2 = store2.approve(s2.session_id)
assert s2.phase == "lld" and s2.design_step == 3
assert s2.design_version >= 1

print("legacy map…", flush=True)
mapped = _legacy_map({"phase": "spec_interview"})
assert mapped["phase"] == "phase0"
mapped = _legacy_map({"phase": "system_design"})
assert mapped["phase"] == "hld" and mapped["design_step"] == 4

print("OK", flush=True)
