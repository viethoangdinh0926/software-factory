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
_DIAGRAM_COMPONENTS_HEADING = "## Diagram components"
_DIAGRAM_RELATIONSHIPS_HEADING = "## Diagram relationships"
_REL_HEADING_RE = re.compile(
    r"(?m)^###\s+(.+?)\s*(?:→|->|--|>)\s*(.+?)\s*$"
)
_PIPE_EDGE_RE = re.compile(
    r"^([A-Za-z][\w-]*)(?:\[[^\]]*\]|\([^)]*\)|\{[^}]*\})?\s*"
    r"(?:--+|==+|\.-+|-+\.)>\s*\|([^|\n]*)\|\s*"
    r"([A-Za-z][\w-]*)"
)
_TEXT_EDGE_RE = re.compile(
    r"^([A-Za-z][\w-]*)(?:\[[^\]]*\]|\([^)]*\)|\{[^}]*\})?\s*"
    r"--\s*([^>\n]+?)\s*-->\s*"
    r"([A-Za-z][\w-]*)"
)
_BARE_EDGE_RE = re.compile(
    r"^([A-Za-z][\w-]*)(?:\[[^\]]*\]|\([^)]*\)|\{[^}]*\})?\s*"
    r"(?:--+|==+|\.-+|-+\.)>\s*"
    r"([A-Za-z][\w-]*)"
    r"(?:\[[^\]]*\]|\([^)]*\)|\{[^}]*\})?"
    r"(?:\s*:\s*(.+))?$"
)
_SKIP_EDGE_PREFIXES = (
    "style ",
    "classdef ",
    "class ",
    "click ",
    "subgraph",
    "direction",
    "%%",
    "linkstyle",
)


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
            "Use design_diagram_lines only. First line must be flowchart LR or classDiagram.\n"
            "Keep arrow labels SHORT (protocol or call style: HTTPS, gRPC, Kafka, in-process). "
            "Do not put the full relationship essay on the arrow."
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
        "  UI[Desktop UI] -->|compose| App[Application Shell]",
        "  App -->|in-process| Editor[Request Editor]",
        "  App -->|in-process| Collections[Collection Store]",
        "  App -->|in-process| History[History Store]",
        "  App -->|in-process| Http[HTTP Client]",
        "  App -->|in-process| Errors[Error Presenter]",
        "  Collections -->|SQL| Sqlite[(SQLite)]",
        "  History -->|SQL| Sqlite",
        "  Editor -->|dispatch| Http",
        "  Http -->|HTTPS| Remote[Remote API]",
    ]
    if any(token in lower for token in ("auth", "token", "credential", "api key")):
        lines += [
            "  App -->|in-process| Creds[Credential Store]",
            "  Creds -->|SQL| Sqlite",
        ]
    if any(token in lower for token in ("queue", "offline", "sync")):
        lines += [
            "  App -->|in-process| Queue[Request Queue]",
            "  Queue -->|dispatch| Http",
            "  Queue -->|SQL| Sqlite",
        ]
    return "\n".join(lines)


def _fallback_hld_diagram(spec: str) -> str:
    del spec
    return "\n".join(
        [
            "flowchart LR",
            "  Client[Web/Mobile Client] -->|HTTPS| LB[Load Balancer]",
            "  LB -->|TCP| GW[API Gateway]",
            "  GW -->|gRPC| Auth[Auth IdentityService]",
            "  GW -->|HTTPS| Core[Core Domain Service]",
            "  Core -->|SQL| DB[(Postgres)]",
            "  Core -->|cache| Cache[(Redis)]",
            "  Core -->|pub/sub| Bus[(Kafka)]",
            "  GW -->|HTTPS| Search[Search Service]",
            "  Search -->|index| ES[(Elasticsearch)]",
            "  Client -->|HLS| CDN[CDN]",
        ]
    )


