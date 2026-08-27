#!/usr/bin/env python3
"""Smoke: Phase 0 → HLD steps → design approve → market → handoff → new round at Phase 0.
Also covers a short LLD path."""
from __future__ import annotations

import inspect
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
    _TURN_INTENT_SYSTEM,
    classify_user_message,
    format_classify_context,
    resolve_wait_action,
    workflow_action,
    is_accept_recommendation_message,
    is_advance_request,
    is_informational_query,
    is_revision_request,
    is_step_approval_message,
    looks_like_help_answering,
    promote_chat_to_approve,
    with_next_prompt,
    with_resolution_close,
    without_user_echo,
)
from architect_agent.json_util import coerce_artifact_markdown, parse_llm_json_object
from architect_agent.ascii_text import fold_to_ascii, with_ascii_instruction
from architect_agent.workflow import architect_workflow, package_from_workflow
from architect_agent.graph.nodes.common import assistant_message_is_thin, ensure_step_briefing
from architect_agent.graph.nodes import common as architect_common
from architect_agent.graph.nodes import phase0 as architect_phase0
from architect_agent.design_diagram import (
    apply_diagram_catalogs,
    catalog_covers_diagram,
    diagram_is_due,
    diagram_node_count,
    ensure_component_catalog,
    ensure_design_diagram,
    extract_diagram_edges,
    extract_spec_section,
    fallback_relationship_catalog,
    relationships_cover_diagram,
    upsert_spec_section,
    with_component_walkthrough,
    with_relationship_walkthrough,
)
from architect_agent.graph.state import _keep_nonempty_str, _merge_spec
from architect_agent.design_progress import classify_rewind_stage, design_position, package_fingerprint
from architect_agent.context_budget import (
    PRINCIPAL_ARCHITECT_DIGEST,
    TRACK_CLASSIFICATION_RULES,
    consult_user_turn,
)
from architect_agent.interview_progress import (
    accepts_recommended_default,
    append_spec_bullet,
    apply_skipped_question,
    cannot_answer_current_question,
    extract_recommended_default,
    heading_for_turn,
    hydrate_spec_from_transcript,
    is_interview_control_phrase,
    is_pain_opportunity_question,
    living_spec_scaffold,
    lock_open_answer_into_spec,
    scrub_control_phrases_from_spec,
    spec_still_scaffold,
    spec_substance,
    user_requests_ready,
    user_skips_current_question,
)
from architect_agent.graph.nodes.hld import (
    domain_model_is_concrete,
    ensure_domain_model,
    fallback_domain_model,
    fallback_fmea_notes,
    fmea_notes_are_concrete,
    listed_domain_entities,
)
from architect_agent.scope import (
    ensure_classified_topology,
    recommend_design_track,
    resolve_design_track,
    wants_standalone,
)
from architect_agent.sessions import DesignSession, SessionStore, _legacy_map

get_settings.cache_clear()
get_chat_model.cache_clear()
reset_graph()

