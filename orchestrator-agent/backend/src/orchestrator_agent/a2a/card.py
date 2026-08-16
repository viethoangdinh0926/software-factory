from __future__ import annotations

from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    AgentSkill,
)

from orchestrator_agent.config import get_settings


def build_agent_card() -> AgentCard:
    settings = get_settings()
    base = settings.public_base_url.rstrip("/")
    skill = AgentSkill(
        id="orchestrate_delivery",
        name="Orchestrator Planning",
        description=(
            "Accept an architect design-package markdown keyed by design_session_id. "
            "Classify stand-alone vs distributed, plan tech stacks (and per-service APIs), "
            "then hand plan specs to the Engineer agent."
        ),
        tags=["orchestration", "planning", "software-factory"],
        examples=[
            "Here is System Design Package v1 for design session …",
        ],
        input_modes=["text/markdown", "text/plain"],
        output_modes=["text/markdown", "text/plain", "application/json"],
    )
    return AgentCard(
        name="Orchestrator Agent",
        description=(
            "Software Factory Orchestrator. Receives architect design packages via A2A, "
            "plans delivery with a human on a shared UI, and queues work for the Engineer."
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
