"""Local git workspace for a sub-engineer: pull, private folder, item files, ship once."""

from __future__ import annotations

import logging
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from engineer_agent.config import get_settings

logger = logging.getLogger(__name__)

_UNSAFE_DIR_RE = re.compile(r"[^A-Za-z0-9._-]+")
_SAFE_ID_RE = _UNSAFE_DIR_RE
_SNAPSHOT_DIR = ".pi-snapshots"
_SNAPSHOT_SKIP = {_SNAPSHOT_DIR, "__pycache__", ".git", "node_modules", ".venv"}


def _slug_dir_name(raw: str) -> str:
    text = (raw or "").strip().replace("\\", "/")
    text = text.split("/")[-1].strip()
    cleaned = _UNSAFE_DIR_RE.sub("-", text)
    cleaned = re.sub(r"-{2,}", "-", cleaned).strip(".-")
    return cleaned[:80]


def private_dir_name(
    microservice_name: str,
    *,
    microservice_id: str = "",
    taken: set[str] | None = None,
) -> str:
    """Repo-root folder for this sub-engineer: service name, sanitized only as needed."""
    base = _slug_dir_name(microservice_name) or _slug_dir_name(microservice_id) or "app"
    occupied = {name.lower() for name in (taken or set())}
    if base.lower() in occupied:
        suffix = re.sub(r"[^A-Za-z0-9]", "", microservice_id)[:8] or "x"
        candidate = f"{base}-{suffix}"[:80]
        n = 2
        while candidate.lower() in occupied:
            candidate = f"{base}-{suffix}-{n}"[:80]
            n += 1
        return candidate
    return base


def repo_root_for(session_id: str) -> Path:
    return get_settings().workspaces_dir / session_id / "repo"


def private_dir_for(
    session_id: str,
    microservice_name: str,
    *,
    microservice_id: str = "",
    taken: set[str] | None = None,
) -> Path:
    return repo_root_for(session_id) / private_dir_name(
        microservice_name, microservice_id=microservice_id, taken=taken
    )


def prepare_workspace(
    *,
    session_id: str,
    microservice_id: str,
    microservice_name: str,
    git_data: dict[str, str] | None,
    taken: set[str] | None = None,
) -> tuple[Path, str]:
    """Pull/clone the fleet repo (when enabled) and create this sub-engineer's private folder.

    Returns (private_dir, error_message). error_message is empty on success.
    """
    root = repo_root_for(session_id)
    root.mkdir(parents=True, exist_ok=True)
    error = ""
    settings = get_settings()
    if settings.git_execute_enabled and git_data and git_data.get("repo_url") and git_data.get("ssh_private_key"):
        try:
            _clone_or_pull(root, git_data["repo_url"], git_data["ssh_private_key"])
        except Exception as exc:  # noqa: BLE001
            logger.exception("git clone/pull failed for fleet %s", session_id)
            error = str(exc) or "Could not pull the git repo."
    elif not settings.git_execute_enabled:
        logger.info("Skipping live git clone/pull (GIT_EXECUTE_ENABLED=false)")
    folder = private_dir_name(microservice_name, microservice_id=microservice_id, taken=taken)
    private = root / folder
    private.mkdir(parents=True, exist_ok=True)
    readme = private / "README.md"
    if not readme.is_file():
        readme.write_text(
            f"# {microservice_name or folder}\n\n"
            "Private workspace for this sub-engineer at the git repo root. "
            "Other sub-engineers use their own service-named directories. "
            "Code is shipped only after the full execution plan completes.\n",
            encoding="utf-8",
        )
    return private, error


def write_item_spec(
    private_dir: Path,
    *,
    item: dict[str, Any],
    microservice_name: str,
    instructions: str = "",
    feature_spec: str = "",
    bug_spec: str = "",
    offered_api: str = "",
    tech_stack: str = "",
) -> Path:
    """Write the item brief Pi (or local stubs) will implement against."""
    item_id = _SAFE_ID_RE.sub("-", str(item.get("id") or "item")) or "item"
    dest = private_dir / "items" / item_id
    dest.mkdir(parents=True, exist_ok=True)
    title = str(item.get("title") or item_id)
    kind = str(item.get("kind") or "feature")
    peers = item.get("peer_services") or []
    contracts = item.get("contracts") or {}
    contract_md = ""
    if isinstance(contracts, dict) and contracts:
        parts = [f"### {name}\n\n{body}\n" for name, body in contracts.items()]
        contract_md = "## Settled communication contracts\n\n" + "\n".join(parts)
    elif peers:
        contract_md = (
            "## Communication contracts\n\n"
            "This item depends on other services, but contracts were not settled yet.\n"
        )
    instr = (instructions or "").strip()
    instr_md = f"## Resume instructions\n\n{instr}\n\n" if instr else ""
    (dest / "README.md").write_text(
        f"# {title}\n\n"
        f"- Kind: `{kind}`\n"
        f"- Service: **{microservice_name}**\n"
        f"- Priority: {item.get('priority')}\n\n"
        f"{instr_md}{contract_md}",
        encoding="utf-8",
    )
    spec_parts = [
        f"# {title}\n",
        f"- Kind: `{kind}`",
        f"- Service: **{microservice_name}**",
        f"- Priority: {item.get('priority')}",
        f"- Item id: `{item_id}`",
        "",
        instr_md.rstrip(),
        contract_md.rstrip(),
        "## Feature spec\n",
        (feature_spec or "").strip() or "(none)",
        "",
        "## Bug spec\n",
        (bug_spec or "").strip() or "(none)",
        "",
        "## Offered API\n",
        (offered_api or "").strip() or "(none)",
        "",
        "## Tech stack\n",
        (tech_stack or "").strip() or "(none)",
        "",
    ]
    (dest / "SPEC.md").write_text("\n".join(part for part in spec_parts if part is not None), encoding="utf-8")
    return dest


