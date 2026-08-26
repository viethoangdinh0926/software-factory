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
_SERVICE_NAME_RE = re.compile(r"\b([A-Z][A-Za-z0-9]+Service)\b")
_SERVICE_HEADING_RE = re.compile(r"(?m)^#{2,3}\s+([A-Z][A-Za-z0-9]+Service)\s*$")
_CORE_SECTION_RE = re.compile(r"(?ims)^##\s*(?:.*?\b)?Core Microservices\s*\n(.*?)(?=^##\s|\Z)")
_API_SECTION_RE = re.compile(r"(?ims)^##\s*(?:.*?\b)?API Contracts\s*\n(.*?)(?=^##\s|\Z)")
_COMMS_SECTION_RE = re.compile(r"(?ims)^##\s*(?:.*?\b)?Communication Schemes?\s*\n(.*?)(?=^##\s|\Z)")


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


def _role_key(name: str) -> str:
    stem = re.sub(r"Service$", "", name)
    return re.sub(r"(?<!^)(?=[A-Z])", "-", stem).lower() or name.lower()


def _core_services_source(markdown: str) -> str:
    text = markdown or ""
    for regex in (_CORE_SECTION_RE, _API_SECTION_RE):
        match = regex.search(text)
        if match:
            return match.group(1)
    return ""


def extract_communication_schemes(markdown: str) -> str:
    match = _COMMS_SECTION_RE.search(markdown or "")
    return (match.group(1).strip() if match else "")


def service_comms_excerpt(markdown: str, names: list[str]) -> str:
    """Slice communication schemes for this service, or return the package-level schemes."""
    schemes = extract_communication_schemes(markdown)
    if not schemes:
        return ""
    wanted = {n.strip() for n in names if n and str(n).strip()}
    if not wanted:
        return schemes[:2500]
    blocks = re.split(r"(?m)^(?=#{2,3}\s+)", schemes)
    for block in blocks:
        heading = re.sub(r"^#{2,3}\s+", "", block.split("\n", 1)[0]).strip()
        if heading in wanted:
            return block.strip()[:2500]
    return schemes[:2500]


def extract_core_services(markdown: str) -> list[dict[str, str]]:
    """Pull core *Service names from Core Microservices (or legacy API Contracts) headings."""
    text = markdown or ""
    contracts = _core_services_source(text)
    ordered: list[dict[str, str]] = []
    seen: set[str] = set()

    def add(name: str, summary: str = "") -> None:
        if name in seen:
            return
        seen.add(name)
        ordered.append(
            {
                "name": name,
                "role_key": _role_key(name),
                "contract_summary": summary
                or f"Core microservice {name} as described in the architect package.",
            }
        )

    source = contracts or text
    headings = _SERVICE_HEADING_RE.findall(source)
    if headings:
        blocks = re.split(r"(?m)^#{2,3}\s+", source)
        summaries: dict[str, str] = {}
        for block in blocks:
            first = block.split("\n", 1)[0].strip()
            rest = block.split("\n", 1)[1].strip() if "\n" in block else ""
            if _SERVICE_NAME_RE.fullmatch(first):
                summaries[first] = " ".join(rest.split())[:400]
        for name in headings:
            add(name, summaries.get(name, ""))
        return ordered

    for name in _SERVICE_NAME_RE.findall(source):
        add(name)
    return ordered


def service_contract_section(markdown: str, names: list[str]) -> str:
    """Return the core-microservice subsection for this service only (not the whole package)."""
    wanted = [n.strip() for n in names if n and str(n).strip()]
    if not wanted:
        return ""
    text = markdown or ""
    source = _core_services_source(text) or text
    wanted_set = set(wanted)
    blocks = re.split(r"(?m)^(?=#{2,3}\s+)", source)
    for block in blocks:
        heading = re.sub(r"^#{2,3}\s+", "", block.split("\n", 1)[0]).strip()
        if heading in wanted_set:
            return block.strip()[:2500]
    for name in wanted:
        match = re.search(
            rf"(?ms)^#{{2,3}}\s+{re.escape(name)}\s*\n(.*?)(?=^#{{2,3}}\s|\Z)",
            text,
        )
        if match:
            return f"### {name}\n{match.group(1).strip()}"[:2500]
    return ""


_HTTP_ENDPOINT_RE = re.compile(
    r"(?im)\b(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+(`?/+[A-Za-z0-9_{}/.\-:]*)"
)


def extract_http_endpoints(*texts: str) -> list[tuple[str, str]]:
    """Pull METHOD /path pairs from API design or contract markdown."""
    seen: list[tuple[str, str]] = []
    found: set[tuple[str, str]] = set()
    for text in texts:
        for match in _HTTP_ENDPOINT_RE.finditer(text or ""):
            method = match.group(1).upper()
            path = match.group(2).strip().strip("`")
            if not path.startswith("/"):
                path = "/" + path
            item = (method, path)
            if item not in found:
                found.add(item)
                seen.append(item)
    return seen


def format_agreed_endpoints(name: str, endpoints: list[tuple[str, str]]) -> str:
    if not endpoints:
        return (
            f"No URL endpoints are recorded yet for **{name}**. "
            "Complete the communication spec on this tile (REST paths, gRPC RPCs, or event topics), "
            "or tell me which paths to add."
        )
    lines = [f"Agreed URL endpoints for **{name}**:", ""]
    lines.extend(f"- `{method} {path}`" for method, path in endpoints)
    return "\n".join(lines)
