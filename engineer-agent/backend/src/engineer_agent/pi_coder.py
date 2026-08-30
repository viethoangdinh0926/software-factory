"""Hand one execution-plan item to the Pi coding-agent SDK."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

OnQuestions = Callable[[list[str]], None]

from engineer_agent.config import get_settings

logger = logging.getLogger(__name__)

RUNNER_DIR = Path(__file__).resolve().parent / "pi_runner"
RUNNER_SCRIPT = RUNNER_DIR / "run_item.mjs"


@dataclass
class PiItemResult:
    ok: bool
    summary: str
    test_output: str
    error: str
    rounds: int = 0
    stub: bool = False
    stopped: bool = False


def implement_plan_item(
    private_dir: Path,
    *,
    item: dict[str, Any],
    microservice_name: str,
    instructions: str = "",
    feature_spec: str = "",
    bug_spec: str = "",
    offered_api: str = "",
    tech_stack: str = "",
    stop_check: Callable[[], bool] | None = None,
    on_questions: OnQuestions | None = None,
) -> PiItemResult:
    """Write the item brief, then either run Pi or local stubs."""
    from engineer_agent.workspace import snapshot_item_workspace, write_item_spec, write_item_stubs

    dest = write_item_spec(
        private_dir,
        item=item,
        microservice_name=microservice_name,
        instructions=instructions,
        feature_spec=feature_spec,
        bug_spec=bug_spec,
        offered_api=offered_api,
        tech_stack=tech_stack,
    )
    snapshot_item_workspace(private_dir, str(item.get("id") or dest.name))
    if stop_check and stop_check():
        return PiItemResult(
            ok=False,
            summary="",
            test_output="",
            error="Pi coding run was stopped.",
            stopped=True,
        )
    settings = get_settings()
    if not settings.pi_coder_enabled:
        write_item_stubs(dest, item=item, microservice_name=microservice_name)
        return PiItemResult(
            ok=True,
            summary="Wrote local implementation stubs (Pi coder disabled).",
            test_output="",
            error="",
            stub=True,
        )
    return _run_pi_sdk(
        private_dir,
        dest,
        item_id=str(item.get("id") or dest.name),
        stop_check=stop_check,
        on_questions=on_questions,
    )


def _run_pi_sdk(
    private_dir: Path,
    item_dir: Path,
    *,
    item_id: str,
    stop_check: Callable[[], bool] | None = None,
    on_questions: OnQuestions | None = None,
) -> PiItemResult:
    settings = get_settings()
    result_path = item_dir / ".pi-result.json"
    if result_path.exists():
        result_path.unlink()
    if not RUNNER_SCRIPT.is_file():
        return PiItemResult(
            ok=False,
            summary="",
            test_output="",
            error="Pi runner script is missing from the engineer package.",
        )
    node_modules = RUNNER_DIR / "node_modules" / "@earendil-works" / "pi-coding-agent"
    if not node_modules.is_dir():
        return PiItemResult(
            ok=False,
            summary="",
            test_output="",
            error=(
                "Pi SDK is not installed for the engineer runner. "
                f"Run `npm install` in {RUNNER_DIR}."
            ),
        )
    cmd = [
        settings.pi_node_bin,
        str(RUNNER_SCRIPT),
        "--cwd",
        str(private_dir),
        "--item-dir",
        str(item_dir),
        "--max-rounds",
        str(max(1, settings.pi_coder_max_rounds)),
        "--result",
        str(result_path),
        "--python",
        sys.executable,
    ]
    logger.info("Handing plan item to Pi SDK: %s", item_dir)
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(RUNNER_DIR),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env={**os.environ, "PYTHON": sys.executable},
        )
    except FileNotFoundError:
        return PiItemResult(
            ok=False,
            summary="",
            test_output="",
            error=f"Node binary not found: {settings.pi_node_bin}",
        )
    started = time.monotonic()
    limit = max(30, settings.pi_coder_timeout_seconds)
    stdout = ""
    stderr = ""
    last_questions: tuple[str, ...] = ()
    try:
        while True:
            if stop_check and stop_check():
                proc.terminate()
                try:
                    proc.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    proc.kill()
                return PiItemResult(
                    ok=False,
                    summary="",
                    test_output="",
                    error="Pi coding run was stopped.",
                    stopped=True,
                )
            if time.monotonic() - started > limit:
                proc.kill()
                return PiItemResult(
                    ok=False,
                    summary="",
                    test_output="",
                    error=f"Pi coding run exceeded {limit}s.",
                )
            if on_questions:
                from engineer_agent.workspace import read_pi_questions

                current = tuple(read_pi_questions(private_dir, item_id))
                if current and current != last_questions:
                    last_questions = current
                    try:
                        on_questions(list(current))
                    except Exception:  # noqa: BLE001
                        logger.exception("Failed to surface Pi questions to the sub-engineer")
            try:
                stdout, stderr = proc.communicate(timeout=2)
                break
            except subprocess.TimeoutExpired:
                continue
    except Exception as exc:  # noqa: BLE001
        proc.kill()
        logger.exception("Pi SDK runner failed")
        return PiItemResult(ok=False, summary="", test_output="", error=str(exc))

    payload = _read_result(result_path)
    if payload is None:
        detail = (stderr or stdout or "Pi runner produced no result file.").strip()
        return PiItemResult(
            ok=False,
            summary="",
            test_output="",
            error=detail[:2000],
        )
    return PiItemResult(
        ok=bool(payload.get("ok")),
        summary=str(payload.get("summary") or "").strip(),
        test_output=str(payload.get("test_output") or "").strip(),
        error=str(payload.get("error") or "").strip(),
        rounds=int(payload.get("rounds") or 0),
    )


def _read_result(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None