def write_item_stubs(
    dest: Path,
    *,
    item: dict[str, Any],
    microservice_name: str,
) -> Path:
    """Offline fallback used when Pi is disabled (tests / stub mode)."""
    item_id = _SAFE_ID_RE.sub("-", str(item.get("id") or "item")) or "item"
    title = str(item.get("title") or item_id)
    kind = str(item.get("kind") or "feature")
    peers = item.get("peer_services") or []
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "impl.py").write_text(
        f'"""Implementation stub for {title} ({kind}) on {microservice_name}."""\n\n'
        f"ITEM_ID = {item_id!r}\n"
        f"PEERS = {list(peers)!r}\n\n\n"
        f"def apply() -> dict[str, str]:\n"
        f"    return {{'item_id': ITEM_ID, 'service': {microservice_name!r}}}\n",
        encoding="utf-8",
    )
    (dest / "test_impl.py").write_text(
        f'"""Tests for {title}."""\n\n'
        f"import unittest\n\n"
        f"from impl import apply\n\n\n"
        f"class TestImpl(unittest.TestCase):\n"
        f"    def test_apply(self) -> None:\n"
        f"        result = apply()\n"
        f"        self.assertEqual(result['item_id'], {item_id!r})\n"
        f"        self.assertEqual(result['service'], {microservice_name!r})\n",
        encoding="utf-8",
    )
    return dest


def _safe_item_id(item_id: str) -> str:
    return _SAFE_ID_RE.sub("-", str(item_id or "item")) or "item"


def snapshot_dir_for(private_dir: Path, item_id: str) -> Path:
    return Path(private_dir) / _SNAPSHOT_DIR / _safe_item_id(item_id) / "before"


def _skip_snapshot_path(path: Path, root: Path) -> bool:
    try:
        parts = set(path.relative_to(root).parts)
    except ValueError:
        return True
    return bool(parts & _SNAPSHOT_SKIP)


def snapshot_item_workspace(private_dir: Path, item_id: str) -> Path:
    """Copy the service folder (except snapshots) so Pi work on this item can be undone."""
    root = Path(private_dir)
    dest = snapshot_dir_for(root, item_id)
    if dest.exists():
        shutil.rmtree(dest, ignore_errors=True)
    dest.mkdir(parents=True, exist_ok=True)
    if not root.is_dir():
        return dest
    for path in root.rglob("*"):
        if _skip_snapshot_path(path, root):
            continue
        rel = path.relative_to(root)
        target = dest / rel
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        elif path.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
    return dest


def restore_item_workspace(private_dir: Path, item_id: str) -> bool:
    """Restore the service folder to the snapshot taken before this item's Pi run."""
    root = Path(private_dir)
    src = snapshot_dir_for(root, item_id)
    if not src.is_dir():
        return False
    for child in list(root.iterdir()):
        if child.name == _SNAPSHOT_DIR:
            continue
        if child.is_dir():
            shutil.rmtree(child, ignore_errors=True)
        else:
            child.unlink(missing_ok=True)
    for path in src.rglob("*"):
        rel = path.relative_to(src)
        target = root / rel
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        elif path.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
    return True


def write_implementation_status(
    private_dir: Path,
    *,
    microservice_name: str,
    items: list[dict[str, Any]],
) -> str:
    """Rewrite the service implementation-status markdown from current plan items."""
    name = microservice_name or "Service"
    stamp = datetime.now(timezone.utc).isoformat()
    lines = [
        f"# Implementation status — {name}",
        "",
        f"Updated: `{stamp}`",
        "",
        "| Item | Kind | Status |",
        "| --- | --- | --- |",
    ]
    for item in items:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or item.get("id") or "item").replace("|", "/")
        kind = str(item.get("kind") or "")
        status = str(item.get("status") or "")
        lines.append(f"| {title} | {kind} | {status} |")
    details = []
    for item in items:
        if not isinstance(item, dict):
            continue
        notes = str(item.get("notes") or "").strip()
        if not notes:
            continue
        details.append(f"## {item.get('title') or item.get('id')}\n\n{notes}")
    if details:
        lines.extend(["", *details])
    text = "\n".join(lines).rstrip() + "\n"
    root = Path(private_dir)
    if root.is_dir():
        (root / "IMPLEMENTATION_STATUS.md").write_text(text, encoding="utf-8")
    return text


