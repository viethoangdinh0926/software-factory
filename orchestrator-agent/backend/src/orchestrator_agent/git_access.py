from __future__ import annotations

import hashlib
import logging
import os
import re
import stat
import subprocess
import tempfile
from pathlib import Path

from orchestrator_agent.config import get_settings

logger = logging.getLogger(__name__)

_SSH_URL_RE = re.compile(
    r"^(?:git@[\w.\-]+:[\w.\-/@~]+(?:\.git)?|ssh://[\w.\-@]+(?::\d+)?/[\w.\-/@~]+(?:\.git)?)$"
)
_KEY_BEGIN_RE = re.compile(
    r"-----BEGIN (?:OPENSSH |RSA |EC |DSA |ENCRYPTED )?PRIVATE KEY-----"
)
_KEY_END_RE = re.compile(
    r"-----END (?:OPENSSH |RSA |EC |DSA |ENCRYPTED )?PRIVATE KEY-----"
)


class GitAccessError(Exception):
    """User-facing git access failure. Never include key material."""

    def __init__(self, user_message: str) -> None:
        super().__init__(user_message)
        self.user_message = user_message


def normalize_repo_url(url: str) -> str:
    return (url or "").strip()


def normalize_private_key(key: str) -> str:
    text = (key or "").replace("\r\n", "\n").strip()
    if text and not text.endswith("\n"):
        text += "\n"
    return text


def key_fingerprint(key: str) -> str:
    body = normalize_private_key(key)
    if not body:
        return ""
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
    return digest[:16]


def validate_repo_url(url: str) -> str:
    cleaned = normalize_repo_url(url)
    if not cleaned:
        raise GitAccessError("Enter a git repo URL (SSH), for example git@github.com:org/repo.git.")
    if cleaned.startswith(("http://", "https://")):
        raise GitAccessError("Use an SSH git URL with the private key, not HTTPS.")
    if not _SSH_URL_RE.match(cleaned):
        raise GitAccessError(
            "That git URL does not look like an SSH remote "
            "(git@host:path.git or ssh://git@host/path.git)."
        )
    return cleaned


def validate_private_key(key: str) -> str:
    cleaned = normalize_private_key(key)
    if not cleaned:
        raise GitAccessError("Paste an SSH private key for this repo.")
    if not _KEY_BEGIN_RE.search(cleaned) or not _KEY_END_RE.search(cleaned):
        raise GitAccessError(
            "That does not look like an SSH private key. Paste the full PEM / OpenSSH key block."
        )
    return cleaned


def verify_repo_access(url: str, ssh_private_key: str) -> None:
    """Validate URL/key, then optionally `git ls-remote` with that key."""
    cleaned_url = validate_repo_url(url)
    cleaned_key = validate_private_key(ssh_private_key)
    settings = get_settings()
    if not settings.git_verify_enabled:
        logger.info("Skipping live git ls-remote (GIT_VERIFY_ENABLED=false)")
        return
    _live_ls_remote(cleaned_url, cleaned_key)


def _live_ls_remote(url: str, ssh_private_key: str) -> None:
    key_path: Path | None = None
    try:
        handle = tempfile.NamedTemporaryFile("w", delete=False, prefix="sf-git-key-", suffix=".pem")
        key_path = Path(handle.name)
        handle.write(ssh_private_key)
        handle.close()
        os.chmod(key_path, stat.S_IRUSR | stat.S_IWUSR)
        ssh_cmd = (
            f"ssh -i {key_path} -o IdentitiesOnly=yes -o BatchMode=yes "
            f"-o StrictHostKeyChecking=accept-new -o UserKnownHostsFile=/dev/null"
        )
        result = subprocess.run(
            ["git", "ls-remote", "--heads", url],
            check=False,
            capture_output=True,
            text=True,
            timeout=25,
            env={**os.environ, "GIT_SSH_COMMAND": ssh_cmd, "GIT_TERMINAL_PROMPT": "0"},
        )
    except FileNotFoundError:
        raise GitAccessError("git is not installed on the orchestrator host, so the repo cannot be verified.") from None
    except subprocess.TimeoutExpired:
        raise GitAccessError("Timed out contacting the git repo. Check the URL, key, and network.") from None
    except OSError:
        logger.exception("git ls-remote failed to start")
        raise GitAccessError("Could not run git to verify repo access.") from None
    finally:
        if key_path is not None:
            try:
                key_path.unlink(missing_ok=True)
            except OSError:
                logger.warning("Failed to delete temporary git key file")
    if result.returncode != 0:
        err = (result.stderr or result.stdout or "").strip()
        logger.info("git ls-remote failed for a session repo (stderr omitted if it may leak paths)")
        raise GitAccessError(_user_git_error(err))


def _user_git_error(stderr: str) -> str:
    lower = (stderr or "").lower()
    if "permission denied" in lower or "publickey" in lower or "authentication" in lower:
        return "Could not authenticate to the git repo with that SSH key."
    if "could not resolve" in lower or "name or service not known" in lower:
        return "Could not resolve the git host. Check the repo URL."
    if "repository not found" in lower or "does not appear to be a git" in lower:
        return "The remote is not a git repository, or this key cannot see it."
    if "connection refused" in lower or "timed out" in lower or "network" in lower:
        return "Could not reach the git host. Check the URL and network."
    return "Could not access the git repo with the provided SSH key."
