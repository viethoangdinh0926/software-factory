from __future__ import annotations

import re
from dataclasses import dataclass, field


_FIELD_RE = re.compile(
    r"(?im)^[-*]\s*(action|design_session_id|design_version|microservice_id|microservice_name)\s*:\s*`?([^`\n]+)`?\s*$"
)
_HEADING_RE = re.compile(r"(?m)^##\s+(.+?)\s*$")
_ENTITY_HEAD_RE = re.compile(
    r"(?m)^###\s+(.+?)(?:\s*\(([^)]*)\))?\s*$"
)


@dataclass
class RelatedEntity:
    name: str
    kind: str
    microservice_id: str
    we_initiate: bool
    body: str


@dataclass
class ParsedHandoff:
    action: str
    design_session_id: str
    design_version: int
    microservice_id: str
    microservice_name: str
    entity_relationships: str
    feature_spec: str
    bug_spec: str
    tech_stack: str
    markdown: str
    related_entities: list[RelatedEntity] = field(default_factory=list)


def _section(markdown: str, title: str) -> str:
    wanted = title.strip().lower()
    parts = _HEADING_RE.split(markdown)
    if len(parts) < 3:
        return ""
    # split yields [pre, heading, body, heading, body, ...]
    for i in range(1, len(parts), 2):
        heading = parts[i].strip().lower()
        body = parts[i + 1] if i + 1 < len(parts) else ""
        if heading == wanted or heading.startswith(wanted):
            if body.strip():
                return body.strip()
    return ""


def parse_related_entities(text: str) -> list[RelatedEntity]:
    body = (text or "").strip()
    if not body:
        return []
    chunks: list[tuple[str, str, str]] = []
    matches = list(_ENTITY_HEAD_RE.finditer(body))
    for idx, match in enumerate(matches):
        start = match.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(body)
        chunks.append((match.group(1).strip(), (match.group(2) or "").strip(), body[start:end].strip()))
    out: list[RelatedEntity] = []
    for name, meta, chunk in chunks:
        meta_l = meta.lower()
        kind = "unknown"
        mid = ""
        kind_m = re.search(r"kind\s*:\s*([a-z_]+)", meta_l)
        if kind_m:
            kind = kind_m.group(1)
        id_m = re.search(r"(?:id|microservice_id)\s*:\s*([A-Za-z0-9\-]+)", meta, re.I)
        if id_m:
            mid = id_m.group(1)
        initiate = False
        init_m = re.search(r"(?im)we initiate\s*:\s*(yes|true|no|false)", chunk)
        if init_m:
            initiate = init_m.group(1).lower() in {"yes", "true"}
        elif re.search(r"(?i)\bwe initiate\b", chunk) and re.search(r"(?i)\byes\b", chunk):
            initiate = True
        out.append(
            RelatedEntity(
                name=name,
                kind=kind,
                microservice_id=mid,
                we_initiate=initiate,
                body=chunk,
            )
        )
    return out


def parse_handoff(markdown: str) -> ParsedHandoff:
    text = (markdown or "").strip()
    if not text:
        raise ValueError("Expected orchestrator plan-spec or suspend markdown.")
    fields: dict[str, str] = {}
    for match in _FIELD_RE.finditer(text):
        fields[match.group(1).lower()] = match.group(2).strip()
    action = (fields.get("action") or "").lower()
    if not action:
        if re.search(r"(?im)^#\s*suspend", text):
            action = "suspend"
        elif re.search(r"(?im)^#\s*plan spec", text):
            action = "plan"
        else:
            raise ValueError("Handoff is missing action (plan or suspend).")
    session_id = fields.get("design_session_id") or ""
    if not session_id:
        sid = re.search(r"(?i)design session(?:_id)?[:\s]+`?([0-9a-f-]{36})`?", text)
        if sid:
            session_id = sid.group(1)
    if not session_id:
        raise ValueError("Handoff is missing a design session ID.")
    version_raw = fields.get("design_version") or "1"
    try:
        version = int(re.sub(r"[^0-9]", "", version_raw) or "1")
    except ValueError:
        version = 1
    relations = _section(text, "Entity relationships")
    features = _section(text, "Features / functionality") or _section(text, "Features")
    bugs = _section(text, "Bugs")
    stack = _section(text, "Tech stack")
    return ParsedHandoff(
        action=action,
        design_session_id=session_id,
        design_version=version,
        microservice_id=fields.get("microservice_id") or "",
        microservice_name=fields.get("microservice_name") or ("app" if not fields.get("microservice_id") else "Service"),
        entity_relationships=relations,
        feature_spec=features,
        bug_spec=bugs,
        tech_stack=stack,
        markdown=text,
        related_entities=parse_related_entities(relations),
    )


def sub_agent_id(design_session_id: str, microservice_id: str | None) -> str:
    mid = (microservice_id or "").strip() or "app"
    return f"{design_session_id}:{mid}"
