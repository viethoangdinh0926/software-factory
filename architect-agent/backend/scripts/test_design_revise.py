#!/usr/bin/env python3
from __future__ import annotations

import os
import sys

os.environ["LLM_PROVIDER"] = "stub"

print("importing…", flush=True)
from architect_agent.config import get_settings
from architect_agent.llm import get_chat_model
from architect_agent.graph import reset_graph
from architect_agent.sessions import SessionStore

get_settings.cache_clear()
get_chat_model.cache_clear()
reset_graph()

print("store…", flush=True)
store = SessionStore()
print("start…", flush=True)
s = store.start(
    "# Spec\n\n## Actors\n- Clerk\n\n## In scope (v1)\n- receive\n\n"
    "## Out of scope\n- accounting\n\n## Critical invariants\n- no silent stock loss\n\n"
    "## Success criteria\n- clerks can adjust counts\n\n## Assumptions & risks\n- single tenant\n"
)
print("started", s.phase, s.ready_for_design, flush=True)
for ans in [
    "Clerks and manager; keep accurate stock counts.",
    "Never lose adjustments; audit everything. V1 receive/adjust/report. Out: purchasing.",
]:
    if s.ready_for_design:
        break
    s = store.chat(s.session_id, ans)
    print("chat", s.ready_for_design, len(s.messages), flush=True)

print("approve…", flush=True)
s = store.approve(s.session_id)
print("approve", s.phase, flush=True)
print("d1", repr(s.design_diagram[:100]), flush=True)
d1 = s.design_diagram

print("revise…", flush=True)
s = store.chat(
    s.session_id,
    "Please switch to a modular monolith and drop notifications.",
)
print("d2", repr(s.design_diagram[:140]), flush=True)
print("changed", s.design_diagram != d1, flush=True)
if s.design_diagram == d1:
    sys.exit("diagram did not change")
if "Monolith" not in s.design_diagram and "monolith" not in s.design_diagram.lower():
    sys.exit("expected monolith in diagram")
if "Notify" in s.design_diagram:
    sys.exit("expected Notify removed")
print("OK", flush=True)
