from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_ROOT = Path(__file__).resolve().parents[2]
AGENT_ROOT = BACKEND_ROOT.parent
STATIC_DIR = Path(__file__).resolve().parent / "static"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(str(AGENT_ROOT / ".env"), str(BACKEND_ROOT / ".env")),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    host: str = "127.0.0.1"
    port: int = 8090
    public_base_url: str = "http://127.0.0.1:8090"
    ssl_verify: bool = True

    llm_provider: Literal["openai", "anthropic", "ollama", "stub"] = "stub"
    llm_model: str = "gpt-4o-mini"
    openai_api_key: str | None = None
    anthropic_api_key: str | None = None
    ollama_base_url: str = "http://127.0.0.1:11434"
    llm_temperature: float = 0.3

    reallm_base_url: str | None = None
    reallm_api_key: str | None = None

    aia_gateway_client_id: str | None = None
    aia_gateway_client_secret: str | None = None
    aia_gateway_base_url: str | None = None

    data_dir: Path = BACKEND_ROOT / "data" / "sessions"
    skill_path: Path = AGENT_ROOT / "skills" / "orchestrator" / "SKILL.md"
    static_dir: Path = STATIC_DIR

    context_history_max_tokens: int = 1800
    context_history_max_turns: int = 12
    context_digest_soft_tokens: int = 900
    context_digest_hard_tokens: int = 1400
    context_digest_compact_target_tokens: int = 700

    engineer_agent_url: str | None = None

    # Live `git ls-remote` with the session SSH key before sending git data to the engineer.
    git_verify_enabled: bool = True

    # Enable/disable web search for feature research
    web_search_enabled: bool = True


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    return settings
