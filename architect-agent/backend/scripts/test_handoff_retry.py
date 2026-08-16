#!/usr/bin/env python3
"""Smoke: failed/queued handoff can be retried without bumping design version."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

os.environ["LLM_PROVIDER"] = "stub"
os.environ["ORCHESTRATOR_AGENT_URL"] = ""
os.environ.pop("SYSTEM_MANAGER_AGENT_URL", None)

tmp = Path(tempfile.mkdtemp(prefix="architect-handoff-retry-"))
os.environ["DATA_DIR"] = str(tmp / "sessions")

from architect_agent.a2a.orchestrator import HandoffResult, strip_handoff_header
from architect_agent.config import get_settings
from architect_agent.graph import reset_graph
from architect_agent.llm import get_chat_model
from architect_agent.sessions import DesignSession, SessionStore, _now

get_settings.cache_clear()
get_chat_model.cache_clear()
reset_graph()

headered = (
    "<!-- architect-agent handoff id=abc session=aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee "
    "version=5 at=2026-08-16T00:00:00+00:00 -->\n\n"
    "# System Design Package\n\nBody here.\n"
)
assert strip_handoff_header(headered).startswith("# System Design Package")

store = SessionStore()
session_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
pkg = tmp / "v5.md"
pkg.write_text(headered, encoding="utf-8")
session = DesignSession(
    session_id=session_id,
    created_at=_now(),
    updated_at=_now(),
    phase="hld",
    design_track="hld",
    design_step=4,
    business_spec="# spec",
    design_version=5,
    last_handoff={
        "status": "failed",
        "handoff_id": "old",
        "path": str(pkg),
        "target_url": "http://127.0.0.1:8090",
        "detail": "A2A delivery to Orchestrator failed: Client Request timed out",
        "at": _now(),
    },
)
store._sessions[session_id] = session
store._ensure_graph_resumable = lambda _s: None  # type: ignore[method-assign]
store._persist(session)

pub = session.to_public()
assert pub["can_retry_handoff"] is True, pub
assert pub["design_version"] == 5

calls: list[int] = []
orig = __import__("architect_agent.sessions", fromlist=["send_design_package"])


def fake_send(*, session_id: str, markdown: str, version: int) -> HandoffResult:
    calls.append(version)
    assert "architect-agent handoff" not in markdown[:80]
    assert "# System Design Package" in markdown
    return HandoffResult(
        status="sent",
        handoff_id="retry-1",
        path=str(pkg),
        target_url="http://127.0.0.1:8090",
        detail="ok",
        at=_now(),
    )


import architect_agent.sessions as sessions_mod

sessions_mod.send_design_package = fake_send  # type: ignore[assignment]
sessions_mod.retry_design_package = (  # type: ignore[assignment]
    lambda **kwargs: fake_send(
        session_id=kwargs["session_id"],
        markdown=strip_handoff_header(Path(kwargs["saved_path"]).read_text(encoding="utf-8"))
        if kwargs.get("saved_path")
        else kwargs.get("markdown") or "",
        version=kwargs["version"],
    )
)

updated = store.retry_orchestrator_handoff(session_id)
assert updated.design_version == 5, updated.design_version
assert updated.last_handoff and updated.last_handoff["status"] == "sent"
assert calls == [5], calls
assert updated.to_public()["can_retry_handoff"] is False
print("OK")
