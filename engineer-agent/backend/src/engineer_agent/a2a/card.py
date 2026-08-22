from __future__ import annotations

from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    AgentSkill,
)

from engineer_agent.config import get_settings


def build_agent_card() -> AgentCard:
    settings = get_settings()
    base = settings.public_base_url.rstrip("/")
    skill = AgentSkill(
        id="engineer_fleet",
        name="Engineer Fleet",
        description=(
            "Accept an orchestrator plan-spec markdown keyed by design_session_id and "
            "microservice_id. Spawn a sub-engineer that drafts an execution plan, owns "
            "the offered API, and consults peer sub-engineers it initiates toward."
        ),
        tags=["engineering", "software-factory", "fleet"],
        examples=[
            "Here is Plan spec for design session … microservice …",
        ],
        input_modes=["text/markdown", "text/plain"],
        output_modes=["text/markdown", "text/plain", "application/json"],
    )
    return AgentCard(
        name="Engineer Agent",
        description=(
            "Software Factory Engineer. Receives plan specs via A2A, runs one sub-engineer "
            "per design_session_id + microservice_id, drafts an execution plan, and owns "
            "each service's offered API."
        ),
        version="0.1.0",
        default_input_modes=["text/markdown", "text/plain"],
        default_output_modes=["text/markdown", "text/plain", "application/json"],
        capabilities=AgentCapabilities(streaming=False),
        skills=[skill],
        supported_interfaces=[
            AgentInterface(
                url=base,
                protocol_binding="JSONRPC",
                protocol_version="1.0",
            ),
        ],
    )
