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
        name="Principal Architect Design",
        description=(
            "Accept a business-specification markdown, classify LLD vs HLD "
            "(Principal Architect), run the structured design track with a "
            "trade-off ledger, re-evaluate the market on every design-version "
            "approve, then hand off the design package to the System Manager."
        ),
        tags=["architecture", "design", "principal-architect", "software-factory"],
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
            "Software Factory Principal Architect. "
            "Starts design sessions from markdown specs via A2A / HTTP "
            "(Phase 0 → LLD/HLD steps → market eval on design approve)."
        ),
        version="0.2.0",
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
