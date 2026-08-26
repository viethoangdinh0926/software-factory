"""Keep a Mermaid design diagram once the track has reached the diagram step."""

from __future__ import annotations

import re

from architect_agent.json_util import coerce_diagram_text
from architect_agent.mermaid_sanitize import sanitize_mermaid

_NODE_DECL_RE = re.compile(r"\b([A-Za-z][\w]*)\s*(?:\[|\(|\{)")
_NODE_LABEL_RES = (
    re.compile(r'\b([A-Za-z][\w-]*)\s*\[\s*\(?\s*"?([^\]\)\n"]+?)"?\s*\)?\s*\]'),
    re.compile(r'\b([A-Za-z][\w-]*)\s*\{\s*"?([^}\n"]+?)"?\s*\}'),
    re.compile(r'\b([A-Za-z][\w-]*)\s*\(\s*"?([^)\n"]+?)"?\s*\)'),
)
_EDGE_RE = re.compile(r"\b([A-Za-z][\w-]*)\s*-+\s*>\s*([A-Za-z][\w-]*)")
_DIAGRAM_COMPONENTS_HEADING = "## Diagram components"


def diagram_node_count(diagram: str) -> int:
    return len(set(_NODE_DECL_RE.findall(diagram or "")))


def diagram_is_concrete(diagram: str, *, minimum: int = 6) -> bool:
    return diagram_node_count(diagram) >= minimum


def diagram_is_due(phase: str, track: str, step: int) -> bool:
    """True once the track should have produced a diagram."""
    if phase in {"market_research", "done"}:
        return track in {"lld", "hld"}
    if phase == "lld":
        return int(step or 0) >= 2
    if phase == "hld":
        return int(step or 0) >= 4
    return False


def fallback_design_diagram(spec: str, track: str = "lld") -> str:
    """Deterministic sketch so the UI is never blank after the diagram step."""
    if track == "hld":
        return _fallback_hld_diagram(spec)
    return _fallback_lld_diagram(spec)


def ensure_design_diagram(
    spec: str,
    prior: str = "",
    *,
    track: str = "lld",
    allow_llm: bool = False,
) -> str:
    """Return a usable Mermaid diagram, preferring an existing one."""
    kept = sanitize_mermaid(prior or "")
    if diagram_is_concrete(kept):
        return kept
    if allow_llm:
        drafted = sanitize_mermaid(_llm_draft_diagram(spec, kept, track=track))
        if diagram_is_concrete(drafted):
            return drafted
        if drafted.strip() and diagram_node_count(drafted) > diagram_node_count(kept):
            kept = drafted
    fallback = sanitize_mermaid(fallback_design_diagram(spec, track))
    if diagram_is_concrete(kept) or diagram_node_count(kept) >= diagram_node_count(fallback):
        return kept or fallback
    return fallback


def _llm_draft_diagram(spec: str, prior: str, *, track: str) -> str:
    from architect_agent.graph.nodes.common import invoke_json

    kind = (
        "class/structure diagram of the desktop or local application "
        "(modules, stores, clients — not a distributed microservice mesh)"
        if track == "lld"
        else "system architecture flowchart (clients, gateway, services, stores)"
    )
    result = invoke_json(
        system=(
            "You draft only a Mermaid design diagram for the Architect agent.\n"
            f"Write a {kind} with at least 8 named nodes.\n"
            "Respond ONLY with JSON:\n"
            "{\n"
            '  "design_diagram_lines": [string, ...],\n'
            '  "design_diagram": "",\n'
            '  "assistant_message": "Diagram drafted."\n'
            "}\n"
            "Use design_diagram_lines only. First line must be flowchart LR or classDiagram."
        ),
        user=(
            f"Living specification:\n\n{spec or '(empty)'}\n\n"
            f"Current diagram:\n{prior or '(none)'}\n"
        ),
    )
    return coerce_diagram_text(result, fallback=prior)


def _fallback_lld_diagram(spec: str) -> str:
    lower = (spec or "").lower()
    lines = [
        "flowchart LR",
        "  UI[Desktop UI] --> App[Application Shell]",
        "  App --> Editor[Request Editor]",
        "  App --> Collections[Collection Store]",
        "  App --> History[History Store]",
        "  App --> Http[HTTP Client]",
        "  App --> Errors[Error Presenter]",
        "  Collections --> Sqlite[(SQLite)]",
        "  History --> Sqlite",
        "  Editor --> Http",
        "  Http --> Remote[Remote API]",
    ]
    if any(token in lower for token in ("auth", "token", "credential", "api key")):
        lines += [
            "  App --> Creds[Credential Store]",
            "  Creds --> Sqlite",
        ]
    if any(token in lower for token in ("queue", "offline", "sync")):
        lines += [
            "  App --> Queue[Request Queue]",
            "  Queue --> Http",
            "  Queue --> Sqlite",
        ]
    return "\n".join(lines)


