"""Parse JSON from LLM replies that often contain raw newlines / broken strings."""

from __future__ import annotations

import json
import re
from typing import Any

from json_repair import repair_json

_OBJECT_RE = re.compile(r"\{[\s\S]*\}")


def parse_llm_json_object(text: str) -> dict[str, Any]:
    """Extract and parse a JSON object from model output.

    Models frequently emit Mermaid / markdown with literal newlines or unescaped
    quotes inside JSON string values. Try strict parse, then control-char repair,
    then ``json-repair``.
    """
    cleaned = _strip_fences(text.strip())
    blob = _extract_object(cleaned)

    errors: list[str] = []
    for candidate in (blob, _escape_raw_controls_in_strings(blob)):
        try:
            data = json.loads(candidate)
            if isinstance(data, dict):
                return data
            errors.append(f"parsed non-object {type(data).__name__}")
        except json.JSONDecodeError as exc:
            errors.append(str(exc))

    try:
        repaired = repair_json(blob, return_objects=True)
    except Exception as exc:  # noqa: BLE001
        raise ValueError(
            "Failed to parse model JSON "
            f"({'; '.join(errors[:2]) or 'unknown'}); repair also failed: {exc}"
        ) from exc

    if isinstance(repaired, dict):
        return repaired
    if isinstance(repaired, str):
        data = json.loads(repaired)
        if isinstance(data, dict):
            return data
    raise ValueError(
        "Failed to parse model JSON into an object "
        f"({'; '.join(errors[:2]) or type(repaired).__name__})"
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


def _strip_fences(text: str) -> str:
    if not text.startswith("```"):
        return text
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _extract_object(text: str) -> str:
    match = _OBJECT_RE.search(text)
    if not match:
        raise ValueError(f"Expected JSON object in model response: {text[:400]}")
    return match.group(0)


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
