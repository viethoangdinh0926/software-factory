from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from orchestrator_agent.config import get_settings

logger = logging.getLogger(__name__)


def secrets_dir() -> Path:
    path = get_settings().data_dir.parent / "secrets"
    path.mkdir(parents=True, exist_ok=True)
    return path


def secrets_path(session_id: str) -> Path:
    return secrets_dir() / f"{session_id}.json"


def load_git_secrets(session_id: str) -> dict[str, str]:
    path = secrets_path(session_id)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        logger.exception("Failed to read git secrets for session %s", session_id)
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        "git_repo_url": str(data.get("git_repo_url") or ""),
        "ssh_private_key": str(data.get("ssh_private_key") or ""),
    }


def save_git_secrets(session_id: str, *, git_repo_url: str, ssh_private_key: str) -> None:
    path = secrets_path(session_id)
    payload: dict[str, Any] = {
        "git_repo_url": git_repo_url,
        "ssh_private_key": ssh_private_key,
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    os.chmod(path, 0o600)