def extract_diagram_edges(diagram: str) -> list[tuple[str, str, str]]:
    """Return unique (start_id, end_id, short_label) pairs in diagram order."""
    seen: set[tuple[str, str]] = set()
    out: list[tuple[str, str, str]] = []
    for raw in (diagram or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        lower = line.lower()
        if lower.startswith(_SKIP_EDGE_PREFIXES) or lower.startswith(("flowchart", "graph ", "classdiagram")):
            continue
        parsed = _parse_edge_line(line)
        if not parsed:
            continue
        start, end, label = parsed
        key = (start, end)
        if key in seen:
            continue
        seen.add(key)
        out.append((start, end, label))
    return out


def _parse_edge_line(line: str) -> tuple[str, str, str] | None:
    match = _PIPE_EDGE_RE.match(line)
    if match:
        return match.group(1), match.group(3), (match.group(2) or "").strip()
    match = _TEXT_EDGE_RE.match(line)
    if match:
        return match.group(1), match.group(3), (match.group(2) or "").strip()
    match = _BARE_EDGE_RE.match(line)
    if match:
        return match.group(1), match.group(2), (match.group(3) or "").strip()
    return None


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
    for start, end, _label in extract_diagram_edges(diagram):
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


def parse_relationship_entries(catalog: str) -> list[tuple[str, str, str]]:
    """Parse ### Start → End blocks into (start, end, explanation) triples."""
    text = catalog or ""
    matches = list(_REL_HEADING_RE.finditer(text))
    out: list[tuple[str, str, str]] = []
    for index, match in enumerate(matches):
        start = (match.group(1) or "").strip().strip("`")
        end = (match.group(2) or "").strip().strip("`")
        body_from = match.end()
        body_to = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        body = text[body_from:body_to].strip()
        body = re.sub(r"^##[^\n]*\n?", "", body).strip()
        if start and end and body:
            out.append((start, end, body))
    return out


def _norm_rel_token(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (value or "").lower())


def lookup_relationship(
    entries: list[tuple[str, str, str]],
    start_id: str,
    end_id: str,
    start_label: str = "",
    end_label: str = "",
) -> str:
    start_keys = {_norm_rel_token(start_id), _norm_rel_token(start_label)} - {""}
    end_keys = {_norm_rel_token(end_id), _norm_rel_token(end_label)} - {""}
    for left, right, body in entries:
        if _norm_rel_token(left) in start_keys and _norm_rel_token(right) in end_keys:
            return body
    return ""


def relationships_cover_diagram(catalog: str, diagram: str) -> bool:
    edges = extract_diagram_edges(diagram)
    entries = parse_relationship_entries(catalog)
    if not edges or not entries:
        return False
    labels = {node_id: label for node_id, label in extract_diagram_components(diagram)}
    hits = 0
    for start, end, _label in edges:
        if lookup_relationship(entries, start, end, labels.get(start, start), labels.get(end, end)):
            hits += 1
    return hits >= len(edges)


def fallback_relationship_catalog(
    diagram: str,
    spec: str = "",
    comms: str = "",
) -> str:
    notes = diagram_edge_notes(diagram, "", spec=spec, comms=comms)
    if not notes:
        return ""
    lines = [_DIAGRAM_RELATIONSHIPS_HEADING, ""]
    for note in notes:
        lines.append(f"### {note['from']} → {note['to']}")
        lines.append(note["explanation"])
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def ensure_relationship_catalog(
    diagram: str,
    spec: str = "",
    comms: str = "",
    prior: str = "",
    *,
    allow_llm: bool = False,
) -> str:
    """Return a per-edge relationship catalog covering every diagram arrow."""
    if relationships_cover_diagram(prior, diagram):
        return prior.strip() + ("\n" if not prior.endswith("\n") else "")
    if allow_llm:
        drafted = _llm_relationship_catalog(diagram, spec, comms, prior)
        if relationships_cover_diagram(drafted, diagram):
            return drafted.strip() + "\n"
        if parse_relationship_entries(drafted):
            merged = _merge_relationship_catalog(diagram, drafted, spec, comms)
            if relationships_cover_diagram(merged, diagram):
                return merged
    return fallback_relationship_catalog(diagram, spec, comms)


def diagram_edge_notes(
    diagram: str,
    catalog: str = "",
    *,
    spec: str = "",
    comms: str = "",
) -> list[dict[str, str]]:
    """Structured notes for every diagram arrow (catalog first, then a written fallback)."""
    labels = {node_id: label for node_id, label in extract_diagram_components(diagram)}
    entries = parse_relationship_entries(catalog)
    notes: list[dict[str, str]] = []
    for start, end, edge_label in extract_diagram_edges(diagram):
        start_label = labels.get(start, start)
        end_label = labels.get(end, end)
        explanation = lookup_relationship(entries, start, end, start_label, end_label)
        if not explanation:
            explanation = _relationship_explanation(
                start, end, start_label, end_label, edge_label, spec, comms
            )
        notes.append(
            {
                "from": start,
                "to": end,
                "from_label": start_label,
                "to_label": end_label,
                "label": edge_label or "",
                "explanation": explanation,
            }
        )
    return notes


def apply_diagram_catalogs(
    diagram: str,
    spec: str,
    *,
    justification: str = "",
    comms: str = "",
    allow_llm: bool = False,
) -> tuple[str, str, str]:
    """Upsert component and relationship catalogs into the living spec."""
    components = ensure_component_catalog(
        diagram,
        spec,
        justification,
        allow_llm=allow_llm,
    )
    prior_rels = extract_spec_section(spec, "Diagram relationships")
    if not prior_rels and "diagram relationships" in (justification or "").lower():
        prior_rels = justification
    relationships = ensure_relationship_catalog(
        diagram,
        spec,
        comms,
        prior_rels,
        allow_llm=allow_llm,
    )
    next_spec = upsert_spec_section(spec, "Diagram components", components)
    next_spec = upsert_spec_section(next_spec, "Diagram relationships", relationships)
    return next_spec, components, relationships


def with_relationship_walkthrough(message: str, catalog: str) -> str:
    """Make sure chat explains each connecting line on the diagram."""
    body = (catalog or "").strip()
    chat = (message or "").strip()
    if not body:
        return chat
    if "diagram relationships" in chat.lower():
        return chat
    headings = re.findall(r"(?m)^### (.+)$", body)
    if headings and sum(1 for title in headings if title.lower() in chat.lower()) >= min(
        3, len(headings)
    ):
        return chat
    walkthrough = (
        "Each connecting line on the **system design diagram** is a relationship. "
        "Hover a line to read the same explanation.\n\n"
        f"{body}"
    )
    if not chat:
        return walkthrough
    return f"{chat}\n\n{walkthrough}"


def with_diagram_walkthrough(message: str, components: str, relationships: str = "") -> str:
    return with_relationship_walkthrough(
        with_component_walkthrough(message, components),
        relationships,
    )


def chat_describes_relationships(messages: list[dict[str, object]], diagram: str) -> bool:
    texts = "\n".join(
        str(m.get("content") or "")
        for m in (messages or [])
        if str(m.get("role") or "") == "assistant"
    )
    if "diagram relationships" in texts.lower():
        return True
    return relationships_cover_diagram(texts, diagram)


def _merge_relationship_catalog(
    diagram: str,
    catalog: str,
    spec: str,
    comms: str,
) -> str:
    notes = diagram_edge_notes(diagram, catalog, spec=spec, comms=comms)
    lines = [_DIAGRAM_RELATIONSHIPS_HEADING, ""]
    for note in notes:
        lines.append(f"### {note['from']} → {note['to']}")
        lines.append(note["explanation"])
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def _relationship_explanation(
    start_id: str,
    end_id: str,
    start_label: str,
    end_label: str,
    edge_label: str,
    spec: str,
    comms: str,
) -> str:
    snippet = _snippet_from_text(comms, start_label, end_label, start_id, end_id)
    if not snippet:
        snippet = _snippet_from_text(spec, start_label, end_label, start_id, end_id)
    protocol = (edge_label or "").strip()
    proto = f" over {protocol}" if protocol else ""
    start_kind = _node_kind(start_label or start_id)
    end_kind = _node_kind(end_label or end_id)
    body = _relationship_template(start_kind, end_kind, start_label, end_label, proto)
    if snippet and snippet.lower() not in body.lower():
        return f"{body} {snippet}"
    return body


def _snippet_from_text(text: str, *needles: str) -> str:
    if not (text or "").strip():
        return ""
    wanted = [n for n in needles if (n or "").strip() and len(n.strip()) > 1]
    if len(wanted) < 2:
        return ""
    for para in re.split(r"\n+", text):
        blob = para.lower()
        hits = sum(1 for n in wanted if n.lower() in blob)
        if hits >= 2:
            clean = re.sub(r"\s+", " ", para).strip(" -*")
            if len(clean) >= 40:
                return clean[:600]
    return ""


def _node_kind(label: str) -> str:
    lower = (label or "").lower()
    if any(token in lower for token in ("client", "desktop ui", "mobile", "browser", "operator")):
        return "client"
    if "cdn" in lower:
        return "cdn"
    if "load balancer" in lower or lower in {"lb", "loadbalancer"}:
        return "lb"
    if "gateway" in lower:
        return "gateway"
    if "auth" in lower or "identity" in lower or "credential" in lower:
        return "auth"
    if "redis" in lower or "cache" in lower:
        return "cache"
    if "kafka" in lower or "bus" in lower or "pub/sub" in lower:
        return "bus"
    if "elastic" in lower or "search" in lower:
        return "search"
    if "sqlite" in lower or "postgres" in lower or "database" in lower or lower in {"db"}:
        return "store"
    if "s3" in lower or "object" in lower or "storage" in lower:
        return "object"
    if "http" in lower:
        return "http"
    if "editor" in lower:
        return "editor"
    if "collection" in lower or "history" in lower:
        return "store"
    if "error" in lower:
        return "errors"
    if "shell" in lower or lower in {"app", "application shell"}:
        return "app"
    if "ui" in lower:
        return "ui"
    if "remote" in lower or lower.endswith(" api"):
        return "remote"
    if "service" in lower:
        return "service"
    return "component"


def _relationship_template(
    start_kind: str,
    end_kind: str,
    start_label: str,
    end_label: str,
    proto: str,
) -> str:
    pair = (start_kind, end_kind)
    templates = {
        ("client", "lb"): (
            f"{start_label} opens user sessions{proto} to {end_label}. "
            "The balancer terminates the public connection and fans requests across gateway instances "
            "so a single edge box is not a hotspot."
        ),
        ("client", "gateway"): (
            f"{start_label} sends user commands{proto} to {end_label}. "
            "The gateway authenticates the caller, applies rate limits, and routes by path "
            "before any domain service sees the request."
        ),
        ("client", "cdn"): (
            f"{start_label} fetches cacheable bytes{proto} from {end_label}. "
            "This hop stays off origin so playback or static assets do not compete with API QPS."
        ),
        ("lb", "gateway"): (
            f"{start_label} forwards accepted connections{proto} to {end_label}. "
            "Health checks decide which gateway replica may receive the next request."
        ),
        ("gateway", "auth"): (
            f"{start_label} asks {end_label}{proto} to prove who the caller is and which actions are allowed. "
            "Auth stays a separate hop so identity policy is not copied into every domain service."
        ),
        ("gateway", "service"): (
            f"{start_label} routes an authenticated request{proto} to {end_label}. "
            "The service owns the operation and the data for that bounded context; the gateway does not."
        ),
        ("gateway", "search"): (
            f"{start_label} forwards discovery queries{proto} to {end_label}. "
            "Search stays off the system-of-record path so ranking work cannot stall writes."
        ),
        ("service", "store"): (
            f"{start_label} reads and writes durable state{proto} in {end_label}. "
            "This is the system of record for that service's objects; other services must not share the table."
        ),
        ("service", "cache"): (
            f"{start_label} loads hot keys{proto} from {end_label} before hitting durable storage. "
            "Cache misses fall back to the system of record; TTL and invalidation keep the window bounded."
        ),
        ("service", "bus"): (
            f"{start_label} publishes domain events{proto} onto {end_label}. "
            "Consumers react asynchronously so the write path does not wait on every collaborator."
        ),
        ("service", "service"): (
            f"{start_label} collaborates with {end_label}{proto}. "
            "The call is an explicit contract between two owners — not a shared database — "
            "so each side can fail and retry on its own terms."
        ),
        ("service", "object"): (
            f"{start_label} stores or fetches blobs{proto} in {end_label}. "
            "Object storage holds large payloads so the relational store keeps only metadata and keys."
        ),
        ("search", "search"): (
            f"{start_label} writes or queries the index{proto} in {end_label}. "
            "Indexing is eventually consistent with the system of record."
        ),
        ("ui", "app"): (
            f"{start_label} composes the operator surface through {end_label}. "
            "This is an in-process call, not a network hop: the shell owns lifecycle and wires child modules."
        ),
        ("app", "editor"): (
            f"{start_label} opens {end_label} to build the next request. "
            "The editor mutates headers, body, and target in memory until the operator dispatches."
        ),
        ("app", "store"): (
            f"{start_label} persists local records{proto} through {end_label}. "
            "The store is process-local so collections and history survive restarts without a remote database."
        ),
        ("app", "http"): (
            f"{start_label} hands a built request to {end_label}. "
            "The HTTP client owns timeouts, TLS, and status mapping so the shell never talks sockets directly."
        ),
        ("app", "errors"): (
            f"{start_label} surfaces failures through {end_label}. "
            "Network and server errors become operator-visible instead of failing silently."
        ),
        ("editor", "http"): (
            f"{start_label} dispatches the drafted call through {end_label}. "
            "The editor stays a pure builder; the client performs the I/O."
        ),
        ("http", "remote"): (
            f"{start_label} reaches {end_label}{proto}. "
            "This is the only path off the local process; when the client is offline the call cannot complete."
        ),
        ("store", "store"): (
            f"{start_label} shares the same embedded engine as {end_label}. "
            "Tables stay separate even though the file is one SQLite database."
        ),
    }
    if pair in templates:
        return templates[pair]
    if start_kind == "gateway" and end_kind == "store":
        return (
            f"{start_label} should not own durable data; if this line exists it is a constrained "
            f"lookup{proto} into {end_label} and should stay read-mostly."
        )
    direction = "calls" if not proto else f"calls{proto}"
    return (
        f"{start_label} {direction} {end_label}. "
        f"The arrow is a directed dependency: {start_label} needs {end_label} to complete its work, "
        "and the protocol on this hop is what the two sides actually speak."
    )


def _llm_relationship_catalog(diagram: str, spec: str, comms: str, prior: str) -> str:
    from architect_agent.graph.nodes.common import invoke_json

    edges = extract_diagram_edges(diagram)
    labels = {node_id: label for node_id, label in extract_diagram_components(diagram)}
    required = "\n".join(
        f"- {start} → {end} ({labels.get(start, start)} to {labels.get(end, end)}"
        + (f", arrow label {label}" if label else "")
        + ")"
        for start, end, label in edges
    ) or "(no edges parsed)"
    result = invoke_json(
        system=(
            "You write diagram relationship notes for the Architect agent.\n"
            "For EVERY arrow on the Mermaid diagram, explain the relationship in 2-4 sentences: "
            "what flows, in which direction, which protocol or call style, why this coupling exists, "
            "and what happens if the hop fails. ASCII only.\n"
            "Respond ONLY with JSON:\n"
            "{\n"
            '  "design_justification": string\n'
            "}\n"
            "design_justification MUST start with '## Diagram relationships' and include one "
            "### heading per arrow using the EXACT node ids, for example '### Client → GW'."
        ),
        user=(
            f"Living specification:\n\n{spec or '(empty)'}\n\n"
            f"Communication schemes:\n{comms or '(empty)'}\n\n"
            f"Mermaid:\n{diagram or '(none)'}\n\n"
            f"Required arrows (one ### heading each):\n{required}\n\n"
            f"Current catalog:\n{prior or '(none)'}\n"
        ),
    )
    return str(result.get("design_justification") or "").strip()
