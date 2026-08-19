"""Parse JSON from LLM replies that often contain raw newlines / broken strings."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from json_repair import repair_json

logger = logging.getLogger(__name__)

_OBJECT_START_RE = re.compile(r"\{")
_FENCED_JSON_RE = re.compile(r"```(?:json)?\s*\r?\n([\s\S]*?)```", re.IGNORECASE)
_MARKET_GRADE_RE = re.compile(
    r"(?i)(?:idea\s+)?grade\b[^\nA-F]{0,48}\b([ABCDF])(?:\s*[+\-])?\b"
)
_BOLD_GRADE_RE = re.compile(r"\*\*\s*([ABCDF])(?:\s*[+\-])?\s*\*\*")


def parse_llm_json_object(text: str) -> dict[str, Any]:
    """Extract and parse a JSON object from model output.

    Handles:
    - trailing prose / second objects (``Extra data``)
    - raw newlines inside string values
    - **truncated** replies (unbalanced braces / cut mid-string) via close+repair
    - ```json fences after a markdown preamble
    """
    cleaned = _strip_fences(text.strip())
    if not cleaned:
        raise ValueError("Expected JSON object in model response: (empty)")

    errors: list[str] = []
    candidates = _candidate_blobs(cleaned)

    for candidate in candidates:
        parsed = _try_parse_dict(candidate)
        if parsed is not None:
            return parsed
        try:
            json.loads(candidate)
        except json.JSONDecodeError as exc:
            errors.append(str(exc))

    # Last resort: json-repair only when the text looks like it contains an object.
    if "{" in cleaned:
        for candidate in candidates:
            try:
                repaired = repair_json(candidate, return_objects=True)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"repair failed: {exc}")
                continue
            if isinstance(repaired, dict):
                return repaired
            if isinstance(repaired, str):
                parsed = _try_parse_dict(repaired)
                if parsed is not None:
                    return parsed

    raise ValueError(
        "Failed to parse model JSON into an object "
        f"({'; '.join(errors[:2]) or 'unknown'}; preview={cleaned[:240]!r})"
    )


def coerce_diagram_text(payload: dict[str, Any], fallback: str = "") -> str:
    """Accept ``design_diagram`` string or ``design_diagram_lines`` array."""
    lines = payload.get("design_diagram_lines")
    if isinstance(lines, list) and lines:
        return "\n".join(str(line) for line in lines)
    diagram = payload.get("design_diagram")
    if isinstance(diagram, str) and diagram.strip():
        return diagram
    return fallback


_MERMAID_FENCE_RE = re.compile(
    r"```(?:mermaid)?\s*\n([\s\S]*?)```",
    re.IGNORECASE,
)
_MERMAID_START_RE = re.compile(
    r"(?im)^(?:mermaid\s*\n)?((?:flowchart|graph|sequenceDiagram|classDiagram|stateDiagram"
    r"|erDiagram|journey|gantt|pie|mindmap|timeline|C4Context|C4Container)\b[\s\S]+)"
)


def extract_mermaid_from_text(text: str) -> str:
    """Pull a Mermaid diagram out of markdown / bare diagram replies."""
    raw = (text or "").strip()
    if not raw:
        return ""
    fences = _MERMAID_FENCE_RE.findall(raw)
    for block in fences:
        block = block.strip()
        if re.match(
            r"^(?:flowchart|graph|sequenceDiagram|classDiagram|stateDiagram|erDiagram)\b",
            block,
            re.I,
        ):
            return block
        # Fenced as ```mermaid with body starting after optional blank lines
        if "-->" in block or "subgraph" in block.lower():
            return block
    match = _MERMAID_START_RE.search(raw)
    if match:
        body = match.group(1).strip()
        # Stop at markdown headings that often follow diagrams in prose dumps.
        body = re.split(r"\n(?=#{1,3}\s)", body, maxsplit=1)[0].strip()
        return body
    return ""