def _fallback_hld_diagram(spec: str) -> str:
    del spec
    return "\n".join(
        [
            "flowchart LR",
            "  Client[Web/Mobile Client] --> LB[Load Balancer]",
            "  LB --> GW[API Gateway]",
            "  GW --> Auth[Auth IdentityService]",
            "  GW --> Core[Core Domain Service]",
            "  Core --> DB[(Postgres)]",
            "  Core --> Cache[(Redis)]",
            "  Core --> Bus[(Kafka)]",
            "  GW --> Search[Search Service]",
            "  Search --> ES[(Elasticsearch)]",
            "  Client --> CDN[CDN]",
        ]
    )


def extract_diagram_components(diagram: str) -> list[tuple[str, str]]:
    """Return unique (node_id, label) pairs in diagram order."""
    seen: set[str] = set()
    out: list[tuple[str, str]] = []
    for pattern in _NODE_LABEL_RES:
        for node_id, label in pattern.findall(diagram or ""):
            key = node_id.lower()
            if key in seen:
                continue
            seen.add(key)
            clean = (label or node_id).strip()
            if clean:
                out.append((node_id, clean))
    return out


def extract_spec_section(spec: str, heading: str) -> str:
    title = heading if heading.startswith("## ") else f"## {heading}"
    match = re.search(
        rf"(?ms)^{re.escape(title)}\s*\n(.*?)(?=^## |\Z)",
        spec or "",
    )
    return (match.group(1) if match else "").strip()


