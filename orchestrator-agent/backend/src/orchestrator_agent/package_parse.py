"""Parse architect-agent design-package markdown."""

from __future__ import annotations

import re
from dataclasses import dataclass

_UUID_RE = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
_COMMENT_SESSION_RE = re.compile(rf"session=({_UUID_RE})", re.I)
_COMMENT_VERSION_RE = re.compile(r"version=(\d+)", re.I)
_BODY_SESSION_RE = re.compile(rf"Design session:\s*`({_UUID_RE})`", re.I)
_BODY_VERSION_RE = re.compile(r"Design version:\s*`(\d+)`", re.I)
_TRACK_RE = re.compile(r"Track:\s*`?(lld|hld)`?", re.I)
_MERMAID_RE = re.compile(r"```(?:mermaid)?\s*\n([\s\S]*?)```", re.I)


@dataclass
class ParsedPackage:
    design_session_id: str
    design_version: int
    architect_track: str
    markdown: str
    design_diagram: str


def parse_design_package(markdown: str) -> ParsedPackage:
    text = markdown or ""
    session_id = ""
    comment_match = _COMMENT_SESSION_RE.search(text)
    body_match = _BODY_SESSION_RE.search(text)
    if comment_match:
        session_id = comment_match.group(1)
    elif body_match:
        session_id = body_match.group(1)

    version = 1
    version_match = _COMMENT_VERSION_RE.search(text) or _BODY_VERSION_RE.search(text)
    if version_match:
        version = int(version_match.group(1))

    track = "unset"
    track_match = _TRACK_RE.search(text)
    if track_match:
        track = track_match.group(1).lower()

    diagram = ""
    for block in _MERMAID_RE.findall(text):
        body = block.strip()
        if re.match(
            r"^(?:flowchart|graph|sequenceDiagram|classDiagram|stateDiagram|erDiagram)\b",
            body,
            re.I,
        ):
            diagram = body
            break
        if "-->" in body:
            diagram = body
            break

    return ParsedPackage(
        design_session_id=session_id,
        design_version=version,
        architect_track=track,
        markdown=text,
        design_diagram=diagram,
    )