def recover_architecture_payload_from_prose(text: str) -> dict[str, Any] | None:
    """Best-effort payload when the model ignores JSON and dumps markdown/Mermaid."""
    cleaned = (text or "").strip()
    if not cleaned:
        return None
    # If it already looks like JSON, do not invent a recovery object here.
    if cleaned.lstrip().startswith("{") or cleaned.lstrip().startswith("```json"):
        return None

    mermaid = extract_mermaid_from_text(cleaned)
    if mermaid:
        lines = [ln for ln in mermaid.splitlines() if ln.strip() != ""]
        logger.info(
            "Recovered Mermaid (%d lines) from non-JSON LLM reply",
            len(lines),
        )
        return {
            "updated_business_spec": "",
            "tradeoff_ledger": "",
            "scale_estimates": "",
            "core_microservices": "",
            "api_contracts": "",
            "communication_schemes": "",
            "fmea_notes": "",
            "design_diagram_lines": lines,
            "design_diagram": "",
            "design_justification": "",
            "ready_to_advance": False,
            "design_ready_to_approve": False,
            "assistant_message": (
                "Recovered a Mermaid diagram from a non-JSON model reply. "
                "Review the diagram; reply again if other artifacts still need deepening."
            ),
        }

    # Pure prose / architecture essay — keep session alive with a clear nudge.
    if len(cleaned) > 40 and not cleaned.lstrip().startswith("{"):
        logger.info("LLM returned prose without Mermaid; synthesizing empty-artifact payload")
        return {
            "updated_business_spec": "",
            "tradeoff_ledger": "",
            "scale_estimates": "",
            "core_microservices": "",
            "api_contracts": "",
            "communication_schemes": "",
            "fmea_notes": "",
            "design_diagram_lines": [],
            "design_diagram": "",
            "design_justification": "",
            "ready_to_advance": False,
            "design_ready_to_approve": False,
            "assistant_message": (
                "I need a single JSON object (starting with `{`), not a markdown essay. "
                "Put Mermaid in `design_diagram_lines` as an array of short lines, and leave "
                "unchanged fields as empty strings."
            ),
        }
    return None


def recover_market_evaluation_from_prose(text: str) -> dict[str, Any] | None:
    """Treat a markdown essay as the market-evaluation report when JSON is missing."""
    cleaned = (text or "").strip()
    if not cleaned:
        return None
    if cleaned.lstrip().startswith("{") or cleaned.lstrip().startswith("```json"):
        return None
    looks_like_report = bool(
        re.search(
            r"(?i)(executive summary|market evaluation|popular alternatives|"
            r"build vs buy|idea grade|strategic recommendations)",
            cleaned,
        )
    )
    if not looks_like_report and len(cleaned) < 80:
        return None
    grade = _extract_letter_grade(cleaned)
    summary = _executive_summary(cleaned)
    report = cleaned if cleaned.lstrip().startswith("#") else (
        "# Market Evaluation Report\n\n" + cleaned
    )
    logger.info("Recovered market evaluation from non-JSON LLM reply (grade=%s)", grade)
    return {
        "grade": grade,
        "grade_rationale": summary,
        "summary": summary,
        "report_markdown": report,
    }


def recover_interview_payload_from_prose(text: str) -> dict[str, Any] | None:
    """Keep the spec interview alive when the model dumps prose instead of JSON."""
    cleaned = (text or "").strip()
    if not cleaned:
        return None
    if cleaned.lstrip().startswith("{") or cleaned.lstrip().startswith("```json"):
        return None
    logger.info("Recovered spec-interview payload from non-JSON LLM reply")
    return {
        "ready_for_design": False,
        "updated_business_spec": "",
        "assistant_message": (
            "I need a JSON object to continue the interview. Send your next answer, "
            "or approve when the checklist is covered."
        ),
        "topic_id": "format",
        "rationale": "model returned non-JSON",
    }


def _extract_letter_grade(text: str) -> str:
    match = _MARKET_GRADE_RE.search(text) or _BOLD_GRADE_RE.search(text)
    if match:
        return match.group(1).upper()
    return "C"


def _executive_summary(text: str) -> str:
    match = re.search(
        r"(?is)##\s*executive summary\s*\n+(.+?)(?=\n##\s|\Z)",
        text,
    )
    blob = match.group(1) if match else ""
    if not blob.strip():
        for para in re.split(r"\n\s*\n", text):
            para = para.strip()
            if para and not para.startswith("#") and len(para) > 40:
                blob = para
                break
    blob = re.sub(r"\s+", " ", blob).strip()
    return blob[:600] or "Market evaluation complete; see the report for details."


def _candidate_blobs(cleaned: str) -> list[str]:
    """Build parse candidates: balanced object, truncated tail, control-escaped variants."""
    blobs: list[str] = []
    for fence in _FENCED_JSON_RE.findall(cleaned):
        fence = fence.strip()
        if fence.startswith("{") or fence.startswith("["):
            blobs.append(fence)
    starts = _object_start_indexes(cleaned)
    if not starts:
        blobs.append(cleaned)
    else:
        start = starts[0]
        try:
            blobs.append(_extract_balanced_object(cleaned))
        except ValueError:
            # Truncated model output — take from first `{` through end and close later.
            tail = cleaned[start:]
            blobs.append(tail)
            blobs.append(_close_truncated_json(tail))
            logger.info(
                "LLM JSON appears truncated/unbalanced (%d chars); attempting repair",
                len(tail),
            )

        # Also try from later `{` if the first was prose with a brace.
        for start in starts[1:3]:
            chunk = cleaned[start:]
            try:
                blobs.append(_extract_balanced_object(cleaned[start:]))
            except ValueError:
                blobs.append(_close_truncated_json(chunk))

    out: list[str] = []
    seen: set[str] = set()
    for blob in blobs:
        for variant in (
            blob,
            _escape_raw_controls_in_strings(blob),
            _escape_invalid_backslashes_in_strings(blob),
            _escape_invalid_backslashes_in_strings(_escape_raw_controls_in_strings(blob)),
        ):
            if variant and variant not in seen:
                seen.add(variant)
                out.append(variant)
    return out