def strip_spec_sections(spec: str, headings: tuple[str, ...]) -> str:
    text = spec or ""
    for heading in headings:
        title = heading if heading.startswith("## ") else f"## {heading}"
        text = re.sub(rf"(?ms)^{re.escape(title)}\s*\n.*?(?=^## |\Z)", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def upsert_spec_section(spec: str, heading: str, body: str) -> str:
    """Replace or append a `## Heading` block in the living spec."""
    title = heading if heading.startswith("## ") else f"## {heading}"
    inner = (body or "").strip()
    inner = re.sub(rf"(?m)^{re.escape(title)}\s*\n*", "", inner, count=1).strip()
    block = f"{title}\n\n{inner}\n"
    text = spec or ""
    match = re.search(rf"(?ms)^{re.escape(title)}\s*\n.*?(?=^## |\Z)", text)
    if match:
        return f"{text[: match.start()].rstrip()}\n\n{block}{text[match.end() :].lstrip()}".rstrip() + "\n"
    return f"{text.rstrip()}\n\n{block}"


def catalog_covers_diagram(catalog: str, diagram: str) -> bool:
    labels = [label for _, label in extract_diagram_components(diagram)]
    if not labels or not (catalog or "").strip():
        return False
    blob = catalog.lower()
    hits = sum(1 for label in labels if label.lower() in blob)
    return hits >= min(3, max(1, (len(labels) + 1) // 2))


def fallback_component_catalog(diagram: str, spec: str = "") -> str:
    """One headed note per diagram node so chat and the spec stay aligned."""
    components = extract_diagram_components(diagram)
    if not components:
        return ""
    neighbors = _neighbors(diagram, {node_id: label for node_id, label in components})
    lines = [f"{_DIAGRAM_COMPONENTS_HEADING}", ""]
    for node_id, label in components:
        peers = neighbors.get(node_id, [])
        peer_txt = ", ".join(peers[:4]) if peers else ""
        lines.append(f"### {label}")
        lines.append(_component_role(label, spec, peer_txt))
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def ensure_component_catalog(
    diagram: str,
    spec: str = "",
    prior: str = "",
    *,
    allow_llm: bool = False,
) -> str:
    """Return a per-node catalog, preferring a catalog that already covers the diagram."""
    if catalog_covers_diagram(prior, diagram):
        return prior.strip() + ("\n" if not prior.endswith("\n") else "")
    if allow_llm:
        drafted = _llm_component_catalog(diagram, spec, prior)
        if catalog_covers_diagram(drafted, diagram):
            return drafted.strip() + "\n"
    return fallback_component_catalog(diagram, spec)


def with_component_walkthrough(message: str, catalog: str) -> str:
    """Make sure chat names what each diagram box does."""
    body = (catalog or "").strip()
    chat = (message or "").strip()
    if not body:
        return chat
    if "diagram components" in chat.lower():
        return chat
    headings = re.findall(r"(?m)^### (.+)$", body)
    if headings and sum(1 for title in headings if title.lower() in chat.lower()) >= min(
        3, len(headings)
    ):
        return chat
    walkthrough = (
        "Here is what each box on the **system design diagram** is responsible for.\n\n"
        f"{body}"
    )
    if not chat:
        return walkthrough
    return f"{chat}\n\n{walkthrough}"


def chat_describes_components(messages: list[dict[str, object]], diagram: str) -> bool:
    texts = "\n".join(
        str(m.get("content") or "")
        for m in (messages or [])
        if str(m.get("role") or "") == "assistant"
    )
    if "diagram components" in texts.lower():
        return True
    return catalog_covers_diagram(texts, diagram)


def _neighbors(diagram: str, labels: dict[str, str]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {node_id: [] for node_id in labels}
    for start, end in _EDGE_RE.findall(diagram or ""):
        if start in out and end in labels:
            name = labels[end]
            if name not in out[start]:
                out[start].append(name)
        if end in out and start in labels:
            name = labels[start]
            if name not in out[end]:
                out[end].append(name)
    return out


def _component_role(label: str, spec: str, peers: str) -> str:
    lower = label.lower()
    spec_l = (spec or "").lower()
    if "desktop ui" in lower or lower == "ui":
        role = "Operator-facing desktop surface for collections, the request editor, and results."
    elif "application shell" in lower or lower == "app":
        role = "Composes stores, the editor, HTTP, and error presentation; owns the desktop lifecycle."
    elif "request editor" in lower:
        role = "Builds the headers, body, and target of an API request before dispatch."
    elif "collection" in lower:
        role = "Persists named collections of API requests in the local store."
    elif "history" in lower:
        role = "Persists request/response pairs, status, and body for later review."
    elif "http" in lower:
        role = "Dispatches HTTP/HTTPS calls to the remote API and returns status and body."
    elif "error" in lower:
        role = "Surfaces network and server failures to the operator instead of failing silently."
    elif "sqlite" in lower:
        role = "Embedded transactional database for collections, history, and other local records."
    elif "remote" in lower or lower.endswith(" api"):
        role = "Destination service under test; reached only while the client is online."
    elif "credential" in lower or "auth" in lower or "identity" in lower:
        role = "Holds tokens or API keys used when a request needs authentication."
    elif "queue" in lower:
        role = (
            "Holds work that could not be sent yet."
            if "offline" in spec_l or "queue" in spec_l
            else "Optional buffer between the shell and the HTTP client."
        )
    elif "gateway" in lower:
        role = "Edge entry: routing, authn/authz, and protocol translation for clients."
    elif "load balancer" in lower or lower == "lb":
        role = "Spreads inbound client traffic across gateway instances."
    elif "cdn" in lower:
        role = "Caches and serves static or streamed content close to clients."
    elif "redis" in lower or "cache" in lower:
        role = "Low-latency cache and session/hot-key store in front of durable data."
    elif "kafka" in lower or "bus" in lower:
        role = "Async event backbone between services."
    elif "postgres" in lower or "database" in lower or lower == "db":
        role = "System of record for durable domain data."
    elif "search" in lower or "elastic" in lower:
        role = "Indexes documents for discovery and full-text lookup."
    elif "client" in lower:
        role = "User-facing client that talks to the edge of the system."
    elif "service" in lower:
        role = "Owns a bounded slice of domain operations and data."
    else:
        role = f"{label} participates in the structure shown on the diagram."
    if peers:
        return f"{role} Collaborates with {peers}."
    return role


def _llm_component_catalog(diagram: str, spec: str, prior: str) -> str:
    from architect_agent.graph.nodes.common import invoke_json

    result = invoke_json(
        system=(
            "You write a diagram components catalog for the Architect agent.\n"
            "For EVERY named node on the Mermaid diagram, write a ### heading with that "
            "node's label and 1–2 sentences: what it owns, why it exists, who it talks to.\n"
            "Respond ONLY with JSON:\n"
            "{\n"
            '  "design_justification": string\n'
            "}\n"
            "design_justification MUST start with '## Diagram components' and include one "
            "### heading per diagram node."
        ),
        user=(
            f"Living specification:\n\n{spec or '(empty)'}\n\n"
            f"Mermaid:\n{diagram or '(none)'}\n\n"
            f"Current catalog:\n{prior or '(none)'}\n"
        ),
    )
    return str(result.get("design_justification") or "").strip()
