from __future__ import annotations

from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    AgentSkill,
)

from architect_agent.config import get_settings


def build_agent_card() -> AgentCard:
    settings = get_settings()
    base = settings.public_base_url.rstrip("/")
    skill = AgentSkill(
        id="system_design",
        name="System Design",
        description=(
            "Accept a business-specification markdown (or WIP design markdown), "
            "run a grill-me interview until the spec is ready, then propose and "
            "finalize a system design diagram with component justifications."
        ),
        tags=["architecture", "design", "grill-me", "software-factory"],
        examples=[
            "Design a warehouse inventory tracker from this business spec…",
            "Continue this WIP architecture markdown…",
        ],
        input_modes=["text/markdown", "text/plain"],
        output_modes=["text/markdown", "text/plain", "application/json"],
    )
    return AgentCard(
        name="Architect Agent",
        description=(
            "Software Factory architect (merged BA+Architect). "
            "Starts design sessions from markdown specs via A2A / HTTP."
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