assert classify_user_message("Approve") == ("command", "approve")
assert classify_user_message("I'm happy with this, let's continue") == ("command", "approve")
assert "copy the recommended text into the spec" in _TURN_INTENT_SYSTEM
assert "NOT approve" in _TURN_INTENT_SYSTEM
assert "Last assistant message:" in format_classify_context("wf", "pick Content Discovery")
from architect_agent import query_intent as architect_intent
assert "BOTH messages" in inspect.getsource(architect_intent._llm_turn_intent)
assert "Latest user message:" in inspect.getsource(architect_intent._llm_turn_intent)
assert workflow_action("revise") == "revise"
assert workflow_action("answer") == "answer"
assert workflow_action("none") == "chat"
assert resolve_wait_action("revise", "add a health check") == "revise"
assert resolve_wait_action("answer", "Why is this HLD?") == "answer"
rec_ctx = (
    "Recommendation: For a platform aiming to compete with YouTube, the most "
    "impactful starting point is usually the Content Discovery & Personalization pain."
)
assert classify_user_message("as you recommended", rec_ctx) == ("command", "revise")
assert classify_user_message("as you recommended", rec_ctx) != ("command", "approve")
assert is_accept_recommendation_message("as you recommended", rec_ctx)
assert not is_step_approval_message("as you recommended")
assert classify_user_message("Why is this HLD rather than LLD?") == ("information", "answer")
assert classify_user_message("Show me all URL endpoints we agreed on") == ("information", "answer")
assert classify_user_message("Add a health check endpoint") == ("command", "revise")
# Example phrasings of "help me answer your questions" — not a keyword list.
assert classify_user_message("Give me some potential answers") == ("information", "answer")
assert classify_user_message("What would you pick for those?") == ("information", "answer")
assert classify_user_message("Can you draft replies I could use?") == ("information", "answer")
assert looks_like_help_answering("can you suggest a response?")
assert classify_user_message("can you suggest a response?") == ("information", "answer")
assert user_skips_current_question("skip this question please")
assert accepts_recommended_default("as you recommended")
assert cannot_answer_current_question("i don't have any concrete role to provide")
assert "Content Discovery" in extract_recommended_default(
    "Recommendation: For a platform aiming to compete with YouTube, the most "
    "impactful starting point is usually the Content Discovery & Personalization pain."
)
assert "End-User" in extract_recommended_default(
    "Recommendation: For initial design, focusing on the end-user experience is "
    "paramount. Therefore, we recommend prioritizing the End-User as the primary focus."
)
yt_scaffold = living_spec_scaffold("design a platform similar to YouTube")
assert spec_still_scaffold(yt_scaffold)
yt_problem_q = (
    "❓ **Problem / opportunity**: What concrete pain or opportunity makes this worth building now?\n\n"
    "➡️ (Recommended) One paragraph naming who hurts today and what fails without this system."
)
yt_rec = (
    "Here are three concrete ways we can frame this problem.\n\n"
    "Recommendation: For a platform aiming to compete with YouTube, the most "
    "impactful starting point is usually the Content Discovery & Personalization pain."
)
yt_actors_q = (
    "❓ **Primary actors**: Who uses this system day-to-day, and what job are they hiring it to do?\n\n"
    "➡️ (Recommended) Name 1–2 concrete roles with a single primary job each."
)
yt_actor_rec = (
    "Content Consumer (Primary Actor): A global user who consumes video content.\n\n"
    "Recommendation: For initial design, focusing on the end-user experience is "
    "paramount. Therefore, we recommend prioritizing the End-User as the primary focus."
)
yt_spec = lock_open_answer_into_spec(
    yt_scaffold,
    yt_problem_q,
    "This platform aims to serve people around the world with the video sharing "
    "demand that dominated by YouTube.",
)
yt_spec = lock_open_answer_into_spec(yt_spec, yt_rec, "as you recommended")
yt_spec = lock_open_answer_into_spec(
    yt_spec, yt_actors_q, "people around the world is target users."
)
yt_spec = lock_open_answer_into_spec(
    yt_spec, yt_actor_rec, "i don't have any concrete role to provide"
)
yt_spec = lock_open_answer_into_spec(yt_spec, yt_actor_rec, "looks good to me.")
assert "serve people around the world" in yt_spec
assert "Content Discovery" in yt_spec
assert "End-User" in yt_spec
assert "people around the world is target users" in yt_spec
assert "as you recommended" not in yt_spec
assert "looks good to me" not in yt_spec
hydrated = hydrate_spec_from_transcript(
    yt_scaffold,
    [
        {"role": "assistant", "content": yt_problem_q, "node": "phase0"},
        {
            "role": "user",
            "content": (
                "This platform aims to serve people around the world with the "
                "video sharing demand that dominated by YouTube."
            ),
            "node": "phase0",
        },
        {"role": "assistant", "content": yt_rec, "node": "phase0"},
        {"role": "user", "content": "as you recommended", "node": "phase0"},
        {"role": "assistant", "content": yt_actors_q, "node": "phase0"},
        {
            "role": "user",
            "content": "people around the world is target users.",
            "node": "phase0",
        },
        {"role": "assistant", "content": yt_actor_rec, "node": "phase0"},
        {"role": "user", "content": "looks good to me.", "node": "phase0"},
    ],
)
assert "Content Discovery" in hydrated
assert "End-User" in hydrated
assert spec_substance(hydrated) > spec_substance(yt_scaffold)
assert "Distributed" in ensure_classified_topology(yt_scaffold, "hld")
assert "to be classified after discovery" not in ensure_classified_topology(
    yt_scaffold, "hld"
).lower()
consult_accept = consult_user_turn(
    pending="as you recommended",
    last_assistant=yt_rec,
    keynotes="",
    phase="phase0",
)
assert not consult_accept.needs_clarification, consult_accept
assert consult_accept.kind in {"answer", "complement"}
assert is_interview_control_phrase("skip this question please")
assert is_interview_control_phrase("that's all I have for now")
assert is_interview_control_phrase("can you suggest a response?")
assert "skip this question please" not in append_spec_bullet(
    living_spec_scaffold("Public global video platform."),
    "## Problem",
    "skip this question please",
)
leaked = (
    "# Business Specification\n\n"
    "## Problem\n"
    "design a video sharing platform similar to YouTube.\n\n"
    "- skip this question please\n"
)
assert "skip this question please" not in scrub_control_phrases_from_spec(leaked)
assert "skip this question please" not in architect_phase0._fold_decision_into_spec(
    living_spec_scaffold("design a video sharing platform similar to YouTube."),
    "skip this question please",
    "❓ **Problem / opportunity**: What concrete pain?",
)
assert not user_skips_current_question("that's all I have for now")
assert user_requests_ready("that's all I have for now")
assert classify_user_message("skip this question please") == ("command", "revise")
assert classify_user_message("that's all I have for now") == ("command", "approve")
assert is_pain_opportunity_question(
    "❓ **Problem / opportunity**: What concrete pain or opportunity makes this worth building now?"
)
consult_skip = consult_user_turn(
    pending="skip this question please",
    last_assistant=(
        "❓ **Problem / opportunity**: What concrete pain makes this worth building now?\n"
        "➡️ (Recommended) One paragraph naming who hurts today."
    ),
    keynotes="",
    phase="phase0",
)
assert not consult_skip.needs_clarification, consult_skip
consult_help = consult_user_turn(
    pending="can you suggest a response?",
    last_assistant="❓ **Problem / opportunity**: What concrete pain makes this worth building?",
    keynotes="",
    phase="phase0",
)
assert not consult_help.needs_clarification, consult_help
consult_done = consult_user_turn(
    pending="that's all I have for now",
    last_assistant="Please provide a concrete pain point or opportunity.",
    keynotes="",
    phase="phase0",
)
assert not consult_done.needs_clarification, consult_done
skipped_spec = apply_skipped_question(
    living_spec_scaffold("Public global video platform competing with YouTube."),
    "❓ **Problem / opportunity**: What concrete pain?\n➡️ (Recommended) Creators need a public place to publish videos.",
)
assert "Creators need a public place" in skipped_spec
assert heading_for_turn(
    "❓ **Problem / opportunity**: What concrete pain?",
    "This system is to compete with YouTube on the global market of video sharing.",
) == "## Problem"
assert heading_for_turn(
    "❓ **V1 scope**: What 3 capabilities must ship in v1?",
    "Users register, login, search, watch, and upload videos.",
) == "## In scope (v1)"
live_spec = living_spec_scaffold("describe a system similar to YouTube")
live_spec = append_spec_bullet(
    live_spec,
    heading_for_turn(
        "❓ **Problem / opportunity**: What concrete pain?",
        "This system is to compete with YouTube on the global market of video sharing.",
    ),
    "This system is to compete with YouTube on the global market of video sharing.",
)
assert "compete with YouTube" in live_spec
assert spec_substance(live_spec) > spec_substance(
    living_spec_scaffold("describe a system similar to YouTube")
)
assert "compete with YouTube" in _merge_spec(
    live_spec, living_spec_scaffold("describe a system similar to YouTube")
)
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
assert "video sharing platform" in without_user_echo(
    "- The **video sharing platform** stores uploads.\n- CDN delivers playback.",
    "video sharing platform",
)
assert "I heard you:" not in without_user_echo("I heard you: GDPR please.\nNext question?", "GDPR please")
assert NEXT_PROMPT_HEADER not in with_next_prompt("Here is the proposal.")
assert "click" not in without_user_echo(
    "Scope looks like **HLD**. Click **Approve** to start that track."
).lower()
assert "please approve" in without_user_echo(
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
assert (
    recommend_design_track(
        spec="Compete with YouTube on the global market of video sharing."
    )
    == "hld"
)
assert (
    resolve_design_track(
        "lld",
        pending="looks good to me",
        spec="Users register, login, search, watch, and upload videos.",
        prior="unset",
        context="Compete with YouTube. Unequivocally High-Level Design. CDN and Kafka.",
    )
    == "hld"
)
assert "FACTORY LLD / HLD DEFINITIONS" in TRACK_CLASSIFICATION_RULES
assert "mutually exclusive" in TRACK_CLASSIFICATION_RULES
assert "does not precede" in TRACK_CLASSIFICATION_RULES
assert "object-oriented" in TRACK_CLASSIFICATION_RULES
assert "API gateway" in TRACK_CLASSIFICATION_RULES
assert TRACK_CLASSIFICATION_RULES in PRINCIPAL_ARCHITECT_DIGEST
assert "TRACK_CLASSIFICATION_RULES" in inspect.getsource(architect_common.answer_open_query)
assert "TRACK_CLASSIFICATION_RULES" in inspect.getsource(architect_phase0._compile_phase0_spec)
assert "TRACK_CLASSIFICATION_RULES" in inspect.getsource(architect_phase0._fold_decision_into_spec)
assert "PRINCIPAL_ARCHITECT_DIGEST" in inspect.getsource(architect_phase0.phase0_classify_node)
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
scale_md = coerce_artifact_markdown(
    {
        "daily_active_users": "100,000",
        "peak_concurrent_streams": "50,000",
        "video_ingestion_rate_gb_per_hour": "100",
    }
)
assert "- **Daily active users:** 100,000" in scale_md
assert "peak_concurrent_streams" not in scale_md
assert coerce_artifact_markdown(
    "{'daily_active_users': '100,000', 'peak_concurrent_streams': '50,000'}"
).startswith("- **Daily active users:**")
scale_brief = ensure_step_briefing(
    "HLD step 1 update.",
    track="hld",
    step=1,
    title="Requirements & capacity estimation",
    artifacts={"scale_estimates": "{'daily_active_users': '100,000'}"},
    primary_field="scale_estimates",
)
assert "{'daily_active_users'" not in scale_brief
assert "**Daily active users:**" in scale_brief
empty_hld_spec = living_spec_scaffold("design a video sharing platform similar to YouTube.")
assert not domain_model_is_concrete(empty_hld_spec)
video_domain = fallback_domain_model(empty_hld_spec)
assert listed_domain_entities(video_domain)[:3] == ["User", "Channel", "Video"]
assert domain_model_is_concrete(f"## Domain model\n\n{video_domain}")
modeled = ensure_domain_model(empty_hld_spec)
assert extract_spec_section(modeled, "Domain model")
assert "### Video" in modeled
assert "(to be captured)" in modeled
domain_brief = ensure_step_briefing(
    "HLD step 2 update.",
    track="hld",
    step=2,
    title="Domain object modeling",
    artifacts={"business_spec": modeled},
    primary_field="business_spec",
)
assert "## Domain model" in domain_brief
assert "### Video" in domain_brief
assert "(to be captured)" not in domain_brief
assert "skip this question please" not in domain_brief
scaffold_leak_brief = ensure_step_briefing(
    "HLD step 2 — Domain object modeling is complete enough to review.\n\n"
    "Here is what this step locked in:\n\n"
    f"{empty_hld_spec}\n- skip this question please\n",
    track="hld",
    step=2,
    title="Domain object modeling",
    artifacts={"business_spec": modeled},
    primary_field="business_spec",
)
assert "### Video" in scaffold_leak_brief
assert "(to be captured)" not in scaffold_leak_brief
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

assert fmea_notes_are_concrete(fallback_fmea_notes())
empty_fmea_brief = ensure_step_briefing(
    "HLD step 5 update.",
    track="hld",
    step=5,
    title="Vulnerability & edge-case analysis (FMEA)",
    artifacts={
        "scale_estimates": (
            "Daily active users: 100 million\n"
            "Peak concurrent streams: 50 million\n"
        ),
        "fmea_notes": "",
    },
    primary_field="fmea_notes",
)
assert "100 million" not in empty_fmea_brief
assert "not ready to review" in empty_fmea_brief.lower()
wrong_step5 = (
    "**HLD step 5 — Vulnerability & edge-case analysis (FMEA)** is complete enough to review.\n\n"
    "Here is what this step locked in:\n\n"
    "Daily active users: 100 million\n"
    "Peak concurrent streams: 50 million\n"
    "Ingestion rate streams: 10 million per minute\n"
    "Storage growth rate: 20% per quarter\n\n"
    "Next we synthesize the session and wrap up.\n\n"
    "Here is what each box on the system design diagram is responsible for.\n\n"
    "Diagram components\n"
    "Clients (Web/Mobile)\n"
    "This node represents the external consumers of the platform, such as web "
    "browsers or mobile applications. It exists to initiate requests to the "
    "backend services. It talks directly to the API Gateway.\n"
)
fmea_brief = ensure_step_briefing(
    wrong_step5,
    track="hld",
    step=5,
    title="Vulnerability & edge-case analysis (FMEA)",
    artifacts={
        "scale_estimates": "Daily active users: 100 million",
        "fmea_notes": fallback_fmea_notes(),
    },
    primary_field="fmea_notes",
)
assert "Failure mode" in fmea_brief
assert "SPOF" in fmea_brief
assert "Mitigation" in fmea_brief
assert "each box on the system design diagram" not in fmea_brief.lower()
assert "100 million" not in fmea_brief
assert "Clients (Web/Mobile)" not in fmea_brief

latex_json = r'{"assistant_message": "DAU $\text{assumed}$ is $\approx$ 5k"}'
latex_msg = parse_llm_json_object(latex_json)["assistant_message"]
assert "\t" not in latex_msg, repr(latex_msg)
assert r"\text" in latex_msg
assert r"\approx" in latex_msg

print("HLD path…", flush=True)
store = SessionStore()
broken_fmea = DesignSession(
    session_id="fmea-repair",
    created_at="2026-01-01T00:00:00+00:00",
    updated_at="2026-01-01T00:00:00+00:00",
    phase="hld",
    design_track="hld",
    design_step=5,
    scale_estimates="Daily active users: 100 million",
    fmea_notes="",
    messages=[{"role": "assistant", "content": wrong_step5, "node": "hld"}],
)
store._sessions[broken_fmea.session_id] = broken_fmea
store._fill_missing_fmea(broken_fmea)
assert fmea_notes_are_concrete(broken_fmea.fmea_notes)
assert "SPOF" in broken_fmea.messages[-1]["content"]
assert "100 million" not in broken_fmea.messages[-1]["content"]
assert "each box on the system design diagram" not in broken_fmea.messages[-1]["content"].lower()
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
assert "Core Microservices" in pkg
assert "Communication Schemes" in pkg
assert "## API Contracts" not in pkg
wf_after = architect_workflow(s)
assert wf_after["id"] == "phase0", wf_after
hld3 = next(t for t in wf_after["tiles"] if t["id"] == "hld3")
assert hld3["body"].strip(), hld3
assert "current" in {t["status"] for t in wf_after["tiles"] if t["id"] == "phase0"}

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
assert "please approve" in compiled_msg.lower(), compiled_msg[:400]
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
assert "please approve" in classified_msg.lower(), classified_msg[:400]
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
section = extract_spec_section(spec_with, "Diagram components")
assert section, spec_with
assert "Desktop UI" in section and "Application Shell" in section and "Remote API" in section
assert spec_with.count("## Diagram components") == 1
walked = with_component_walkthrough("LLD step 2 is ready.", catalog)
assert "Desktop UI" in walked and "Diagram components" in walked
assert with_component_walkthrough(walked, catalog) == walked

labeled = (
    "flowchart LR\n"
    "  UI[Desktop UI] -->|compose| App[Application Shell]\n"
    "  App -->|HTTPS| Remote[Remote API]\n"
)
edges = extract_diagram_edges(labeled)
assert ("UI", "App", "compose") in edges, edges
assert ("App", "Remote", "HTTPS") in edges, edges
rel = fallback_relationship_catalog(labeled, "Local desktop client.", "")
assert "## Diagram relationships" in rel
assert "### UI → App" in rel
assert "### App → Remote" in rel
assert relationships_cover_diagram(rel, labeled)
rel_walk = with_relationship_walkthrough("Blueprint is ready.", rel)
assert "connecting line" in rel_walk.lower() and "UI → App" in rel_walk
assert with_relationship_walkthrough(rel_walk, rel) == rel_walk
spec_rel, _comps, rels_out = apply_diagram_catalogs(labeled, "# Spec\n\n## Goals\n- ship\n", allow_llm=False)
assert extract_spec_section(spec_rel, "Diagram relationships")
assert "### UI → App" in rels_out

print("legacy map…", flush=True)
mapped = _legacy_map({"phase": "spec_interview"})
assert mapped["phase"] == "phase0"
mapped = _legacy_map({"phase": "system_design"})
assert mapped["phase"] == "hld" and mapped["design_step"] == 4

print("ASCII fold and workflow tiles…", flush=True)
assert fold_to_ascii("smart “quotes” and an em—dash → next") == 'smart "quotes" and an em-dash -> next'
assert "CHARACTER SET" in with_ascii_instruction("You are a test.")
wf = architect_workflow(s)
assert wf["id"]
assert wf["tiles"]
assert any(t["id"] == "phase0" for t in wf["tiles"])
pkg2 = package_from_workflow(s)
assert "Phase 0" in pkg2
assert all(ord(ch) < 128 or ch in "\n\r\t" for ch in pkg2)

print("OK", flush=True)