def _try_parse_dict(text: str) -> dict[str, Any] | None:
    parsed = _raw_decode_object(text)
    if isinstance(parsed, dict):
        return parsed
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _strip_fences(text: str) -> str:
    if not text.startswith("```"):
        return text
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _object_start_indexes(text: str) -> list[int]:
    return [m.start() for m in _OBJECT_START_RE.finditer(text)]


def _raw_decode_object(text: str) -> dict[str, Any] | list[Any] | None:
    """Parse the first JSON value; ignore trailing Extra data."""
    try:
        data, _end = json.JSONDecoder().raw_decode(text.lstrip())
    except json.JSONDecodeError:
        return None
    return data  # type: ignore[return-value]


def _extract_balanced_object(text: str) -> str:
    """Return the first top-level `{...}` using brace depth (string-aware)."""
    start = text.find("{")
    if start < 0:
        raise ValueError("no object start")

    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escape:
                escape = False
                continue
            if ch == "\\":
                escape = True
                continue
            if ch == '"':
                in_string = False
            continue

        if ch == '"':
            in_string = True
            continue
        if ch == "{":
            depth += 1
            continue
        if ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    raise ValueError("unbalanced JSON object")


def _close_truncated_json(text: str) -> str:
    """Best-effort close of truncated JSON (open strings / braces / brackets)."""
    in_string = False
    escape = False
    stack: list[str] = []
    for ch in text:
        if in_string:
            if escape:
                escape = False
                continue
            if ch == "\\":
                escape = True
                continue
            if ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
            continue
        if ch in "{[":
            stack.append("}" if ch == "{" else "]")
            continue
        if ch in "}]" and stack and stack[-1] == ch:
            stack.pop()

    out = text.rstrip()
    # Trim a dangling trailing backslash or incomplete escape.
    while out.endswith("\\"):
        out = out[:-1]
    if in_string:
        out += '"'
    while stack:
        out += stack.pop()
    return out


def _escape_raw_controls_in_strings(text: str) -> str:
    """Escape literal control chars that appear inside JSON string literals."""
    out: list[str] = []
    in_string = False
    escape = False
    for ch in text:
        if in_string:
            if escape:
                out.append(ch)
                escape = False
                continue
            if ch == "\\":
                out.append(ch)
                escape = True
                continue
            if ch == '"':
                out.append(ch)
                in_string = False
                continue
            code = ord(ch)
            if ch == "\n":
                out.append("\\n")
            elif ch == "\r":
                out.append("\\r")
            elif ch == "\t":
                out.append("\\t")
            elif code < 0x20:
                out.append(f"\\u{code:04x}")
            else:
                out.append(ch)
            continue

        out.append(ch)
        if ch == '"':
            in_string = True
    return "".join(out)


def _escape_invalid_backslashes_in_strings(text: str) -> str:
    """Keep LaTeX like ``\\text`` / ``\\approx`` as literals instead of JSON escapes.

    Models often write ``$\\text{DAU}$`` inside JSON strings. ``\\t`` is a tab and
    ``\\a`` is invalid JSON, so parse either corrupts or fails.
    """
    out: list[str] = []
    in_string = False
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if not in_string:
            out.append(ch)
            if ch == '"':
                in_string = True
            i += 1
            continue
        if ch == "\\":
            if i + 1 >= n:
                out.append("\\\\")
                i += 1
                continue
            nxt = text[i + 1]
            if nxt == "u" and i + 5 < n and all(
                text[i + 2 + k] in "0123456789abcdefABCDEF" for k in range(4)
            ):
                out.append(text[i : i + 6])
                i += 6
                continue
            if nxt in '"\\/':
                out.append(text[i : i + 2])
                i += 2
                continue
            if nxt in "bfnrt":
                j = i + 2
                while j < n and text[j].isalpha():
                    j += 1
                if j > i + 2:
                    out.append("\\\\")
                    out.append(text[i + 1 : j])
                    i = j
                    continue
                out.append(text[i : i + 2])
                i += 2
                continue
            out.append("\\\\")
            i += 1
            continue
        if ch == '"':
            in_string = False
        out.append(ch)
        i += 1
    return "".join(out)
