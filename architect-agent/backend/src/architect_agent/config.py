from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/src/architect_agent/config.py → backend / architect-agent
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
    port: int = 8080
    public_base_url: str = "http://127.0.0.1:8080"

    # openai | anthropic | ollama | stub
    llm_provider: Literal["openai", "anthropic", "ollama", "stub"] = "stub"
    llm_model: str = "gpt-4o-mini"
    openai_api_key: str | None = None
    anthropic_api_key: str | None = None
    ollama_base_url: str = "http://127.0.0.1:11434"
    llm_temperature: float = 0.3

    # Dell RealLM Proxy
    reallm_base_url: str | None = None
    reallm_api_key: str | None = None

    # Dell AIA Gateway
    aia_gateway_client_id: str | None = None
    aia_gateway_client_secret: str | None = None
    aia_gateway_base_url: str | None = None


    data_dir: Path = BACKEND_ROOT / "data" / "sessions"
    grill_me_skill_path: Path = AGENT_ROOT / "skills" / "grill-me" / "SKILL.md"
    static_dir: Path = STATIC_DIR

    # Downstream A2A peer (Software System Manager) — optional until that agent exists
    system_manager_agent_url: str | None = None


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    return settings