def write_item_work(
    private_dir: Path,
    *,
    item: dict[str, Any],
    microservice_name: str,
    instructions: str = "",
) -> Path:
    """Write stub code + test for one plan item under the private folder."""
    dest = write_item_spec(
        private_dir,
        item=item,
        microservice_name=microservice_name,
        instructions=instructions,
    )
    return write_item_stubs(dest, item=item, microservice_name=microservice_name)


def run_workspace_tests(private_dir: Path) -> tuple[bool, str]:
    """Run every item test suite in the private folder. All must pass before the next item."""
    root = Path(private_dir)
    suites = sorted({path.parent.resolve() for path in root.glob("items/*/test_*.py")})
    if not suites:
        return False, "No tests were added for the current item."
    failures: list[str] = []
    for dest in suites:
        try:
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "unittest",
                    "discover",
                    "-s",
                    str(dest),
                    "-p",
                    "test_*.py",
                    "-q",
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=90,
                cwd=str(dest),
                env={**os.environ, "PYTHONPATH": str(dest)},
            )
        except subprocess.TimeoutExpired:
            failures.append(f"{dest.name}: tests timed out.")
            continue
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{dest.name}: could not run tests ({exc}).")
            continue
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "tests failed").strip()
            failures.append(f"{dest.name}: {detail[:800]}")
    if failures:
        return False, "Tests failed; I will not start the next plan item.\n" + "\n".join(failures)
    return True, f"All tests passed ({len(suites)} item suite(s))."


def ship_workspace(
    *,
    session_id: str,
    microservice_name: str,
    git_data: dict[str, str] | None,
) -> tuple[str, str]:
    """Commit and push only after the entire plan is done. Returns (status, error)."""
    settings = get_settings()
    root = repo_root_for(session_id)
    if not settings.git_execute_enabled:
        return "local_only", ""
    if not git_data or not git_data.get("repo_url") or not git_data.get("ssh_private_key"):
        return "local_only", ""
    if not (root / ".git").is_dir():
        return "local_only", ""
    try:
        _git(["add", "-A"], root)
        staged = _git(["status", "--porcelain"], root)
        if not (staged.stdout or "").strip():
            return "unchanged", ""
        _git(
            [
                "commit",
                "-m",
                f"feat({microservice_name}): complete execution plan",
            ],
            root,
        )
        _git_with_key(["push"], root, git_data["ssh_private_key"])
        return "pushed", ""
    except Exception as exc:  # noqa: BLE001
        logger.exception("git ship failed for fleet %s", session_id)
        return "failed", "Could not ship the completed plan to the git remote."


def _clone_or_pull(root: Path, repo_url: str, ssh_private_key: str) -> None:
    if (root / ".git").is_dir():
        _git_with_key(["pull", "--ff-only"], root, ssh_private_key)
        return
    if any(root.iterdir()):
        # Local files already exist (prior skipped clone). Do not clobber.
        logger.info("Workspace %s already has files; skipping clone", root)
        return
    parent = root.parent
    parent.mkdir(parents=True, exist_ok=True)
    tmp = parent / f".clone-{root.name}"
    if tmp.exists():
        _rmtree(tmp)
    _git_with_key(["clone", repo_url, str(tmp)], parent, ssh_private_key)
    tmp.rename(root)


def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["git", *args],
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(cwd),
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
    )
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "git command failed").strip()[:400])
    return result


def _git_with_key(
    args: list[str],
    cwd: Path,
    ssh_private_key: str,
) -> subprocess.CompletedProcess[str]:
    key_path: Path | None = None
    try:
        handle = tempfile.NamedTemporaryFile("w", delete=False, prefix="sf-eng-git-", suffix=".pem")
        key_path = Path(handle.name)
        handle.write(ssh_private_key)
        handle.close()
        os.chmod(key_path, stat.S_IRUSR | stat.S_IWUSR)
        ssh_cmd = (
            f"ssh -i {key_path} -o IdentitiesOnly=yes -o BatchMode=yes "
            f"-o StrictHostKeyChecking=accept-new -o UserKnownHostsFile=/dev/null"
        )
        result = subprocess.run(
            ["git", *args],
            check=False,
            capture_output=True,
            text=True,
            timeout=90,
            cwd=str(cwd),
            env={**os.environ, "GIT_SSH_COMMAND": ssh_cmd, "GIT_TERMINAL_PROMPT": "0"},
        )
    except FileNotFoundError as exc:
        raise RuntimeError("git is not installed on the engineer host.") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("Timed out contacting the git repo.") from exc
    finally:
        if key_path is not None:
            try:
                key_path.unlink(missing_ok=True)
            except OSError:
                logger.warning("Failed to delete temporary git key file")
    if result.returncode != 0:
        raise RuntimeError("git remote operation failed.")
    return result


def _rmtree(path: Path) -> None:
    import shutil

    shutil.rmtree(path, ignore_errors=True)
